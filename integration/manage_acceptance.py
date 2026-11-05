"""Recheck a stable saved configuration without applying or restarting anything."""
import json
import re
import threading
import time

from manage_common import ManagementError, operation_lock, read_bytes, require_idle, write_json
from manage_model import load_settings, settings_revision

CHECK = 'var/lib/paopaogw/admin/settings-check.json'


class Acceptance:
    def __init__(self, layout, system):
        self.layout, self.system = layout, system
        self.lock = threading.Lock()
        self.last = None
        self.attempted = 0

    def start(self):
        if not self.layout.live: return
        def tick():
            self.trigger(None, {})
            timer = threading.Timer(60, tick); timer.daemon = True; timer.start()
        timer = threading.Timer(3, tick); timer.daemon = True; timer.start()

    def trigger(self, revision, runtime):
        if not self.layout.live: return
        actual = runtime.get('data', runtime)
        identity = (revision, actual.get('boot_id'), actual.get('config_revision'))
        with self.lock:
            if (identity == self.last and time.monotonic() - self.attempted < 60) or getattr(self, 'running', False): return
            self.last, self.attempted, self.running = identity, time.monotonic(), True
        def work():
            try: self.check()
            except Exception: pass  # The next status sample retries; never affect a transaction.
            finally:
                with self.lock: self.running = False
        threading.Thread(target=work, daemon=True).start()

    def check(self):
        if not self.layout.live: return {'result': 'staged', 'checked_at': time.time(), 'checks': []}
        with operation_lock(self.layout):
            require_idle(self.layout)
            revision = settings_revision(self.layout)
            value = {'revision': revision, 'checked_at': time.time(), 'result': 'unconfirmed', 'checks': []}
            try:
                before = self.system.runtime()
                actual = before.get('data', before)
                value.update(boot_id=actual.get('boot_id'), config_revision=actual.get('config_revision'))
                if (not isinstance(actual.get('boot_id'), str) or not re.fullmatch(r'[0-9a-f-]{36}', actual['boot_id']) or
                        not isinstance(actual.get('config_revision'), str) or not re.fullmatch(r'[0-9a-f]{64}', actual['config_revision']) or
                        actual.get('configuration_state') != 'loaded' or actual.get('last_load_result') != 'success'):
                    raise ManagementError('CORE_NOT_RUNNING')
                value['checks'] = self.system.verify_current(load_settings(self.layout))
                after = self.system.runtime()
                after = after.get('data', after)
                if (settings_revision(self.layout) != revision or
                        any(actual.get(key) != after.get(key) for key in ('boot_id', 'config_revision'))):
                    raise ManagementError('STATE_CHANGED_DURING_CHECK')
                value['result'] = 'verified'
                from manage_operations import remember_loaded
                remember_loaded(self.layout, revision, after, checked_at=value['checked_at'])
            except Exception as error:
                code = str(error).split(':', 1)[0]
                allowed = {'CORE_NOT_RUNNING', 'RUNNING_CONFIG_MISMATCH', 'SERVICE_ENV_MISMATCH',
                           'SERVICE_STATE_MISMATCH', 'SOURCE_NOT_CONFIRMED', 'POLICY_NOT_CONFIRMED',
                           'API_READINESS_TIMEOUT', 'STATE_CHANGED_DURING_CHECK'}
                value['error'] = code if isinstance(error, ManagementError) and code in allowed else 'STATE_SAMPLE_UNAVAILABLE'
            write_json(self.layout, CHECK, value)
            return value


def latest_check(layout):
    try:
        value = json.loads(read_bytes(layout, CHECK, b'{}'))
        return {key: value[key] for key in ('revision', 'boot_id', 'config_revision', 'result', 'checked_at', 'checks', 'error') if key in value}
    except (OSError, ValueError): return {}
