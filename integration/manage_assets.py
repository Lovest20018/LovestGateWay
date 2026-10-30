"""Import bounded local data files; never follow configuration commands."""
import json
from pathlib import Path
import re
import shlex
from manage_common import ManagementError, operation_lock, atomic_write, require_idle
from manage_model import validate
from manage_backup import BackupStore


def import_asset(layout, source, kind, name, overwrite=False):
    validate('ini', 'yamlfile', name)
    extensions = {'yaml': ('.yaml', '.yml'), 'ovpn': ('.ovpn',), 'cert': ('.pem', '.crt', '.key'), 'ppsub': ('.json',)}
    if kind not in extensions or not name.endswith(extensions[kind]): raise ManagementError('ASSET_EXTENSION_MISMATCH')
    source = Path(source)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 1024*1024: raise ManagementError('INVALID_ASSET_INPUT')
    raw = source.read_bytes()
    try:
        text = raw.decode('utf-8')
        if '\x00' in text: raise ValueError()
        if kind == 'yaml':
            import yaml
            try: value = yaml.safe_load(text)
            except yaml.YAMLError: raise ValueError() from None
            if not isinstance(value, dict) or not any(k in value for k in ('proxies', 'proxy-providers')): raise ValueError()
        elif kind == 'ppsub':
            value = json.loads(text)
            if name != 'ppsub.json' or not isinstance(value, dict) or not isinstance(value.get('subs'), list) or not value['subs'] or not value.get('exported_at'): raise ValueError()
        elif kind == 'ovpn':
            validate_ovpn(text)
        elif kind == 'cert':
            if not re.fullmatch(r'\s*-----BEGIN (CERTIFICATE|PRIVATE KEY|ENCRYPTED PRIVATE KEY|RSA PRIVATE KEY)-----[A-Za-z0-9+/=\s]+-----END \1-----\s*', text): raise ValueError()
        else: raise ValueError()
    except (ValueError, UnicodeError): raise ManagementError('INVALID_ASSET_CONTENT') from None
    with operation_lock(layout):
        require_idle(layout)
        path = 'etc/paopaogw/files/' + name
        if layout.path(path).exists() and not overwrite: raise ManagementError('ASSET_EXISTS:use --overwrite')
        backup = BackupStore(layout).create([path], {'kind': 'asset'})
        atomic_write(layout, path, raw)
        return {'status': 'saved', 'name': name, 'backup': backup, 'runtime': 'unchanged_until_config_apply'}


def validate_ovpn(text):
            # An explicit data/network option allowlist covers quoted/escaped directive names too.
            allowed = {'client', 'tls-client', 'dev', 'dev-type', 'proto', 'remote', 'remote-random',
                'resolv-retry', 'nobind', 'persist-key', 'persist-tun', 'remote-cert-tls', 'verify-x509-name',
                'cipher', 'data-ciphers', 'data-ciphers-fallback', 'auth', 'auth-nocache', 'key-direction',
                'tls-version-min', 'tls-version-max', 'tls-cipher', 'tls-ciphersuites', 'verb', 'mute',
                'route', 'route-ipv6', 'route-nopull', 'route-delay', 'route-metric',
                'dhcp-option', 'pull', 'pull-filter', 'keepalive', 'ping', 'ping-restart', 'ping-timer-rem',
                'connect-retry', 'connect-retry-max', 'connect-timeout', 'hand-window', 'reneg-sec',
                'sndbuf', 'rcvbuf', 'tun-mtu', 'mssfix', 'explicit-exit-notify', 'float', 'compress',
                'comp-lzo', 'allow-compression', 'auth-retry', 'auth-token', 'push-peer-info'}
            block = None
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(('#', ';')): continue
                if line.startswith('</'):
                    if line != '</' + str(block) + '>': raise ValueError()
                    block = None; continue
                if block: continue
                if line.startswith('<'):
                    if line not in {'<ca>', '<cert>', '<key>', '<tls-auth>', '<tls-crypt>'}: raise ValueError()
                    block = line[1:-1]
                    continue
                tokens = shlex.split(line, comments=True)
                if not tokens or tokens[0] not in allowed: raise ValueError()
                if tokens[0] == 'route':
                    import ipaddress
                    if len(tokens) not in {3, 4} or tokens[2] != '255.255.255.255': raise ValueError()
                    ipaddress.IPv4Address(tokens[1])
                    if len(tokens) == 4 and tokens[3] != 'vpn_gateway': raise ValueError()
                if tokens[0] == 'route-ipv6':
                    import ipaddress
                    if len(tokens) != 2 or ipaddress.IPv6Network(tokens[1]).prefixlen != 128: raise ValueError()
            if block or not re.search(r'^\s*remote\s+', text, re.M): raise ValueError()
