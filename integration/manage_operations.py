"""Credential-free operation history shared by the independent management services."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from manage_common import read_bytes, write_json

DIRECTORY = 'var/lib/paopaogw/admin/operations/'
RECEIPT = 'var/lib/paopaogw/admin/loaded-settings.json'
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
FIELDS = {'id', 'kind', 'status', 'stage', 'created_at', 'started_at', 'finished_at',
          'error', 'backup', 'outcome', 'execution', 'phase', 'result', 'recovery'}
ACTIVE = {'queued', 'running', 'applying'}
_lock = threading.RLock()
_held = threading.local()


@contextmanager
def lifecycle_lock(layout):
    """Short cross-service lock; never hold this while executing a transaction."""
    with _lock:
        if getattr(_held, 'root', None) == str(layout.root):
            yield
            return
        path = layout.path('var/lib/paopaogw/admin/operations.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+b') as stream:
            if os.name != 'nt':
                import fcntl
                os.fchmod(stream.fileno(), 0o600)
                fcntl.flock(stream, fcntl.LOCK_EX)
            previous = getattr(_held, 'root', None)
            _held.root = str(layout.root)
            try: yield
            finally:
                _held.root = previous
                if os.name != 'nt': fcntl.flock(stream, fcntl.LOCK_UN)


def execution(service):
    value = {'service': service, 'pid': os.getpid(), 'invocation': os.environ.get('INVOCATION_ID', '')}
    try:
        value['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        value['start_ticks'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    except OSError: pass
    return value


def alive(owner):
    """None means unverifiable; PID alone never establishes ownership."""
    if not owner or not owner.get('boot_id') or not owner.get('start_ticks'): return None
    try:
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != owner['boot_id']: return False
        fields = Path('/proc/' + str(int(owner['pid'])) + '/stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == owner['start_ticks']
    except FileNotFoundError: return False
    except (OSError, ValueError, KeyError): return None


def public(value):
    """Compatibility statuses share an explicit phase/result/recovery contract."""
    safe = {key: value[key] for key in FIELDS if key in value and key != 'result'}
    status = value.get('status')
    interrupted = status == 'interrupted' or value.get('stage') == 'interrupted'
    safe['phase'] = 'interrupted' if interrupted else value.get('stage', 'queued') if status in ACTIVE else 'complete'
    safe['result'] = ('unknown' if interrupted else 'pending' if status in ACTIVE else
                      'failed' if status == 'failed' else value.get('outcome') or
                      {'applied': 'applied', 'unchanged': 'unchanged', 'succeeded': 'succeeded', 'complete': 'succeeded'}.get(status, 'unknown'))
    safe['recovery'] = 'check_required' if (interrupted or value.get('recovery') == 'check_required' or
                      value.get('error') in {'ROLLBACK_UNCONFIRMED', 'EXTERNAL_CONFIG_CONFLICT', 'RECOVERY_REQUIRED'}) else 'none'
    return safe


def interrupted(value):
    return {**value, 'status': 'interrupted', 'stage': 'interrupted',
            'finished_at': time.time(), 'error': 'INTERRUPTED_CHECK_RECOVERY'}


def service_state(unit):
    try:
        result = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState,InvocationID'],
                                capture_output=True, text=True, timeout=5, check=True)
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    except (OSError, subprocess.SubprocessError): return None


def record(layout, source, value):
    with lifecycle_lock(layout): return _record(layout, source, value)


def _record(layout, source, value):
    identifier = str(value.get('id', ''))
    if not IDENTIFIER.fullmatch(identifier) or source not in {'settings', 'subscription', 'mihomo'}:
        raise ValueError('INVALID_OPERATION_ID')
    safe = public(value)
    safe['source'] = source
    safe['updated_at'] = time.time()
    write_json(layout, DIRECTORY + source + '-' + identifier + '.json', safe)
    directory = layout.path(DIRECTORY.rstrip('/'))
    timestamps = []
    for path in directory.glob('*.json'):
        try: timestamps.append((path.stat().st_mtime, path))
        except FileNotFoundError: pass
    paths = [path for _, path in sorted(timestamps, reverse=True)]
    # Confirmed live work survives pruning. Dead or unbound legacy work is bounded.
    for path in paths[100:]:
        try:
            old = json.loads(path.read_bytes())
            if (old.get('status') not in ACTIVE or
                    (alive(old.get('execution')) is not True and time.time() - old.get('created_at', 0) >= 60)):
                path.unlink(missing_ok=True)
        except (OSError, ValueError): pass


def history(layout, limit=20):
    with lifecycle_lock(layout): return _history(layout, limit)


def _history(layout, limit):
    rows = []
    directory = layout.path(DIRECTORY.rstrip('/'))
    for path in directory.glob('*.json'):
        try:
            value = json.loads(path.read_bytes())
            if value.get('status') in ACTIVE and value.get('execution') and alive(value['execution']) is False:
                value = {**public(interrupted(value)), 'source': value['source'], 'updated_at': time.time()}
                write_json(layout, DIRECTORY + path.name, value)
            rows.append(value)
        except (OSError, ValueError): pass
    return sorted(rows, key=lambda row: row.get('updated_at', 0), reverse=True)[:limit]


class Operation(dict):
    """Persist every state transition before exposing it to a polling browser."""
    def __init__(self, layout, value, persist=True):
        self.layout = layout
        super().__init__(value)
        self.setdefault('created_at', time.time())
        self.setdefault('execution', execution('paopaogw-subscription.service'))
        self.setdefault('stage', 'queued' if self.get('status') == 'applying' else 'complete')
        if persist: self.flush()

    def flush(self):
        # A separate current record retains the safe, user-facing message for recovery.
        self.update_contract()
        write_json(self.layout, 'var/lib/paopaogw/admin/subscription-operation.json', dict(self))
        record(self.layout, 'subscription', self)

    def update_contract(self):
        for key, value in public(self).items():
            if key in {'phase', 'result', 'recovery'}: self[key] = value

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        if self.get('status') not in {'applying', 'running', 'queued'}:
            self.setdefault('finished_at', time.time())
            self['stage'] = 'interrupted' if self.get('status') == 'interrupted' else 'complete'
        self.flush()


def restore_operation(layout):
    raw = read_bytes(layout, 'var/lib/paopaogw/admin/subscription-operation.json')
    if not raw: return None
    value = json.loads(raw)
    was_interrupted = value.get('status') in ACTIVE
    if was_interrupted:
        value.update(status='interrupted', stage='interrupted', finished_at=time.time(),
                     error='INTERRUPTED_CHECK_RECOVERY',
                     message='操作期间服务重新启动，结果尚未确认；请检查运行状态和恢复提示，不要重复提交。')
    return Operation(layout, value, persist=was_interrupted)


MIHOMO_UNITS = ('paopaogw-maintenance.service',) + tuple('paopaogw-core-action@' + action + '.service'
                for action in ('check', 'update', 'rollback', 'install-local'))


def mihomo_current(layout, state):
    """Caller holds lifecycle_lock; never restart or replay abandoned work."""
    try: value = json.loads((state / 'operation.json').read_bytes())
    except (OSError, ValueError): return None
    if value.get('status') not in ACTIVE: return value
    owner = value.get('execution')
    live = alive(owner)
    if value.get('status') == 'queued':
        unit = service_state((owner or {}).get('service', ''))
        if time.time() - value.get('created_at', 0) < 60: return value
        if unit is None or unit.get('ActiveState') in {'active', 'activating', 'deactivating', 'reloading'}: return value
        live = False
    elif live is None:
        units = [service_state(unit) for unit in MIHOMO_UNITS]
        if any(unit is None or unit.get('ActiveState') in {'active', 'activating', 'deactivating', 'reloading'} for unit in units): return value
        live = False
    if live is False:
        value = interrupted(value)
        from mihomo_update import atomic_json
        atomic_json(state / 'operation.json', value)
        record(layout, 'mihomo', value)
    return value


def begin_mihomo(layout, state, action):
    from mihomo_update import atomic_json
    from manage_common import ManagementError
    service = 'paopaogw-maintenance.service' if action == 'scheduled' else 'paopaogw-core-action@' + action + '.service'
    owner = execution(service)
    with lifecycle_lock(layout):
        try: previous = json.loads((state / 'operation.json').read_bytes())
        except (OSError, ValueError): previous = {}
        # Only a new systemd invocation of the exact requested unit may claim its queued ID.
        unit = service_state(service) if owner['invocation'] else None
        claim = (previous.get('status') == 'queued' and previous.get('kind') == action and
                 previous.get('execution', {}).get('service') == service and owner['invocation'] and
                 unit and unit.get('InvocationID') == owner['invocation'] and
                 unit.get('ActiveState') in {'active', 'activating'} and
                 owner['invocation'] != previous.get('queued_invocation') and
                 time.time() - previous.get('created_at', 0) < 60)
        if not claim:
            previous = mihomo_current(layout, state) or {}
            if previous.get('status') in ACTIVE: raise ManagementError('MANAGEMENT_BUSY')
        value = {'id': previous['id'] if claim else 'mihomo_' + str(time.time_ns()), 'kind': action,
                 'action': action, 'status': 'running', 'stage': 'validating',
                 'created_at': previous['created_at'] if claim else time.time(),
                 'started_at': time.time(), 'execution': owner}
        atomic_json(state / 'operation.json', value)
        record(layout, 'mihomo', value)
        return value


def finish_mihomo(layout, state, value, **changes):
    from mihomo_update import atomic_json
    with lifecycle_lock(layout):
        value.update(changes)
        value.update(stage=changes.get('stage', 'complete'), finished_at=time.time(), completed_at=time.time())
        atomic_json(state / 'operation.json', value)
        record(layout, 'mihomo', value)


def remember_loaded(layout, revision, runtime, checked_at=None):
    actual = runtime.get('data', runtime)
    if actual.get('boot_id') and actual.get('config_revision'):
        write_json(layout, RECEIPT, {'revision': revision, 'boot_id': actual['boot_id'],
                                    'config_revision': actual['config_revision'],
                                    'checked_at': time.time() if checked_at is None else checked_at})


def summary(layout, revision, runtime, recovery=False):
    actual = runtime.get('data', runtime)
    state = 'unknown'
    raw = read_bytes(layout, RECEIPT)
    receipt = json.loads(raw) if raw else {}
    if recovery: state = 'recovery_required'
    elif receipt.get('revision') != revision and receipt: state = 'saved_pending'
    elif receipt and all(receipt.get(k) == actual.get(k) for k in ('boot_id', 'config_revision')):
        state = 'loaded'
    operations = history(layout)
    if state == 'unknown' and operations and operations[0].get('outcome') in {'core_inactive', 'staged'}:
        state = 'saved_pending'
    mihomo = actual.get('mihomo_configuration') or {}
    policy_state = 'none'
    policy = read_bytes(layout, 'etc/paopaogw/files/clash-policy.json')
    if policy:
        policy_state = 'saved_unconfirmed'
        key = read_bytes(layout, 'etc/paopaogw/revision-key')
        if key and mihomo.get('revision_scope') == 'mihomo_source_policy_effective':
            from clash_policy import protected_revision
            if protected_revision(json.loads(policy), key.strip()) == mihomo.get('policy_revision'):
                policy_state = 'loaded'
            elif state == 'loaded': state = 'saved_pending'
    if mihomo.get('status') == 'conflict': state = 'retained'
    if not actual.get('boot_id') or not actual.get('config_revision'):
        state = 'unknown'
    if recovery: state = 'recovery_required'
    from manage_acceptance import latest_check
    check = latest_check(layout)
    if check:
        check['current'] = check.get('revision') == revision and all(check.get(k) == actual.get(k) for k in ('boot_id', 'config_revision'))
    if (not recovery and check.get('revision') == revision and check.get('result') == 'unconfirmed'
            and check.get('checked_at', 0) >= receipt.get('checked_at', 0)):
        state = 'saved_pending' if check.get('error') != 'STATE_SAMPLE_UNAVAILABLE' else 'unknown'
        if mihomo.get('status') == 'conflict': state = 'retained'
    return {'state': state, 'policy_state': policy_state, 'verification': check,
            'recent': operations, 'latest': operations[0] if operations else None}
