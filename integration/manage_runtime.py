"""Bounded systemd operations and authenticated local readiness checks."""
import copy
import json
import re
import shutil
import subprocess
import time
from manage_common import ManagementError
from manage_model import load_settings, parse_assignments, validate

SERVICES = ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-observe',
            'paopaogw-maintenance.timer', 'paopaogw-maintenance', 'paopaogw-core-action@check',
            'paopaogw-core-action@update', 'paopaogw-core-action@rollback')


class System:
    def __init__(self, layout, timeout=90): self.layout = layout; self.timeout = timeout
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
        ini = settings['ini']; mode = ini['mode']
        if len(ini['clash_web_password']) < 16 or ini['clash_web_password'] == 'clashpass': raise ManagementError('DASHBOARD_PASSWORD_TOO_SHORT')
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
    def client(self):
        from gateway_api import CoreClient, core_base_url
        return CoreClient(self.layout.path('var/lib/paopaogw/core-token'), core_base_url(self.layout.path('etc/paopaogw/service.env')))
    def runtime(self): return self.client().request('/sniffbox/info').get('integration', {})
    def prepare(self, action, before):
        self._old_runtime = None
        if self.layout.live and action in {'reload', 'restart'} and before.get('paopaogw', {}).get('active'):
            try: self._old_runtime = self.runtime()
            except Exception: pass  # CLI can repair a saved listener that no longer matches the running core.
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
            if action == 'reload': self.client().request('/sniffbox/reload', {})
            else: self.command('systemctl', 'restart', 'paopaogw')
            result = self.wait(settings, old, action)
        else: result = {'status': 'saved', 'reason': 'core_inactive' if not old else 'core_unchanged'}
        if 'api' in changes and before.get('paopaogw-api', {}).get('active'):
            self.command('systemctl', 'restart', 'paopaogw-api'); self.wait_api(settings)
        if 'TZ' in changes.get('service', {}):
            for unit in ('paopaogw-subscription', 'paopaogw-observe'):
                if before.get(unit, {}).get('active'): self.command('systemctl', 'restart', unit)
        maintenance = changes.get('maintenance', {})
        for field, unit in (('history_enabled', 'paopaogw-observe'), ('auto_update', 'paopaogw-maintenance.timer')):
            if field in maintenance:
                enabled = maintenance[field]
                self.command('systemctl', 'enable' if enabled else 'disable', '--now', unit)
        if 'history_retention_days' in maintenance and settings['maintenance']['history_enabled'] and before.get('paopaogw-observe', {}).get('active'):
            self.command('systemctl', 'restart', 'paopaogw-observe')
        return result
    def stop_all(self):
        if self.layout.live:
            for unit in reversed(SERVICES):
                loaded = self.command('systemctl', 'show', '--property=LoadState', '--value', unit, check=False)
                if loaded.stdout.strip() not in {'', 'not-found'}: self.command('systemctl', 'stop', unit)
    def restore_services(self, states):
        if not self.layout.live: return
        self.command('systemctl', 'daemon-reload')
        for unit, state in states.items():
            self.command('systemctl', 'enable' if state['enabled'] else 'disable', unit, check=False)
        for unit in reversed(tuple(states)):
            if not states[unit]['active']: self.command('systemctl', 'stop', unit, check=False)
        for unit, state in states.items():
            if state['active']: self.command('systemctl', 'restart', unit)
