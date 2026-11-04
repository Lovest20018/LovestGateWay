#!/usr/bin/env python3
"""Authenticated loopback settings service; asynchronous jobs share the CLI transactions."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from manage_common import Layout, ManagementError, read_bytes, write_json
from manage_config import ConfigManager
from manage_model import SCHEMA, SECRET_KEYS, load_settings, settings_revision
from manage_runtime import System
from manage_assets import import_asset
from manage_policy import PolicyManager

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


def effects(preview):
    changes = preview.get('changes', {})
    services = []
    if changes.get('api'): services.append('api')
    maintenance = changes.get('maintenance', {})
    if set(maintenance) & {'history_enabled', 'history_retention_days'}: services.append('history')
    if 'auto_update' in maintenance: services.append('maintenance_timer')
    if 'TZ' in changes.get('service', {}): services.extend(['subscription', 'history'])
    return {'core': preview['action'], 'services': list(dict.fromkeys(services)),
            'web_listen': changes.get('service', {}).get('PPGW_WEB_LISTEN'),
            'access_changed': bool(set(changes.get('ini', {})) & {'admin_cidr', 'clash_web_password'}),
            'source_reset': False}


class SettingsAdmin:
    def __init__(self, layout=None, system=None, policy_reader=None, start_job=None):
        self.layout = layout or Layout()
        self.system = system or System(self.layout, preserve_units={'paopaogw-settings'})
        self.manager = ConfigManager(self.layout, self.system)
        self.policy = PolicyManager(self.layout, self.system, policy_reader)
        self.lock = threading.Lock()
        self.active = None
        self.start_job = start_job or self.schedule
        self.reconcile_jobs()

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
                    value.update(status='failed', error='INTERRUPTED_CHECK_RECOVERY', finished_at=time.time())
                    write_json(self.layout, JOBS + path.name, value)
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
        value['mihomo_installed'] = self.layout.path('usr/local/lib/paopaogw/bin/clash').is_file()
        journal = self.manager.journal()
        value['recovery_required'] = bool(journal and journal.get('phase') not in {'complete', 'rolled_back'})
        value['latest_job'] = self.latest_job()
        try: value['runtime'] = self.system.runtime() if self.layout.live else {'status': 'staged'}
        except Exception: value['runtime'] = {'status': 'unavailable'}
        return value

    def preview(self, body):
        patch, revision, reset, restart = envelope(body)
        value = self.manager.apply(patch, revision, dry_run=True, reset_source=reset, restart=restart, **source_options(body))
        value['effects'] = effects(value)
        if reset:
            updates, _, _ = self.manager.plan(patch, reset)
            value['effects']['source_reset'] = any(raw is None for raw in updates.values())
        return value

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
        return value

    def submit(self, body):
        if (not isinstance(body, dict) or set(body) != {'id', 'kind', 'payload'}
                or not isinstance(body['id'], str) or not IDENTIFIER.fullmatch(body['id'])
                or body['kind'] not in {'config', 'policy', 'policy_reset', 'asset'}):
            raise ManagementError('INVALID_JOB_REQUEST')
        body = deepcopy(body)
        # A keyed digest detects accidental ID reuse without persisting request credentials.
        key = read_bytes(self.layout, 'etc/paopaogw/revision-key')
        if not key: raise ManagementError('REVISION_KEY_REQUIRED')
        digest = hmac.new(key, json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), 'sha256').hexdigest()
        identifier = body['id']; relative = JOBS + identifier + '.json'
        with self.lock:
            previous = read_bytes(self.layout, relative)
            if previous:
                if json.loads(previous).get('request_digest') != digest: raise ManagementError('JOB_ID_REUSED')
                return self.job(identifier)
            if self.active: raise ManagementError('MANAGEMENT_BUSY')
            if body['kind'] == 'config': envelope(body['payload'])
            value = {'id': identifier, 'kind': body['kind'], 'status': 'queued',
                     'created_at': time.time(), 'request_digest': digest}
            write_json(self.layout, relative, value)
            write_json(self.layout, JOBS + 'latest.json', {'id': identifier})
            self.active = identifier
            self.start_job(lambda: self.run(body))
            return self.job(identifier)

    def run(self, body):
        identifier = body['id']; relative = JOBS + identifier + '.json'
        value = json.loads(read_bytes(self.layout, relative))
        try:
            value.update(status='running', started_at=time.time())
            write_json(self.layout, relative, value)
            payload = body['payload']
            if body['kind'] == 'config':
                patch, revision, reset, restart = envelope(payload)
                result = self.manager.apply(patch, revision, reset_source=reset, restart=restart, **source_options(payload))
            elif body['kind'] == 'policy': result = self.policy.apply(payload)
            elif body['kind'] == 'policy_reset': result = self.policy.reset(payload)
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
            value.update(status='failed', error=public_error(error))
        finally:
            value['finished_at'] = time.time()
            write_json(self.layout, relative, value)
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
            elif self.path == '/api/policy': value = self.server.admin.policy.show()
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
            else: return self.respond(404, {'error': 'NOT_FOUND'})
            self.respond(200, value)
        except Exception as error:
            code = public_error(error)
            self.respond(409 if code in {'CONFIG_REVISION_CONFLICT', 'STALE_POLICY_SNAPSHOT', 'MANAGEMENT_BUSY', 'JOB_ID_REUSED'} else 400,
                         {'error': code})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8790), Handler)
    server.daemon_threads = True
    server.admin = SettingsAdmin()
    server.serve_forever()
