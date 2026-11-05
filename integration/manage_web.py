#!/usr/bin/env python3
"""Authenticated loopback settings service; asynchronous jobs share the CLI transactions."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from manage_common import Layout, ManagementError, read_bytes, write_json, operation_lock
from manage_config import ConfigManager
from manage_model import SCHEMA, SECRET_KEYS, load_settings, settings_revision, RevisionSample
from manage_runtime import System
from manage_assets import import_asset
from manage_policy import PolicyManager
from manage_preview import effects, complete_preview, source_details, native_summary
from manage_diagnostics import Diagnostics, public_code

JOBS = 'var/lib/paopaogw/admin/web-jobs/'
IDENTIFIER = re.compile(r'^[0-9a-f]{32}$')


def public_error(error):
    code = str(error).split(':', 1)[0]
    return code if isinstance(error, ManagementError) and re.fullmatch(r'[A-Z_]{3,64}', code) else 'OPERATION_FAILED'


def envelope(body):
    if (not isinstance(body, dict) or set(body) - {'base_revision', 'patch', 'reset_source', 'restart', 'source_ppsub'}
            or not isinstance(body.get('patch'), dict) or not re.fullmatch(r'[0-9a-f]{64}', str(body.get('base_revision', '')))
            or any(type(body[key]) is not bool for key in ('reset_source', 'restart') if key in body)):
        raise ManagementError('INVALID_CONFIG_REQUEST')
    if 'source_ppsub' in body and (not isinstance(body['source_ppsub'], str) or len(body['source_ppsub'].encode()) > 1024*1024):
        raise ManagementError('INVALID_CONFIG_REQUEST')
    return body['patch'], body['base_revision'], body.get('reset_source', False), body.get('restart', False)


def source_options(body):
    if 'source_ppsub' not in body: return {}
    if body.get('patch', {}).get('ini', {}).get('suburl') != 'ppsub@http://127.0.0.1/ppsub.json':
        raise ManagementError('INVALID_CONFIG_REQUEST')
    from unified_editor import validate_ppsub
    return {'ppsub_update': validate_ppsub(json.loads(body['source_ppsub']))}


class SettingsAdmin:
    def __init__(self, layout=None, system=None, policy_reader=None, start_job=None):
        self.layout = layout or Layout()
        self.system = system or System(self.layout, preserve_units={'paopaogw-settings'})
        self.manager = ConfigManager(self.layout, self.system)
        self.policy = PolicyManager(self.layout, self.system, policy_reader)
        self.diagnostics = Diagnostics(self.layout, self.system)
        self.lock = threading.Lock()
        self.active = None
        self.revision_sample = RevisionSample()
        from manage_acceptance import Acceptance
        self.acceptance = Acceptance(self.layout, self.system)
        self.start_job = start_job or self.schedule
        self.reconcile_jobs()
        self.acceptance.start()

    @staticmethod
    def schedule(callback):
        timer = threading.Timer(0.35, callback)
        timer.daemon = True
        timer.start()

    def reconcile_jobs(self):
        directory = self.layout.path(JOBS.rstrip('/'))
        if not directory.exists(): return
        for path in directory.glob('*.json'):
            if not IDENTIFIER.fullmatch(path.stem): continue
            try:
                value = json.loads(read_bytes(self.layout, JOBS + path.name))
                if value.get('status') in {'queued', 'running'}:
                    value.update(status='failed', stage='interrupted', error='INTERRUPTED_CHECK_RECOVERY', finished_at=time.time())
                    write_json(self.layout, JOBS + path.name, value)
                    from manage_operations import record
                    record(self.layout, 'settings', {**value, 'status': 'interrupted', 'stage': 'interrupted'})
            except (ValueError, OSError): continue

    def authenticated(self, authorization):
        try: expected = 'Bearer ' + self.layout.path('var/lib/paopaogw/core-token').read_text(encoding='ascii').strip()
        except (OSError, ValueError): return False
        return isinstance(authorization, str) and hmac.compare_digest(authorization.encode(), expected.encode())

    def observer_token(self, body):
        if body != {}: raise ManagementError('INVALID_CONFIG_REQUEST')
        token = load_settings(self.layout)['api']['read_token']
        if not token: raise ManagementError('OBSERVER_TOKEN_UNAVAILABLE')
        return {'token': token}

    def show(self):
        value = self.manager.show()
        value['schema'] = {ns: {key: {'type': spec[0], 'secret': key in SECRET_KEYS, 'constraints': spec[2:]}
                               for key, spec in fields.items()} for ns, fields in SCHEMA.items()}
        value['assets'] = []
        directory = self.layout.path('etc/paopaogw/files')
        if directory.exists():
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() in {'.yaml', '.yml', '.ovpn', '.pem', '.crt', '.json'} and path.is_file() and not path.is_symlink():
                    value['assets'].append(path.name)
        value['source'] = {'native_ppsub': self.layout.path('etc/paopaogw/files/ppsub.json').exists(),
                           'local_policy': self.layout.path('etc/paopaogw/files/clash-policy.json').exists()}
        value['source_details'] = source_details(self.layout)
        value['mihomo_installed'] = self.layout.path('usr/local/lib/paopaogw/bin/clash').is_file()
        journal = self.manager.journal()
        value['recovery_required'] = bool(journal and journal.get('phase') not in {'complete', 'rolled_back'})
        value['latest_job'] = self.latest_job()
        value.update(self.runtime_sample())
        from manage_operations import summary
        value['management'] = summary(self.layout, value['revision'], value['runtime'], value['recovery_required'])
        self.acceptance.trigger(value['revision'], value['runtime'])
        return value

    def runtime_sample(self):
        try:
            info = self.system.client().request('/sniffbox/info') if self.layout.live else {}
            return {'runtime': info.get('integration', {'status': 'staged' if not self.layout.live else 'unavailable'}),
                    'running_mode': info.get('mode')}
        except Exception: return {'runtime': {'status': 'unavailable'}, 'running_mode': None}

    def status(self):
        """No settings/schema/secrets or full config show on the polling path."""
        from manage_operations import summary, lifecycle_lock, mihomo_current
        from mihomo_update import STATE
        if self.layout.live:
            with lifecycle_lock(self.layout): mihomo_current(self.layout, STATE)
        value = self.runtime_sample()
        journal = self.manager.journal()
        value['recovery_required'] = bool(journal and journal.get('phase') not in {'complete', 'rolled_back'})
        try:
            revision = self.revision_sample.sample(self.layout)
            value['management'] = summary(self.layout, revision, value['runtime'], value['recovery_required'])
            self.acceptance.trigger(revision, value['runtime'])
        except Exception:
            from manage_operations import history
            rows = history(self.layout)
            value['management'] = {'state': 'unknown', 'policy_state': 'saved_unconfirmed', 'recent': rows,
                                   'latest': rows[0] if rows else None, 'error': 'STATE_SAMPLE_UNAVAILABLE'}
        value['source'] = {'local_policy': self.layout.path('etc/paopaogw/files/clash-policy.json').exists()}
        return value

    def preview(self, body):
        patch, revision, reset, restart = envelope(body)
        options = source_options(body)
        value = self.manager.apply(patch, revision, dry_run=True, reset_source=reset, restart=restart, **options)
        value['effects'] = effects(value)
        updates, changes, _ = self.manager.plan(patch, reset)
        if reset:
            value['effects']['source_reset'] = any(raw is None for raw in updates.values())
        values = load_settings(self.layout)['ini']
        values.update(changes.get('ini', {}))
        overrides = {}
        if updates.get('etc/paopaogw/files/clash-policy.json', False) is None: overrides['policy'] = None
        if updates.get('etc/paopaogw/files/ppsub.json', False) is None: overrides['native'] = False
        summary = None
        if 'ppsub_update' in options:
            previous = self.layout.path('etc/paopaogw/files/ppsub.json')
            old = json.loads(previous.read_bytes()) if previous.is_file() else None
            summary = native_summary(old, json.loads(options['ppsub_update']))
            overrides['native'] = True
        details = source_details(self.layout, values, **overrides)
        details['previous_kind'] = source_details(self.layout)['kind']
        return complete_preview(value, self.layout, self.system, summary, details)

    def latest_job(self):
        raw = read_bytes(self.layout, JOBS + 'latest.json')
        if not raw: return None
        return self.job(json.loads(raw)['id'])

    def job(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier): raise ManagementError('INVALID_JOB_ID')
        raw = read_bytes(self.layout, JOBS + identifier + '.json')
        if raw is None: raise ManagementError('JOB_NOT_FOUND')
        value = json.loads(raw)
        value.pop('request_digest', None)
        value.pop('request_key_version', None)
        from manage_operations import public
        contract = public(value)
        value.update({key: contract[key] for key in ('phase', 'recovery')})
        value['operation_result'] = contract['result']
        return value

    def request_key(self):
        """Restoring historical credentials must not break durable job retries."""
        relative = 'var/lib/paopaogw/admin/job-request-key'
        key = read_bytes(self.layout, relative)
        # Established keys are immutable. Idempotent retries must keep working
        # while their original transaction holds the management lock.
        if key is not None and len(key) == 32: return key
        with operation_lock(self.layout):
            key = read_bytes(self.layout, relative)
            if not key:
                if not read_bytes(self.layout, 'etc/paopaogw/revision-key'): raise ManagementError('REVISION_KEY_REQUIRED')
                key = secrets.token_bytes(32)
                # Incomplete first publication falls through the lock above;
                # exclusive creation refuses to replace externally created keys.
                path = self.layout.path(relative)
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise ManagementError('JOB_REQUEST_KEY_INVALID') from None
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(key); stream.flush(); os.fsync(stream.fileno())
            if len(key) != 32: raise ManagementError('JOB_REQUEST_KEY_INVALID')
            return key

    def submit(self, body):
        if (not isinstance(body, dict) or set(body) != {'id', 'kind', 'payload'}
                or not isinstance(body['id'], str) or not IDENTIFIER.fullmatch(body['id'])
                or not isinstance(body['kind'], str)
                or body['kind'] not in {'config', 'policy', 'policy_reset', 'asset', 'backup', 'restore', 'recover'}):
            raise ManagementError('INVALID_JOB_REQUEST')
        body = deepcopy(body)
        # A keyed digest detects accidental ID reuse without persisting request credentials.
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        identifier = body['id']; relative = JOBS + identifier + '.json'
        with self.lock:
            previous = read_bytes(self.layout, relative)
            if previous:
                value = json.loads(previous)
                key = self.request_key() if value.get('request_key_version') == 1 else read_bytes(self.layout, 'etc/paopaogw/revision-key')
                if not key: raise ManagementError('REVISION_KEY_REQUIRED')
                digest = hmac.new(key, encoded, 'sha256').hexdigest()
                if value.get('request_digest') != digest: raise ManagementError('JOB_ID_REUSED')
                return self.job(identifier)
            if self.active: raise ManagementError('MANAGEMENT_BUSY')
            if body['kind'] == 'config': envelope(body['payload'])
            if body['kind'] == 'backup' and body['payload'] != {}: raise ManagementError('INVALID_RECOVERY_REQUEST')
            if body['kind'] in {'restore', 'recover'}:
                with operation_lock(self.layout): self.diagnostics.validate_execution(body['kind'], body['payload'])
            digest = hmac.new(self.request_key(), encoded, 'sha256').hexdigest()
            value = {'id': identifier, 'kind': body['kind'], 'status': 'queued',
                     'stage': 'queued', 'created_at': time.time(), 'request_digest': digest, 'request_key_version': 1}
            from manage_operations import execution
            value['execution'] = execution('paopaogw-settings.service')
            write_json(self.layout, relative, value)
            from manage_operations import record
            record(self.layout, 'settings', value)
            write_json(self.layout, JOBS + 'latest.json', {'id': identifier})
            self.active = identifier
            self.start_job(lambda: self.run(body))
            return self.job(identifier)

    def run(self, body):
        identifier = body['id']; relative = JOBS + identifier + '.json'
        value = json.loads(read_bytes(self.layout, relative))
        from manage_operations import record
        def progress(stage):
            value['stage'] = stage
            write_json(self.layout, relative, value)
            record(self.layout, 'settings', value)
        try:
            value.update(status='running', started_at=time.time())
            progress('validating')
            self.manager.progress = progress
            self.diagnostics.progress = progress
            payload = body['payload']
            if body['kind'] == 'config':
                patch, revision, reset, restart = envelope(payload)
                result = self.manager.apply(patch, revision, reset_source=reset, restart=restart, **source_options(payload))
            elif body['kind'] == 'policy': result = self.policy.apply(payload)
            elif body['kind'] == 'policy_reset': result = self.policy.reset(payload)
            elif body['kind'] == 'backup': result = self.diagnostics.execute_backup(payload)
            elif body['kind'] == 'restore': result = self.diagnostics.execute_restore(payload)
            elif body['kind'] == 'recover': result = self.diagnostics.execute_recover(payload)
            else:
                if (not isinstance(payload, dict) or set(payload) != {'type', 'name', 'content', 'overwrite'}
                        or not isinstance(payload['content'], str) or len(payload['content'].encode()) > 1024*1024
                        or type(payload['overwrite']) is not bool):
                    raise ManagementError('INVALID_ASSET_REQUEST')
                with tempfile.TemporaryDirectory(prefix='lovest-asset-') as temporary:
                    source = Path(temporary) / 'upload'
                    source.write_text(payload['content'], encoding='utf-8'); source.chmod(0o600)
                    result = import_asset(self.layout, source, payload['type'], payload['name'], payload['overwrite'])
            value.update(status='succeeded', result=result)
        except Exception as error:
            code = public_error(error)
            value.update(status='failed', error=public_code(code) if body['kind'] in {'backup', 'restore', 'recover'} else code)
        finally:
            value['finished_at'] = time.time()
            value['stage'] = 'complete'
            if value.get('result'):
                result = value['result']
                value['outcome'] = (result.get('runtime') or {}).get('reason') or result.get('status')
                if result.get('backup'): value['backup'] = result['backup']
            write_json(self.layout, relative, value)
            record(self.layout, 'settings', value)
            self.manager.progress = lambda stage: None
            self.diagnostics.progress = lambda stage: None
            with self.lock: self.active = None


class Handler(BaseHTTPRequestHandler):
    server_version = 'LovestGateWay-Settings/1'
    def setup(self):
        super().setup(); self.connection.settimeout(10)
    def log_message(self, *args): pass
    def respond(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        for key, item in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
                          ('Content-Length', str(len(raw))), ('X-Content-Type-Options', 'nosniff')]:
            self.send_header(key, item)
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if not self.server.admin.authenticated(self.headers.get('Authorization')):
            return self.respond(401, {'error': 'LOGIN_REQUIRED'})
        try:
            if self.path == '/api/config': value = self.server.admin.show()
            elif self.path == '/api/status': value = self.server.admin.status()
            elif self.path == '/api/policy': value = self.server.admin.policy.show()
            elif self.path == '/api/diagnostics': value = self.server.admin.diagnostics.report()
            elif self.path == '/api/backups': value = self.server.admin.diagnostics.backups({})
            elif self.path.startswith('/api/jobs/'): value = self.server.admin.job(self.path[len('/api/jobs/'):])
            else: return self.respond(404, {'error': 'NOT_FOUND'})
            self.respond(200, value)
        except Exception as error: self.respond(409 if isinstance(error, ManagementError) else 503, {'error': public_error(error)})
    def do_POST(self):
        try:
            if (self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Length', [])) != 1): raise ManagementError('INVALID_REQUEST')
            length = int(self.headers['Content-Length'])
            if not 0 < length <= 1100000: raise ManagementError('INVALID_REQUEST')
            raw = self.rfile.read(length)
            if len(raw) != length: raise ManagementError('INVALID_REQUEST')
            # Consume the bounded body before rejecting: closing a socket with
            # unread input can reset it before the caller receives the 401/403.
            if not self.server.admin.authenticated(self.headers.get('Authorization')):
                return self.respond(401, {'error': 'LOGIN_REQUIRED'})
            if self.headers.get('Origin') not in {'http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')}:
                return self.respond(403, {'error': 'ORIGIN_REJECTED'})
            body = json.loads(raw)
            if self.path == '/api/preview': value = self.server.admin.preview(body)
            elif self.path == '/api/policy/preview': value = self.server.admin.policy.preview(body)
            elif self.path == '/api/apply': return self.respond(202, self.server.admin.submit(body))
            elif self.path == '/api/observer-token': value = self.server.admin.observer_token(body)
            elif self.path == '/api/recheck':
                if body != {}: raise ManagementError('INVALID_CONFIG_REQUEST')
                value = self.server.admin.acceptance.check()
            elif self.path == '/api/backups/list': value = self.server.admin.diagnostics.backups(body)
            elif self.path == '/api/restore/preview': value = self.server.admin.diagnostics.preview_restore(body)
            elif self.path == '/api/recover/preview': value = self.server.admin.diagnostics.preview_recover(body)
            else: return self.respond(404, {'error': 'NOT_FOUND'})
            self.respond(200, value)
        except Exception as error:
            code = public_error(error)
            self.respond(409 if code in {'CONFIG_REVISION_CONFLICT', 'STALE_POLICY_SNAPSHOT', 'MANAGEMENT_BUSY', 'JOB_ID_REUSED',
                                        'PREVIEW_EXPIRED', 'STALE_RECOVERY_PREVIEW', 'EXTERNAL_CONFIG_CONFLICT', 'RECOVERY_REQUIRED'} else 400,
                         {'error': code})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8790), Handler)
    server.daemon_threads = True
    server.admin = SettingsAdmin()
    server.serve_forever()
