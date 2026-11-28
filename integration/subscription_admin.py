#!/usr/bin/env python3
"""Loopback PPsub save/apply helper for the authenticated native panel."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
from functools import wraps
import logging
from manage_common import Layout, ManagementError, require_idle, operation_lock as management_lock
from manage_model import parse_assignments, settings_revision
from urllib.parse import urlsplit
from gateway_api import CoreClient, ApiError

CONFIG = Path('/etc/paopaogw/files/ppgw.ini')
FILES = CONFIG.parent
STATE = Path('/var/lib/paopaogw/admin')
CODE = Path(__file__).resolve().parent
CORE_TOKEN = Path('/var/lib/paopaogw/core-token')
POLICY = FILES / 'clash-policy.json'
CLASH_YAML_EDITING_MESSAGE = '本版本仅支持通过 LovestSub 编辑器维护配置，请先在“配置来源”切换到 LovestSub。'


def managed_worker(function):
    @wraps(function)
    def run(self, *args, **kwargs):
        try:
            # Tests may place CONFIG in an isolated root; production uses /.
            layout = Layout(CONFIG.parents[3] if CONFIG.as_posix().endswith('/etc/paopaogw/files/ppgw.ini') else STATE.parent)
            with management_lock(layout):
                require_idle(layout)
                return function(self, *args, **kwargs)
        except ManagementError:
            with self.lock:
                if self.operation: self.operation.update(status='failed', message='管理操作正在进行或需要恢复，请稍后检查状态再试。')
    return run


def management_layout():
    return Layout(CONFIG.parents[3] if CONFIG.as_posix().endswith('/etc/paopaogw/files/ppgw.ini') else STATE.parent)


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def config_values():
    exported = subprocess.run([str(CODE / 'bin/sniffbox'), 'ppgw', '-ini-export', str(CONFIG)],
                              capture_output=True, text=True, check=True, timeout=5)
    return dict(value.split('=', 1) for value in shlex.split(exported.stdout) if '=' in value)


def native_ppsub_source(values):
    return values.get('mode') == 'suburl' and (
        values.get('suburl', '').startswith('ppsub@') or (FILES / 'ppsub.json').is_file())


def update_config(raw: bytes, changes: dict) -> bytes:
    text = raw.decode('utf-8')
    if not text.startswith('#paopao-gateway'):
        raise ValueError('INVALID_CONFIG')
    lines = text.replace('\r\n', '\n').splitlines()
    output = [line for line in lines if not any(re.match(r'^\s*' + re.escape(k) + r'\s*=', line) for k in changes)]
    for key, value in changes.items():
        if '\n' in value or '\r' in value or '"' in value or '\x00' in value:
            raise ValueError('INVALID_VALUE')
        output.append(f'{key}="{value}"')
    return ('\n'.join(output) + '\n').encode()


def validate_subscription(body):
    if not isinstance(body, dict) or set(body) - {'kind', 'value', 'base_revision', 'operation_id'}:
        raise ValueError('INVALID_REQUEST')
    kind, value = body.get('kind'), body.get('value')
    if kind not in {'url', 'yaml', 'ppsub'} or not isinstance(value, str):
        raise ValueError('INVALID_SUBSCRIPTION')
    if kind == 'url':
        if not 1 <= len(value) <= 4096 or any(ord(c) < 33 for c in value) or '"' in value:
            raise ValueError('INVALID_URL')
        target = urlsplit(value)
        if target.scheme not in {'http', 'https'} or not target.hostname or target.username or target.password or target.fragment:
            raise ValueError('INVALID_URL')
        _ = target.port
        return {'mode': 'suburl', 'suburl': value, 'fast_node': 'no'}, None
    if len(value.encode()) > 1024 * 1024:
        raise ValueError('FILE_TOO_LARGE')
    if kind == 'yaml':
        if not re.search(r'^proxies\s*:', value, re.MULTILINE):
            raise ValueError('YAML_PROXIES_REQUIRED')
        return {'mode': 'yaml', 'yamlfile': 'custom.yaml', 'fast_node': 'no'}, ('custom.yaml', value.encode())
    parsed = json.loads(value)
    from unified_editor import validate_ppsub
    validate_ppsub(parsed)
    if (not isinstance(parsed, dict) or not isinstance(parsed.get('subs'), list)
            or not parsed.get('subs') or not parsed.get('exported_at')):
        raise ValueError('INVALID_PPSUB')
    return {'mode': 'suburl', 'suburl': 'ppsub@http://127.0.0.1/ppsub.json', 'fast_node': 'no'}, ('ppsub.json', value.encode())


def atomic_write(path, raw):
    pending = path.with_name(path.name + '.admin-new')
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


class Admin:
    @property
    def operation(self): return self._operation

    @operation.setter
    def operation(self, value):
        from manage_operations import Operation
        self._operation = Operation(self._operation_layout or management_layout(), value) if value is not None else None

    def __init__(self, layout=None):
        self.lock = threading.Lock()
        self._operation_layout = layout
        from manage_operations import restore_operation
        self._operation = restore_operation(layout or management_layout())
        self.core = CoreClient(CORE_TOKEN)

    def authenticated(self, authorization):
        expected = 'Bearer ' + CORE_TOKEN.read_text().strip()
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization.encode('utf-8'), expected.encode('ascii'))

    def maintenance(self):
        from mihomo_update import Updater, STATE as update_state
        def read(path, default):
            try: return json.loads(path.read_bytes())
            except (OSError, ValueError): return default
        from manage_operations import lifecycle_lock, mihomo_current, public
        with lifecycle_lock(self._operation_layout or management_layout()):
            operation = mihomo_current(self._operation_layout or management_layout(), update_state)
        if operation: operation = {**operation, **public(operation)}
        if operation and operation.get('status') == 'queued':
            operation = {**operation, 'status': 'running', 'phase': 'queued'}
        elif operation and operation.get('status') == 'interrupted':
            operation = {**operation, 'status': 'failed', 'phase': 'interrupted', 'result': 'unknown',
                         'recovery': 'check_required',
                         'message': '维护执行进程已中断，结果尚未确认；请检查当前版本与系统服务，不会自动重试或重放。'}
        return {'core': Updater().status(), 'operation': operation,
                'policy': read(STATE / 'policy-status.json', {'status': 'not_applied'}),
                'has_local_policy': POLICY.exists(), 'timezone': os.environ.get('TZ', 'Asia/Shanghai'),
                'log_source': 'instance_controller', 'cloudflared': 'removed'}

    def core_idle(self):
        if self.operation and self.operation.get('status') == 'applying': raise ValueError('MANAGEMENT_BUSY')
        for unit in ('paopaogw-maintenance', 'paopaogw-core-action@check', 'paopaogw-core-action@update',
                     'paopaogw-core-action@rollback', 'paopaogw-core-action@install-local'):
            state = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'],
                                   capture_output=True, text=True, timeout=5)
            if state.stdout.strip() in {'active', 'activating', 'deactivating', 'reloading'}: raise ValueError('MANAGEMENT_BUSY')
        from mihomo_update import STATE as update_state
        from manage_operations import lifecycle_lock, mihomo_current, ACTIVE
        with lifecycle_lock(self._operation_layout or management_layout()):
            pending = mihomo_current(self._operation_layout or management_layout(), update_state)
            if pending and pending.get('status') in ACTIVE: raise ValueError('MANAGEMENT_BUSY')

    def start_core_action(self, action):
        from mihomo_update import atomic_json, STATE as update_state
        from manage_operations import record, lifecycle_lock, mihomo_current, service_state, ACTIVE
        layout = self._operation_layout or management_layout()
        unit = 'paopaogw-core-action@' + action + '.service'
        with lifecycle_lock(layout):
            pending = mihomo_current(layout, update_state)
            if pending and pending.get('status') in ACTIVE: raise ValueError('MANAGEMENT_BUSY')
            state = service_state(unit)
            if state is None or state.get('ActiveState') in {'active', 'activating', 'deactivating', 'reloading'}:
                raise ValueError('MANAGEMENT_BUSY')
            value = {'id': 'mihomo_' + str(time.time_ns()), 'kind': action, 'status': 'queued',
                     'stage': 'queued', 'action': action, 'created_at': time.time(),
                     'execution': {'service': unit}, 'queued_invocation': state.get('InvocationID', '')}
            atomic_json(update_state / 'operation.json', value)
            record(layout, 'mihomo', value)
            try:
                subprocess.run(['systemctl', 'start', '--no-block', unit], check=True, timeout=5)
            except Exception:
                value.update(status='failed', stage='complete', error='CORE_OPERATION_FAILED',
                             message='维护任务未能启动，请检查系统服务。', finished_at=time.time())
                atomic_json(update_state / 'operation.json', value)
                record(layout, 'mihomo', value)
                raise
        return {**value, 'status': 'running'}

    def core_action(self, action):
        if action not in {'check', 'update', 'rollback'}: raise ValueError('INVALID_OPERATION')
        with self.lock:
            self.core_idle()
            return self.start_core_action(action)

    def core_upload(self, body):
        from mihomo_upload import UploadStore
        if not isinstance(body, dict): raise ValueError('INVALID_CORE_UPLOAD')
        operation = body.get('operation')
        store = UploadStore()
        with self.lock:
            # Chunks write private staging under the shared filesystem lock.
            # Begin/commit still verify systemd state; avoid five subprocesses per 16 KiB.
            if operation != 'chunk': self.core_idle()
            if operation == 'begin': return store.begin(body)
            if operation == 'chunk': return store.chunk(body)
            if operation in {'commit', 'cancel'} and set(body) == {'operation', 'id'}:
                if operation == 'cancel': return store.cancel(body['id'])
                store.commit(body['id'])
                return self.start_core_action('install-local')
        raise ValueError('INVALID_CORE_UPLOAD')

    def reset_policy(self, body):
        if not isinstance(body, dict) or set(body) != {'base_revision'}:
            raise ValueError('INVALID_REQUEST')
        with self.lock:
            if self.operation and self.operation.get('status') == 'applying':
                raise ValueError('BUSY')
            if revision(CONFIG.read_bytes()) != body['base_revision']:
                raise ValueError('CONFIG_CHANGED')
            if not POLICY.is_file(): return {'status': 'unchanged'}
            old = POLICY.read_bytes()
            self.operation = {'id': 'policy-reset-' + str(int(time.time())), 'status': 'applying', 'message': '正在恢复订阅原始分流策略…'}
            thread = threading.Timer(.5, self.reset_worker, args=(old, CONFIG.read_bytes()))
            thread.daemon = True; thread.start()
            return dict(self.operation)

    @managed_worker
    def reset_worker(self, old, original=None):
        written = False
        try:
            if (original is not None and CONFIG.read_bytes() != original) or not POLICY.exists() or POLICY.read_bytes() != old:
                raise ManagementError('CONFIG_CHANGED')
            STATE.mkdir(parents=True, exist_ok=True)
            atomic_write(STATE / 'clash-policy.before-reset.json', old)
            POLICY.unlink()
            written = True
            subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if self.core.request('/sniffbox/info').get('clash_version'):
                        with self.lock: self.operation.update(status='applied', message='已恢复订阅原始策略。')
                        return
                except ApiError: pass
                time.sleep(1)
            raise ValueError('NOT_READY')
        except Exception:
            try:
                if written and not POLICY.exists():
                    atomic_write(POLICY, old)
                    subprocess.run(['systemctl', 'restart', 'paopaogw'], timeout=30,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            with self.lock: self.operation.update(status='failed', recovery='check_required',
                message='恢复原始策略未完成；已尝试还原保存文件，运行结果尚未确认，请检查系统服务。')

    def node_cache(self):
        try:
            from manage_subscription_schedule import provider_status
            layout = management_layout()
            values = parse_assignments(CONFIG.read_bytes())
            if not native_ppsub_source(values): return {'subscriptions': []}
            settings = {'ini': {'subtime': values.get('subtime', '1d'), 'subcron': values.get('subcron', '')}}
            return {'subscriptions': provider_status(layout, settings)}
        except (OSError, ValueError): return {'subscriptions': []}

    def refresh_nodes(self, body):
        if (set(body) - {'kind', 'base_revision', 'settings_revision', 'operation_id', 'providers'}
                or not {'kind', 'base_revision', 'settings_revision', 'operation_id'} <= set(body)
                or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(body.get('operation_id', '')))):
            raise ManagementError('INVALID_EDITOR_REQUEST')
        if not native_ppsub_source(config_values()): raise ManagementError('SOURCE_CHANGE_REQUIRES_SWITCH')
        if 'providers' in body:
            from manage_subscription_schedule import providers
            names = body['providers']
            if (not isinstance(names, list) or not names or len(names) > 500
                    or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names)
                    or set(names) - {row['name'] for row in providers(management_layout())}):
                raise ManagementError('INVALID_EDITOR_REQUEST')
        with self.lock:
            if self.operation and self.operation.get('id') == body['operation_id']: return dict(self.operation)
            if self.operation and self.operation.get('status') == 'applying': raise ManagementError('MANAGEMENT_BUSY')
            if (revision(CONFIG.read_bytes()) != body['base_revision']
                    or settings_revision(management_layout()) != body['settings_revision']):
                raise ManagementError('CONFIG_REVISION_CONFLICT')
            self.operation = {'id': body['operation_id'], 'status': 'applying', 'kind': 'refresh_nodes',
                              'message': '正在手动获取订阅节点；获取失败的提供商继续使用已保存节点…'}
            task = threading.Timer(.5, self.refresh_nodes_worker, args=(dict(body),))
            task.daemon = True; task.start()
            return dict(self.operation)

    @managed_worker
    def refresh_nodes_worker(self, body, *, trigger='manual'):
        from manage_model import settings_revision
        from manage_runtime import System
        try:
            layout = management_layout()
            if (revision(CONFIG.read_bytes()) != body['base_revision']
                    or settings_revision(layout) != body['settings_revision']):
                raise ManagementError('CONFIG_REVISION_CONFLICT')
            from manage_subscription import refresh_nodes
            def progress(stage):
                with self.lock: self.operation.update(stage=stage)
            rows = refresh_nodes(layout, System(layout), body.get('providers'), progress=progress, trigger=trigger)
            selected = set(body.get('providers', [row['name'] for row in rows]))
            rows = [row for row in rows if row['name'] in selected]
            failed = [row['name'] for row in rows if row.get('update_failed') and row.get('nodes', 0) > 0]
            missing = [row['name'] for row in rows if not row.get('nodes')]
            unchanged = all(row.get('result') == 'unchanged' for row in rows)
            if failed:
                message = '更新完成；以下提供商获取失败，已保留原节点：' + '、'.join(failed)
            elif unchanged:
                message = '订阅内容未变，无需重新加载。'
            elif all(row.get('core_action') == 'none' for row in rows):
                message = '订阅缓存已更新，运行配置相同，网关继续转发。'
            else:
                message = '订阅节点已更新并加载，重启后继续使用已保存节点。'
            if missing: message += '；以下提供商尚无可用节点：' + '、'.join(missing)
            with self.lock: self.operation.update(status='unchanged' if unchanged else 'applied', message=message, retained_subscriptions=failed,
                                                  outcome='retained' if failed else 'unchanged' if unchanged else 'updated')
        except Exception as error:
            code = str(error).split(':', 1)[0]
            if code == 'SUBSCRIPTION_ROLLBACK_UNCONFIRMED':
                with self.lock: self.operation.update(status='failed', error=code, recovery='check_required',
                    message='订阅更新失败，已尝试还原原配置，运行恢复尚未确认；请检查系统服务和恢复提示。')
            else:
                with self.lock: self.operation.update(status='failed', message='订阅更新未完成，已保存节点和原运行配置继续保留。请检查订阅是否已开启，再手动重试。')

    def scheduled_tick(self):
        from manage_model import load_settings, settings_revision
        from manage_subscription_schedule import due_providers, source_path
        layout = management_layout()
        with management_lock(layout):
            require_idle(layout)
            settings = load_settings(layout)
            if (settings['ini']['mode'] != 'suburl' or not source_path(layout).is_file()
                    or not settings['ini']['suburl'].startswith('ppsub@')):
                return
            with self.lock:
                if self.operation and self.operation.get('status') == 'applying': return
                names = due_providers(layout, settings)
                if not names: return
                body = {'providers': names, 'base_revision': revision(CONFIG.read_bytes()),
                        'settings_revision': settings_revision(layout)}
                self.operation = {'id': 'scheduled_' + str(time.time_ns()), 'kind': 'refresh_nodes',
                                  'status': 'applying', 'message': '正在按机场计划更新订阅节点…'}
            self.refresh_nodes_worker(body, trigger='scheduled')

    def schedule_loop(self):
        while True:
            try: self.scheduled_tick()
            except Exception: pass  # Retain the current nodes and retry the scheduler later.
            time.sleep(30)

    def current_config(self):
        raw = CONFIG.read_bytes()
        layout = management_layout()
        full_revision = settings_revision(layout)
        # Read the Rust parser's quoted assignments without executing a shell.
        values = config_values()
        suburl = values.get('suburl', '')
        local = FILES / 'ppsub.json'
        runtime = layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json')
        from manage_preview import source_details
        details = source_details(layout, values)
        # The editable source URL is retained for the existing source editor;
        # preview explanations use only the safe kind/section fields.
        details['url'] = suburl
        native = native_ppsub_source(values)
        if native and (local.is_file() or runtime.is_file()):
            data = json.loads((local if local.is_file() else runtime).read_bytes())
            source, notice = 'ppsub', '已载入当前 LovestSub 配置，编辑完成后可直接保存并应用。'
        else:
            data = {'version': 'PaoPaoGateWay 3.1', 'exported_at': 'new-config',
                    'subs': [], 'node-groups': [], 'rules': []}
            source = 'empty'
            if native:
                notice = '尚未读取到 LovestSub 配置，请在“配置来源”导入 LovestSub JSON。'
            elif values.get('mode') in {'yaml', 'suburl'}:
                notice = '当前使用完整 Clash 配置，本版本不再支持在线编辑完整 Clash YAML；请先在“配置来源”切换到 LovestSub。'
            else:
                notice = '当前不是 LovestSub 模式，请先在“配置来源”切换到 LovestSub。'
        return {'config': data, 'source': source, 'notice': notice, 'node_cache': self.node_cache(),
                'base_revision': revision(raw), 'edit_kind': 'ppsub', 'write_allowed': source != 'empty',
                'settings_revision': full_revision, 'source_details': details}

    def status(self):
        raw = CONFIG.read_bytes()
        values = parse_assignments(raw)
        try:
            info = self.core.request('/sniffbox/info')
            running_mode = info.get('mode')
            ready = bool(running_mode and (running_mode not in {'yaml', 'suburl'} or info.get('clash_version')))
        except ApiError:
            ready, running_mode = False, None
        with self.lock:
            operation = dict(self.operation) if self.operation else None
        return {'base_revision': revision(raw), 'saved_mode': values.get('mode') or 'unknown',
                'running_mode': running_mode, 'ready': ready,
                'mihomo_installed': (CODE / 'bin/clash').is_file(), 'operation': operation, 'node_cache': self.node_cache(),
                'ppsub_write_allowed': ready and native_ppsub_source(values),
                'edit_kind': 'ppsub'}

    def apply(self, body):
        if isinstance(body, dict) and body.get('kind') == 'refresh_nodes': return self.refresh_nodes(body)
        if isinstance(body, dict) and body.get('kind') == 'editor':
            from unified_editor import handle
            return handle(self, body)
        if isinstance(body, dict) and body.get('kind') in {'clash_patch', 'export_yaml'}:
            raise ValueError('CLASH_YAML_EDITING_UNSUPPORTED')
        changes, upload = validate_subscription(body)
        if body.get('kind') == 'ppsub':
            values = config_values()
            if values.get('mode') in {'yaml', 'suburl'} and not native_ppsub_source(values):
                raise ValueError('CLASH_PPSUB_NOT_EQUIVALENT')
        if not (CODE / 'bin/clash').is_file():
            raise ValueError('MIHOMO_REQUIRED')
        operation_id = body.get('operation_id')
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', operation_id):
            raise ValueError('INVALID_OPERATION_ID')
        with self.lock:
            if self.operation and self.operation['id'] == operation_id:
                return dict(self.operation)
            if self.operation and self.operation['status'] == 'applying':
                raise ValueError('BUSY')
            original = CONFIG.read_bytes()
            if body.get('base_revision') != revision(original):
                raise ValueError('CONFIG_CHANGED')
            candidate = update_config(original, changes)
            self.operation = {'id': operation_id, 'status': 'applying', 'message': '正在保存并加载订阅…'}
            # Allow the native panel's 202 response to finish before restarting its core.
            thread = threading.Timer(0.5, self.worker, args=(original, candidate, upload))
            thread.daemon = True
            thread.start()
            return dict(self.operation)

    @managed_worker
    def worker(self, original, candidate, upload):
        old_files = {}
        written = False
        try:
            if CONFIG.read_bytes() != original: raise ManagementError('CONFIG_CHANGED')
            STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write(STATE / 'ppgw.before-subscription.ini', original)
            # A stale local PPsub must not override a subsequently imported URL/YAML.
            for name in ('ppsub.json', 'custom.yaml'):
                path = FILES / name
                old_files[name] = path.read_bytes() if path.exists() else None
            for name in ('ppsub.json', 'custom.yaml'):
                if upload and upload[0] == name:
                    atomic_write(FILES / name, upload[1])
                elif name == 'ppsub.json' and (FILES / name).exists():
                    (FILES / name).unlink()
            atomic_write(CONFIG, candidate)
            written = True
            subprocess.run(['systemctl', 'restart', 'paopaogw.service'], check=True, timeout=25,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    info = self.core.request('/sniffbox/info')
                    if info.get('mode') in {'yaml', 'suburl'} and info.get('clash_version'):
                        with self.lock:
                            self.operation.update(status='applied', message='LovestSub 配置已加载，可以到代理页选择节点。')
                        return
                except ApiError:
                    pass
                time.sleep(1)
            raise RuntimeError('NOT_READY')
        except Exception:
            # Restore only when the candidate is still ours, preserving later external edits.
            restored = False
            try:
                if written and CONFIG.read_bytes() == candidate:
                    atomic_write(CONFIG, original)
                    for name, content in old_files.items():
                        if content is None:
                            (FILES / name).unlink(missing_ok=True)
                        else:
                            atomic_write(FILES / name, content)
                    restored = True
                    try:
                        subprocess.run(['systemctl', 'restart', 'paopaogw.service'], timeout=25,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
            except Exception:
                restored = False
            finally:
                with self.lock:
                    self.operation.update(status='failed', recovery='check_required', message=('订阅未能加载，原保存文件已还原；运行结果尚未确认，请检查链接、文件和系统服务。'
                        if restored else '订阅未能加载，检测到外部配置变化，请检查运行状态。'))


class Handler(BaseHTTPRequestHandler):
    server_version = 'LovestGateWay-Subscription/1'
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
    def log_message(self, *args):
        pass
    def respond(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        for key, value in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
                           ('Content-Length', str(len(raw))), ('X-Content-Type-Options', 'nosniff')]:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if not self.server.admin.authenticated(self.headers.get('Authorization')):
            return self.respond(401, {'error': 'LOGIN_REQUIRED'})
        try:
            if self.path == '/api/status':
                self.respond(200, self.server.admin.status())
            elif self.path == '/api/providers':
                self.respond(200, self.server.admin.node_cache())
            elif self.path == '/api/config':
                self.respond(200, self.server.admin.current_config())
            elif self.path == '/api/maintenance':
                self.respond(200, self.server.admin.maintenance())
            else:
                self.respond(404, {'error': 'NOT_FOUND'})
        except Exception:
            self.respond(503, {'error': '配置暂时无法读取，请稍后重试。'})
    def do_POST(self):
        body = None
        try:
            if self.headers.get('Origin') not in {'http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')}:
                return self.respond(403, {'error': 'ORIGIN_REJECTED'})
            if (self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Length', [])) != 1):
                raise ValueError('INVALID_REQUEST')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 < length <= 1100000:
                raise ValueError('INVALID_LENGTH')
            body = json.loads(self.rfile.read(length))
            if not self.server.admin.authenticated(self.headers.get('Authorization')):
                return self.respond(401, {'error': 'LOGIN_REQUIRED'})
            if self.path == '/api/subscription':
                return self.respond(202, self.server.admin.apply(body))
            if self.path in {'/api/core/check', '/api/core/update', '/api/core/rollback'}:
                if self.path == '/api/core/update' and body != {}:
                    result = self.server.admin.core_upload(body)
                    return self.respond(202 if result.get('status') == 'running' else 200, result)
                if body != {}: raise ValueError('INVALID_REQUEST')
                return self.respond(202, self.server.admin.core_action(self.path.rsplit('/', 1)[-1]))
            if self.path == '/api/policy/reset':
                return self.respond(202, self.server.admin.reset_policy(body))
            self.respond(404, {'error': 'NOT_FOUND'})
        except ManagementError as error:
            if self.path == '/api/subscription':
                from unified_editor import editor_error_details
                details = editor_error_details(error)
                if details:
                    if details['error'] == 'CLASH_YAML_EDITING_UNSUPPORTED':
                        details['message'] = CLASH_YAML_EDITING_MESSAGE
                    return self.respond(400, details)
            code = str(error).split(':', 1)[0]
            if code == 'SYSTEM_OPERATION_FAILED' and self.path == '/api/subscription':
                return self.respond(503, {'error': code})
            allowed = {'CONFIG_REVISION_CONFLICT', 'SOURCE_CHANGE_REQUIRES_SWITCH', 'MANAGEMENT_BUSY', 'RECOVERY_REQUIRED',
                       'INVALID_EDITOR_REQUEST', 'INVALID_PPSUB', 'INVALID_URL', 'CONFIG_CHANGED'}
            return self.respond(409 if code in {'CONFIG_REVISION_CONFLICT', 'MANAGEMENT_BUSY', 'CONFIG_CHANGED'} else 400,
                                {'error': code if code in allowed else 'INVALID_EDITOR_REQUEST'})
        except ValueError as error:
            if self.path == '/api/subscription' and str(error) == 'CLASH_YAML_EDITING_UNSUPPORTED':
                return self.respond(400, {'error': str(error), 'message': CLASH_YAML_EDITING_MESSAGE})
            if self.path == '/api/subscription' and isinstance(body, dict) and body.get('kind') == 'editor':
                from unified_editor import editor_error_details
                details = editor_error_details(error)
                if details:
                    return self.respond(400, details)
            if self.path.startswith('/api/core/'):
                from mihomo_update import error_details
                details = error_details(error)
                return self.respond(409 if details['error'] in {'MANAGEMENT_BUSY', 'CORE_UPLOAD_BUSY'} else 400,
                                    {'error': details['error'], 'message': details['message']})
            if str(error) == 'CONFIG_CHANGED':
                return self.respond(409, {'error': '配置已变化，请重新读取后再试。'})
            if str(error) == 'CLASH_PPSUB_NOT_EQUIVALENT':
                return self.respond(409, {'error': '请先在“配置来源”确认切换到 LovestSub。'})
            self.respond(400, {'error': '请求无效、配置已变化或有任务正在运行，请刷新后重试。'})
        except (UnicodeError, KeyError):
            self.respond(400, {'error': '请求无效、配置已变化或有任务正在运行，请刷新后重试。'})
        except Exception:
            self.respond(503, {'error': '服务暂不可用'})


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    server = ThreadingHTTPServer(('127.0.0.1', 8789), Handler)
    server.admin = Admin()
    threading.Thread(target=server.admin.schedule_loop, daemon=True).start()
    server.serve_forever()
