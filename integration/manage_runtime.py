"""Bounded systemd operations and authenticated local readiness checks."""
import copy
import importlib.util
from pathlib import Path
import json
import re
import shutil
import socket
from contextlib import ExitStack
import subprocess
import time
from manage_common import ManagementError
from manage_model import load_settings, parse_assignments, validate

SERVICES = ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-observe',
            'paopaogw-settings',
            'paopaogw-maintenance.timer', 'paopaogw-maintenance', 'paopaogw-core-action@check',
            'paopaogw-core-action@update', 'paopaogw-core-action@rollback', 'paopaogw-core-action@install-local')


class System:
    def __init__(self, layout, timeout=90, preserve_units=()):
        self.layout = layout; self.timeout = timeout; self.preserve_units = set(preserve_units)
    def command(self, *args, check=True):
        result = subprocess.run(args, capture_output=True, text=True, timeout=45)
        if check and result.returncode: raise ManagementError('SYSTEM_OPERATION_FAILED:' + args[0])
        return result
    def status(self):
        if not self.layout.live: return {unit: {'active': False, 'enabled': False} for unit in SERVICES}
        return {unit: {'active': self.command('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0,
                       'enabled': self.command('systemctl', 'is-enabled', unit, check=False).stdout.strip() in {'enabled', 'enabled-runtime'}}
                for unit in SERVICES}
    def preflight(self, settings):
        if not self.layout.live: return
        interface = settings['service']['PPGW_INTERFACE']
        if not interface: raise ManagementError('INTERFACE_REQUIRED')
        self.command('ip', 'link', 'show', 'dev', interface)
        addresses = json.loads(self.command('ip', '-j', '-4', 'addr', 'show', 'dev', interface).stdout or '[]')
        if not any(item.get('family') == 'inet' and item.get('scope') == 'global' for link in addresses for item in link.get('addr_info', [])):
            raise ManagementError('INTERFACE_IPV4_REQUIRED')
        ini = settings['ini']; mode = ini['mode']
        if len(ini['clash_web_password']) < 8 or ini['clash_web_password'] == 'clashpass': raise ManagementError('DASHBOARD_PASSWORD_TOO_SHORT')
        if ini['udp_max_sessions'] != '' and ini['udp_reserved_sessions'] >= ini['udp_max_sessions']: raise ManagementError('INVALID_UDP_BUDGET')
        if ini['openport'] and not ini['openport_auth']: raise ManagementError('EXPLICIT_PROXY_AUTH_REQUIRED')
        if mode == 'socks5' and not ini['socks5_ip']: raise ManagementError('SOCKS5_IP_REQUIRED')
        if mode in {'yaml', 'suburl'} and not self.layout.path('usr/local/lib/paopaogw/bin/clash').is_file(): raise ManagementError('MIHOMO_REQUIRED')
        if mode in {'yaml', 'ovpn'}:
            key = 'yamlfile' if mode == 'yaml' else 'ovpnfile'
            asset = self.layout.path('etc/paopaogw/files/' + ini[key])
            if not asset.is_file(): raise ManagementError('ASSET_REQUIRED:' + key)
            if mode == 'yaml':
                binary = self.layout.path('usr/local/lib/paopaogw/bin/clash')
                if not binary.exists(): raise ManagementError('MIHOMO_REQUIRED')
                self.command(str(binary), '-t', '-d', str(self.layout.path('var/lib/paopaogw/clash')), '-f', str(asset))
            else:
                if not shutil.which('openvpn') or not self.layout.path('dev/net/tun').exists(): raise ManagementError('OPENVPN_TUN_REQUIRED')
                from manage_assets import validate_ovpn
                try: validate_ovpn(asset.read_text())
                except (ValueError, UnicodeError): raise ManagementError('UNSAFE_OPENVPN_CONFIG') from None
        if mode == 'suburl' and not ini['suburl'] and not self.layout.path('etc/paopaogw/files/ppsub.json').exists(): raise ManagementError('SUBSCRIPTION_REQUIRED')
    def install_preflight(self):
        if not self.layout.live: return
        self.layout.require_root()
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        if release.get('ID', '').strip('"') != 'debian' or int(release.get('VERSION_ID', '0').strip('"').split('.')[0]) < 12:
            raise ManagementError('DEBIAN_REQUIRED')
        missing = [name for name in ('nft', 'ip', 'python3', 'systemctl', 'pgrep', 'pidof', 'nsenter', 'openvpn', 'openssl') if shutil.which(name) is None]
        if importlib.util.find_spec('yaml') is None: missing.append('python3-yaml')
        if missing: raise ManagementError('DEPENDENCY_REQUIRED:' + ','.join(missing))

    def preflight_listeners(self, settings):
        """Check initial bind addresses before stopping services or writing a new instance."""
        if not self.layout.live: return
        endpoints = [(settings['service']['PPGW_WEB_LISTEN'], socket.SOCK_STREAM),
                     (settings['service']['PPGW_DNS_LISTEN'], socket.SOCK_DGRAM),
                     ('127.0.0.1:8789', socket.SOCK_STREAM), ('127.0.0.1:8790', socket.SOCK_STREAM)]
        if settings['api']['enabled']:
            endpoints.append((f"{settings['api']['listen']}:{settings['api']['port']}", socket.SOCK_STREAM))
        with ExitStack() as stack:
            for endpoint, kind in endpoints:
                host, port = endpoint.rsplit(':', 1); host = host.strip('[]')
                try:
                    sock = stack.enter_context(socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, kind))
                    if ':' in host: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                    sock.bind((host, int(port)))
                    if kind == socket.SOCK_STREAM: sock.listen(1)
                except OSError: raise ManagementError('LISTENER_UNAVAILABLE:' + endpoint) from None
        for family in ('ip', 'ip6'):
            result = self.command('nft', 'list', 'table', family, 'ppgw', check=False)
            if result.returncode == 0: raise ManagementError('NETWORK_RESOURCE_BUSY')
        families = ('-4', '-6') if settings['service']['PPGW_IPV6'] else ('-4',)
        for family in families:
            result = self.command('ip', family, '-j', 'route', 'show', 'table', '100', check=False)
            # Debian iproute2 may emit an incomplete '[' and exit 2 for a table that does not exist yet.
            if result.returncode:
                if 'FIB table does not exist' not in result.stderr: raise ManagementError('SYSTEM_OPERATION_FAILED:ip')
                routes = []
            else:
                try: routes = json.loads(result.stdout or '[]')
                except ValueError: raise ManagementError('SYSTEM_OPERATION_FAILED:ip') from None
            rules = json.loads(self.command('ip', family, '-j', 'rule', 'show').stdout or '[]')
            if routes or any(rule.get('priority') == 11099 or rule.get('table') == 100 for rule in rules):
                raise ManagementError('NETWORK_RESOURCE_BUSY')

    def verify_services(self, expected):
        if not self.layout.live: return
        actual = self.status()
        persistent = {'paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-settings', 'paopaogw-observe', 'paopaogw-maintenance.timer'}
        for unit, state in expected.items():
            # One-shot maintenance tasks may finish successfully during readiness checks.
            if unit in persistent and unit not in self.preserve_units and actual.get(unit) != state:
                raise ManagementError('SERVICE_STATE_MISMATCH:' + unit)

    def client(self):
        from gateway_api import CoreClient, core_base_url
        return CoreClient(self.layout.path('var/lib/paopaogw/core-token'), core_base_url(self.layout.path('etc/paopaogw/service.env')))
    def runtime(self): return self.client().request('/sniffbox/info').get('integration', {})
    def validate_policy(self, document):
        import tempfile
        import yaml
        from pathlib import Path
        if not self.layout.live: raise ManagementError('LIVE_POLICY_VALIDATION_REQUIRED')
        pid = self.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
        if not pid.isascii() or not pid.isdigit() or int(pid) <= 0: raise ManagementError('CORE_NOT_RUNNING')
        directory = self.layout.path('var/lib/paopaogw/admin/candidates')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.yaml', delete=False) as stream:
            candidate = Path(stream.name)
            try:
                candidate.chmod(0o600)
                stream.write(yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode())
                stream.close()
                self.command('nsenter', '-t', pid, '-m', '--', '/usr/local/lib/paopaogw/bin/clash',
                             '-t', '-d', '/etc/config/clash', '-f', str(candidate))
            finally: candidate.unlink(missing_ok=True)
    def prepare(self, action, before):
        self._old_runtime = None
        if self.layout.live and action in {'reload', 'restart'} and before.get('paopaogw', {}).get('active'):
            try: self._old_runtime = self.runtime()
            except Exception: pass  # CLI can repair a saved listener that no longer matches the running core.
            if action == 'reload' and self._old_runtime:
                from pathlib import Path
                pid = self.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
                if not pid.isascii() or not pid.isdigit() or int(pid) <= 0: raise ManagementError('CORE_NOT_RUNNING')
                # Fence the controller before the persistent INI can change. The
                # reload command replaces this with the target export digest.
                marker = Path('/proc') / pid / 'root/tmp/ppgw-core-only.hash'
                marker.write_text('pending\n', encoding='ascii')
                marker.chmod(0o600)
    def verify_loaded(self, settings):
        pid = self.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
        if not pid.isascii() or not pid.isdigit() or int(pid) <= 0: raise ValueError()
        from pathlib import Path
        path = Path('/proc') / pid / 'root/tmp/sniffbox_running.ini'
        if path.stat().st_size > 2*1024*1024: raise ValueError()
        values = parse_assignments(path.read_bytes())
        from manage_model import SCHEMA
        for key, expected in settings['ini'].items():
            value = values.get(key, SCHEMA['ini'][key][1])
            if value == '' and SCHEMA['ini'][key][1] != '': value = SCHEMA['ini'][key][1]
            if validate('ini', key, value, stored=True) != expected: raise ValueError()
    def wait(self, settings, old=None, action=None):
        if not self.layout.live: return {'status': 'staged'}
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                client = self.client(); info = client.request('/sniffbox/info'); runtime = info.get('integration', {})
                if info.get('mode') != settings['ini']['mode']: raise ValueError()
                # Runtime envelope is the core API's versioned projection.
                actual = runtime.get('data', runtime)
                previous = old.get('data', old) if old else {}
                if not isinstance(actual.get('boot_id'), str) or not re.fullmatch(r'[0-9a-f-]{36}', actual['boot_id']) or not re.fullmatch(r'[0-9a-f]{64}', actual.get('config_revision', '')): raise ValueError()
                if action == 'reload' and (actual.get('boot_id') != previous.get('boot_id') or actual.get('config_revision') == previous.get('config_revision')): raise ValueError()
                if action == 'restart' and previous.get('boot_id') and actual.get('boot_id') == previous.get('boot_id'): raise ValueError()
                self.verify_loaded(settings)
                if settings['ini']['mode'] in {'yaml', 'suburl'}:
                    if not info.get('clash_version'): raise ValueError()
                    client.request('/proxies')
                    if self.command('systemctl', 'is-active', '--quiet', 'paopaogw-subscription', check=False).returncode == 0:
                        client.request('/sniffbox/subscription/config')
                return {'status': 'running', 'boot_id': actual.get('boot_id'), 'config_revision': actual.get('config_revision')}
            except Exception: time.sleep(.5)
        raise ManagementError('READINESS_TIMEOUT')
    def wait_api(self, settings):
        if not settings['api']['enabled']: return
        from gateway_api import NoRedirect
        from urllib.request import build_opener, ProxyHandler, Request
        api = settings['api']; host = api['listen']
        if host == '0.0.0.0': host = '127.0.0.1'
        token = self.layout.path('etc/paopaogw/read-token').read_text().strip()
        deadline = time.monotonic() + self.timeout
        opener = build_opener(ProxyHandler({}), NoRedirect())
        while time.monotonic() < deadline:
            try:
                if ':' in host:
                    host = '::1' if host == '::' else host
                    url_host = '[' + host + ']'
                else:
                    url_host = host
                request = Request(f'http://{url_host}:{api["port"]}/api/v1/instance', headers={'Authorization': 'Bearer ' + token})
                with opener.open(request, timeout=2) as response: value = json.load(response)
                identity = self.layout.path('etc/paopaogw/instance-id').read_text().strip()
                if value.get('instance_id') == identity or value.get('data', {}).get('instance_id') == identity: return
            except Exception: pass
            time.sleep(.5)
        raise ManagementError('API_READINESS_TIMEOUT')
    def apply(self, action, settings, changes, before):
        if not self.layout.live: return {'status': 'staged'}
        old = getattr(self, '_old_runtime', None)
        if action in {'reload', 'restart'} and before.get('paopaogw', {}).get('active'):
            if action == 'reload' and not old: action = 'restart'
            if action == 'reload': self.client().request('/sniffbox/reload/core', {})
            else: self.command('systemctl', 'restart', 'paopaogw')
            result = self.wait(settings, old, action)
        else: result = {'status': 'saved', 'reason': 'core_unchanged' if before.get('paopaogw', {}).get('active') else 'core_inactive'}
        api = changes.get('api', {})
        if api:
            enabled = settings['api']['enabled']
            if 'enabled' in api or (not enabled and before.get('paopaogw-api', {}).get('active')):
                self.command('systemctl', 'enable' if enabled else 'disable', '--now', 'paopaogw-api')
                if enabled: self.wait_api(settings)
            elif enabled and before.get('paopaogw-api', {}).get('active'):
                self.command('systemctl', 'restart', 'paopaogw-api'); self.wait_api(settings)
        if 'TZ' in changes.get('service', {}):
            for unit in ('paopaogw-subscription', 'paopaogw-observe'):
                if before.get(unit, {}).get('active'): self.command('systemctl', 'restart', unit)
        maintenance = changes.get('maintenance', {})
        if 'enabled' in api or 'history_enabled' in maintenance:
            collect = settings['api']['enabled'] and settings['maintenance']['history_enabled']
            self.command('systemctl', 'enable' if collect else 'disable', '--now', 'paopaogw-observe')
        if 'auto_update' in maintenance:
            self.command('systemctl', 'enable' if maintenance['auto_update'] else 'disable', '--now', 'paopaogw-maintenance.timer')
        if ('history_retention_days' in maintenance and settings['api']['enabled']
                and settings['maintenance']['history_enabled'] and before.get('paopaogw-observe', {}).get('active')):
            self.command('systemctl', 'restart', 'paopaogw-observe')
        return result
    def stop_all(self):
        if self.layout.live:
            for unit in reversed(SERVICES):
                if unit in self.preserve_units: continue
                loaded = self.command('systemctl', 'show', '--property=LoadState', '--value', unit, check=False)
                if loaded.stdout.strip() not in {'', 'not-found'}: self.command('systemctl', 'stop', unit)
    def restore_services(self, states):
        if not self.layout.live: return
        self.command('systemctl', 'daemon-reload')
        for unit, state in states.items():
            if unit in self.preserve_units: continue
            loaded = self.command('systemctl', 'show', '--property=LoadState', '--value', unit, check=False).stdout.strip()
            if loaded in {'', 'not-found'} and not state['active'] and not state['enabled']: continue
            self.command('systemctl', 'enable' if state['enabled'] else 'disable', unit)
        for unit in reversed(tuple(states)):
            if unit in self.preserve_units: continue
            if not states[unit]['active']: self.command('systemctl', 'stop', unit, check=False)
        for unit, state in states.items():
            if unit in self.preserve_units: continue
            if state['active']: self.command('systemctl', 'restart', unit)
