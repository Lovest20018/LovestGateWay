"""Typed persistent settings. Parsing never evaluates shell expressions."""
import copy
import ipaddress
import json
import re
import shlex
import uuid
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from manage_common import ManagementError, read_bytes, digest

PATHS = {'ini': 'etc/paopaogw/files/ppgw.ini', 'service': 'etc/paopaogw/service.env',
         'api': 'etc/paopaogw/api.json', 'maintenance': 'etc/paopaogw/maintenance.json'}
EXTRA_PATHS = ('etc/paopaogw/network', 'etc/paopaogw/read-token',
               'etc/paopaogw/files/ppsub.json', 'etc/paopaogw/files/clash-policy.json')
# type, default, optional minimum/maximum or choices
SCHEMA = {
 'ini': {
  'mode': ('choice', 'free', ('free', 'socks5', 'ovpn', 'yaml', 'suburl')),
  'fake_cidr': ('v4net', '7.0.0.0/8'), 'dns_ip': ('upstream_ip', '223.5.5.5'), 'dns_port': ('int', 53, 1, 65535),
  'clash_web_password': ('secret', 'clashpass'), 'openport': ('bool', False), 'openport_auth': ('auth', ''),
  'udp_enable': ('bool', False), 'udp_max_sessions': ('optional_int', '', 1, 1000000),
  'udp_reserved_sessions': ('int', 32, 1, 1000000), 'admin_cidr': ('cidrs', ''), 'proxy_cidr': ('cidrs', ''),
  'socks5_ip': ('optional_upstream_ip', ''), 'socks5_port': ('int', 7890, 1, 65535),
  'socks5_username': ('text', ''), 'socks5_password': ('secret', ''),
  'ovpnfile': ('asset', 'custom.ovpn'), 'ovpn_username': ('text', ''), 'ovpn_password': ('secret', ''),
  'yamlfile': ('asset', 'custom.yaml'), 'suburl': ('optional_url', ''), 'subtime': ('interval', '1d'),
  'subcron': ('optional_int', '', 0, 23), 'fast_node': ('text', ''),
  'test_node_url': ('url', 'http://cp.cloudflare.com/generate_204'), 'ext_node': ('text', 'Traffic|Expire| GB|Days|Date'),
  'fall_direct': ('bool', False), 'dns_burn': ('bool', True), 'ex_dns': ('dnslist', '223.5.5.5:53,1.0.0.1:53'),
  'net_rec': ('bool', False), 'max_rec': ('int', 5000, 1, 10000000), 'net_cleanday': ('optional_int', '', 1, 31),
  'pplog': ('endpoint_optional', ''), 'pplog_uuid': ('optional_uuid', ''), 'box': ('box', ''),
  'splice_threshold': ('optional_int', '', 0, 1073741824), 'traffic_window_min': ('optional_int', '', 1, 1440)},
 'service': {'PPGW_INTERFACE': ('interface', ''), 'PPGW_IPV6': ('bool', False),
             'TZ': ('timezone', 'Asia/Shanghai'), 'PPGW_WEB_LISTEN': ('endpoint', '0.0.0.0:80'),
             'PPGW_DNS_LISTEN': ('endpoint', '0.0.0.0:53')},
 'api': {'enabled': ('bool', False), 'listen': ('ipv4', '127.0.0.1'), 'port': ('int', 8788, 1, 65535),
         'name': ('name', 'LovestGateWay'), 'probe_urls': ('probe_urls', []), 'read_token': ('observer_token', '')},
 'maintenance': {'history_enabled': ('bool', True), 'history_retention_days': ('int', 30, 1, 90),
                 'auto_update': ('bool', True), 'timezone': ('timezone', 'Asia/Shanghai')},
}
SECRET_KEYS = {'clash_web_password', 'openport_auth', 'socks5_password', 'ovpn_password', 'suburl', 'read_token'}
BOX = {'route.block_bittorrent': 'bool', 'route.block_quic': 'bool', 'route.block_unknown': 'bool'}


def scan_value(text):
    output = []; index = 0; quote = None
    while index < len(text):
        char = text[index]
        if quote is None and char.isspace(): break
        if char in {'"', "'"} and (quote is None or quote == char):
            quote = char if quote is None else None; index += 1; continue
        if char == '\\' and quote != "'":
            if index + 1 >= len(text): raise ManagementError('INVALID_QUOTE')
            following = text[index + 1]
            if quote is None or following in {'"', '\\', '$', '`'}:
                output.append(following); index += 2; continue
        output.append(char); index += 1
    if quote: raise ManagementError('INVALID_QUOTE')
    return ''.join(output), text[index:]


def parse_assignments(raw):
    result = {}
    for line in raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').splitlines():
        match = re.match(r'^\s*([A-Za-z_][A-Za-z_0-9]*)=(.*)$', line)
        if not match: continue
        result[match[1]] = scan_value(match[2])[0]
    return result


def endpoint(value):
    parsed = urlsplit('http://' + value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError()
    ipaddress.ip_address(parsed.hostname)
    if not parsed.port or not 1 <= parsed.port <= 65535: raise ValueError()


def validate(namespace, key, value, stored=False):
    spec = SCHEMA[namespace][key]; kind = spec[0]
    if value is None:
        if spec[1] == '': value = ''
        else: raise ManagementError('NOT_CLEARABLE:' + key)
    try:
        if stored and kind in {'int', 'optional_int'} and value != '': value = int(value)
        if stored and kind == 'bool':
            if value in ('yes', 'true', '1', True): value = True
            elif value in ('no', 'false', '0', False): value = False
            else: raise ValueError()
        if kind == 'bool':
            if type(value) is not bool: raise ValueError()
        elif kind in {'int', 'optional_int'}:
            if kind == 'optional_int' and value == '': return value
            if type(value) is not int or not spec[2] <= value <= spec[3]: raise ValueError()
        elif kind == 'probe_urls':
            from gateway_api import valid_probe_url
            if not isinstance(value, list) or len(value) > 16 or not all(valid_probe_url(x) for x in value): raise ValueError()
        else:
            if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError()
            if not stored and value == '********': raise ValueError()
            if kind == 'choice' and value not in spec[2]: raise ValueError()
            if kind in {'ip', 'optional_ip'} and (value or kind == 'ip'): ipaddress.ip_address(value)
            if kind == 'ipv4': ipaddress.IPv4Address(value)
            if kind == 'name' and not 1 <= len(value) <= 100: raise ValueError()
            if kind == 'observer_token' and not (stored and value == ''):
                if not 32 <= len(value) <= 256 or any(not 33 <= ord(c) <= 126 for c in value): raise ValueError()
            if kind == 'optional_uuid' and value: uuid.UUID(value)
            if kind == 'auth' and value:
                user, sep, password = value.partition(':')
                if not sep or not user or not password or len(user.encode()) > 255 or len(password.encode()) > 255: raise ValueError()
            if kind in {'upstream_ip', 'optional_upstream_ip'} and (value or kind == 'upstream_ip'):
                if ':' in value:
                    if not (value.startswith('[') and value.endswith(']')): raise ValueError()
                    ipaddress.IPv6Address(value[1:-1])
                else: ipaddress.IPv4Address(value)
            if kind == 'v4net': ipaddress.IPv4Network(value, strict=False)
            if kind == 'cidrs' and value:
                for item in value.split(','): ipaddress.ip_network(item.strip(), strict=False)
            if kind == 'asset' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value): raise ValueError()
            if kind == 'interface' and value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}', value): raise ValueError()
            if kind in {'url', 'optional_url'} and (value or kind == 'url'):
                p = urlsplit(value[6:] if key == 'suburl' and value.startswith('ppsub@') else value)
                if p.scheme not in {'http', 'https'} or not p.hostname or p.fragment or p.username or p.password: raise ValueError()
                p.port
            if kind == 'interval' and not (key == 'subtime' and value == 'manual') and not re.fullmatch(r'[1-9][0-9]{0,3}[mhd]', value): raise ValueError()
            if kind == 'cron' and value:
                fields = value.split()
                if len(fields) != 5: raise ValueError()
                for field, maximum in zip(fields, (59, 23, 31, 12, 7)):
                    if not re.fullmatch(r'[0-9*,/\-]+', field): raise ValueError()
                    if any(int(n) > maximum for n in re.findall(r'\d+', field)): raise ValueError()
            if kind == 'timezone': ZoneInfo(value)
            if kind in {'endpoint', 'endpoint_optional'} and (value or kind == 'endpoint'): endpoint(value)
            if kind == 'dnslist' and value:
                for item in value.split(','): endpoint(item.strip())
            if kind == 'box' and value and not stored:
                for assignment in value.split(','):
                    name, sep, item = assignment.partition('=')
                    if not sep or name not in BOX: raise ValueError()
                    typ = BOX[name]
                    if typ == 'bool' and item not in {'true', 'false'}: raise ValueError()
                    if typ == 'int' and (not item.isdigit() or not 1 <= int(item) <= 10000000): raise ValueError()
                    if typ == 'duration' and not re.fullmatch(r'[1-9][0-9]*(ms|s|m)', item): raise ValueError()
                    if typ == 'level' and item not in {'off', 'error', 'warn', 'info', 'debug', 'trace'}: raise ValueError()
        return value
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ManagementError('INVALID_SETTING:' + namespace + '.' + key) from None


def load_settings(layout):
    result = {}
    for namespace, path in PATHS.items():
        raw = read_bytes(layout, path, b'')
        try:
            values = parse_assignments(raw) if namespace in {'ini', 'service'} else (json.loads(raw) if raw else {})
            if not isinstance(values, dict): raise ValueError()
            if namespace == 'api':
                values['read_token'] = read_bytes(layout, 'etc/paopaogw/read-token', b'').decode('ascii').strip()
            result[namespace] = {}
            for key, spec in SCHEMA[namespace].items():
                value = values.get(key, spec[1])
                if value == '' and spec[1] != '': value = spec[1]
                result[namespace][key] = validate(namespace, key, value, stored=True)
        except (ValueError, UnicodeError):
            raise ManagementError('INVALID_CONFIG:' + namespace) from None
    return result


def public_settings(settings):
    result = copy.deepcopy(settings)
    for fields in result.values():
        if isinstance(fields, dict):
            for key in SECRET_KEYS:
                if fields.get(key): fields[key] = '********'
    return result


def settings_revision(layout):
    records = {path: None if (raw := read_bytes(layout, path)) is None else digest(raw)
               for path in (*PATHS.values(), *EXTRA_PATHS)}
    directory = layout.path('etc/paopaogw/files')
    if directory.exists():
        for path in sorted(directory.rglob('*')):
            layout.check(path)
            if path.is_file():
                relative = path.relative_to(layout.root).as_posix()
                records[relative] = digest(read_bytes(layout, relative, limit=2*1024*1024))
    return digest(json.dumps(records, sort_keys=True).encode())


class RevisionSample:
    """Hash contents only on metadata change; bound cache age covers metadata-preserving edits."""
    def __init__(self):
        self.lock = threading.Lock()
        self.signature = None
        self.revision = None
        self.checked = 0

    def metadata(self, layout):
        names = set((*PATHS.values(), *EXTRA_PATHS))
        directory = layout.path('etc/paopaogw/files')
        if directory.exists():
            for path in directory.rglob('*'):
                layout.check(path)
                names.add(path.relative_to(layout.root).as_posix())
        rows = []
        for name in sorted(names):
            path = layout.path(name)
            try:
                value = path.stat()
                rows.append((name, value.st_mode, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
            except FileNotFoundError: rows.append((name, None))
        return rows

    def sample(self, layout):
        with self.lock:
            signature = self.metadata(layout)
            if signature != self.signature or time.monotonic() - self.checked >= 60:
                revision = settings_revision(layout)
                if signature != self.metadata(layout):
                    raise ManagementError('CONFIG_CHANGED_DURING_SAMPLE')
                self.revision, self.signature, self.checked = revision, signature, time.monotonic()
            return self.revision


def render_assignments(raw, changes, namespace):
    text = raw.decode('utf-8'); newline = '\r\n' if '\r\n' in text else '\n'
    remaining = dict(changes); lines = text.splitlines()
    output = []
    for line in lines:
        match = re.match(r'^\s*([A-Za-z_][A-Za-z_0-9]*)=', line)
        if match and match[1] in changes:
            key = match[1]
            if key not in remaining: continue
            value = remaining.pop(key)
            suffix = scan_value(line.split('=', 1)[1])[1]
            output.append(key + '=' + quote_value(serialize(value), namespace) + suffix)
        else: output.append(line)
    output.extend(key + '=' + quote_value(serialize(value), namespace) for key, value in remaining.items())
    return (newline.join(output) + newline).encode()


def serialize(value):
    return ('yes' if value else 'no') if type(value) is bool else str(value)


def quote_value(value, namespace):
    # systemd EnvironmentFile supports double quotes but not shell quote concatenation.
    if namespace == 'service': return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'
    return shlex.quote(value)


def render_updates(layout, patch):
    if not isinstance(patch, dict) or set(patch) - set(SCHEMA): raise ManagementError('UNKNOWN_NAMESPACE')
    patch = copy.deepcopy(patch)
    tz_service = patch.get('service', {}).get('TZ')
    tz_maintenance = patch.get('maintenance', {}).get('timezone')
    if tz_service is not None and tz_maintenance is not None and tz_service != tz_maintenance: raise ManagementError('TIMEZONE_CONFLICT')
    timezone = tz_service if tz_service is not None else tz_maintenance
    if timezone is not None:
        patch.setdefault('service', {})['TZ'] = timezone
        patch.setdefault('maintenance', {})['timezone'] = timezone
    current = load_settings(layout); changes = {}; updates = {}
    for namespace, fields in patch.items():
        if not isinstance(fields, dict) or set(fields) - set(SCHEMA[namespace]): raise ManagementError('UNKNOWN_SETTING:' + namespace)
        for key, value in fields.items():
            if namespace == 'api' and key == 'read_token' and value == '': continue
            value = validate(namespace, key, value)
            if value != current[namespace][key]: changes.setdefault(namespace, {})[key] = value
    requested_ipv6 = patch.get('service', {}).get('PPGW_IPV6')
    if type(requested_ipv6) is bool:
        network = read_bytes(layout, 'etc/paopaogw/network', b'')
        if (b'eth06' in network) != requested_ipv6: changes.setdefault('service', {})['PPGW_IPV6'] = requested_ipv6
    for namespace, fields in changes.items():
        fields = dict(fields)
        if namespace == 'api' and 'read_token' in fields:
            updates['etc/paopaogw/read-token'] = (fields.pop('read_token') + '\n').encode('ascii')
            if not fields: continue
        path = PATHS[namespace]; raw = read_bytes(layout, path, b'')
        if namespace in {'ini', 'service'}: updates[path] = render_assignments(raw, fields, namespace)
        else:
            obj = json.loads(raw) if raw else {}; obj.update(fields)
            updates[path] = (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode()
    if 'PPGW_IPV6' in changes.get('service', {}):
        path = 'etc/paopaogw/network'; raw = read_bytes(layout, path, b'')
        # The native controller tests substring presence, including legacy comments.
        lines = [line.replace('eth06', 'gateway-ipv6') for line in raw.decode().splitlines() if line.strip() != 'eth06']
        if changes['service']['PPGW_IPV6']: lines.append('eth06')
        updates[path] = ('\n'.join(lines) + '\n').encode()
    return updates, changes
