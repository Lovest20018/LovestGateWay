"""Read-only, bounded explanations of the existing management transaction plan.

Values used for comparison remain private. Public results contain counts, fixed
field names and bounded object names; rule text and credential values never do.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import json
import re

from clash_ppsub import source_identity
from clash_policy import SECTIONS
from manage_model import SCHEMA, SECRET_KEYS, load_settings

LIMIT = 20
_UNSET = object()
_FEATURES = ('global_monitor', 'restart_cron', 'shadowsocks_in', 'node-groups')
_RULE_TYPES = frozenset('DOMAIN DOMAIN-SUFFIX DOMAIN-KEYWORD DOMAIN-REGEX GEOSITE GEOIP '
    'IP-CIDR IP-CIDR6 IP-SUFFIX IP-ASN SRC-IP-CIDR SRC-IP-SUFFIX SRC-IP-ASN '
    'SRC-GEOIP DST-PORT SRC-PORT IN-PORT IN-TYPE IN-USER IN-NAME '
    'PROCESS-NAME PROCESS-NAME-REGEX PROCESS-PATH PROCESS-PATH-REGEX '
    'UID NETWORK DSCP RULE-SET AND OR NOT SUB-RULE MATCH SCRIPT'.split())


def _name(value):
    if not isinstance(value, str): return '（未命名）'
    if re.search(r'(?i)[a-z][a-z0-9+.-]*://|(?:password|passwd|token|authorization)\s*[:=]', value):
        return '（名称已隐藏）'
    return ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127)[:100]


def _signature(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row_name(row):
    return row.get('name') if isinstance(row, dict) else None


def _records(rows, renames=None):
    """Names identify native rows; explicit editor IDs supply any rename map."""
    result, order, occurrences = {}, [], defaultdict(int)
    for row in rows:
        name = _row_name(row)
        identity = ('name', (renames or {}).get(name, name)) if isinstance(name, str) and name else ('value', _signature(row))
        occurrences[identity] += 1
        key = (*identity, occurrences[identity])
        result[key] = row; order.append(key)
    return result, order


def named_changes(before, after, renames=None, expose_names=True):
    old, old_order = _records(before, renames)
    new, new_order = _records(after)
    added = [key for key in new_order if key not in old]
    removed = [key for key in old_order if key not in new]
    modified = [key for key in new_order if key in old and old[key] != new[key]]
    common = old.keys() & new.keys()
    order_changed = [key for key in old_order if key in common] != [key for key in new_order if key in common]
    result = {'counts': {'before': len(before), 'after': len(after), 'added': len(added),
                        'removed': len(removed), 'modified': len(modified)},
              'order_changed': order_changed, 'changed': bool(added or removed or modified or order_changed)}
    if expose_names:
        buckets = {'added': (added, new), 'removed': (removed, old), 'modified': (modified, new)}
        result['names'] = {kind: [_name(_row_name(rows[key])) for key in keys[:LIMIT]]
                           for kind, (keys, rows) in buckets.items()}
        result['names_truncated'] = any(len(keys) > LIMIT for keys, _ in buckets.values())
        renamed = [{'before': _name(_row_name(old[key])), 'after': _name(_row_name(new[key]))}
                   for key in modified if _row_name(old[key]) != _row_name(new[key])]
        result['renamed'] = renamed[:LIMIT]
        result['renames_truncated'] = len(renamed) > LIMIT
    return result


def _mapping_rows(value):
    return [dict(row, name=name) if isinstance(row, dict) else {'name': name, '_value': row}
            for name, row in (value or {}).items()]


def _rule_type(line):
    value = line.split(',', 1)[0].strip().upper() if isinstance(line, str) else ''
    return value if value in _RULE_TYPES else 'OTHER'


def rule_changes(before, after):
    # Rule lines have no stable identity: replacement is removal + addition.
    # This avoids guessing that adjacent edits represent the same rule.
    result = named_changes(before, after, expose_names=False)
    result['unit'] = 'lines'
    result['types'] = {'before': dict(sorted(Counter(map(_rule_type, before)).items())),
                       'after': dict(sorted(Counter(map(_rule_type, after)).items()))}
    return result


def _native_rules(before, after):
    result = named_changes(before, after, expose_names=False)
    result['unit'] = 'blocks'
    def kind(row):
        if not isinstance(row, dict): return 'OTHER'
        if row.get('type') == 'rule-set': return 'RULE-SET'
        if row.get('url'): return 'REMOTE'
        if isinstance(row.get('fixrule'), list): return 'INLINE'
        return 'OTHER'
    result['types'] = {'before': dict(sorted(Counter(map(kind, before)).items())),
                       'after': dict(sorted(Counter(map(kind, after)).items()))}
    def lines(rows):
        return [line for row in rows if isinstance(row, dict) for line in row.get('fixrule', [])
                if isinstance(line, str)]
    result['inline_rules'] = rule_changes(lines(before), lines(after))
    # Native processing sorts blocks by priority. Ignore that field only when
    # pairing otherwise-identical anonymous blocks for the ordering explanation.
    def priorities(rows):
        normalized = [{key: value for key, value in row.items() if key != 'priority'}
                      if isinstance(row, dict) else row for row in rows]
        _, order = _records(normalized)
        mapping = dict(zip(order, rows))
        def priority(key):
            row = mapping[key]
            value = row.get('priority', 0) if isinstance(row, dict) else 0
            return value if type(value) is int else 0
        return mapping, sorted(order, key=priority)
    old, old_order = priorities(before)
    new, new_order = priorities(after)
    result['priority_changed'] = any(isinstance(old[key], dict) and isinstance(new[key], dict)
        and old[key].get('priority') != new[key].get('priority') for key in old.keys() & new.keys())
    common = old.keys() & new.keys()
    result['order_changed'] = result['order_changed'] or [key for key in old_order if key in common] != [
        key for key in new_order if key in common]
    return result


def field_changes(before, after, allowed=None):
    keys = [key for key in (allowed or sorted(set(before) | set(after))) if before.get(key) != after.get(key)]
    return {'modified': len(keys), 'changed_keys': keys[:LIMIT], 'keys_truncated': len(keys) > LIMIT,
            'changed': bool(keys)}


def settings_changes(changes):
    keys = [namespace + '.' + key for namespace, fields in changes.items() for key in fields
            if namespace in SCHEMA and key in SCHEMA[namespace]]
    return {'modified': len(keys), 'changed_keys': keys[:LIMIT], 'keys_truncated': len(keys) > LIMIT,
            'changed': bool(keys)}


def safe_changes(changes):
    """Keep legacy changes keys/types for ordinary fields while hiding all URLs/auth."""
    result = deepcopy(changes)
    for namespace, fields in result.items():
        for key in fields:
            kind = SCHEMA.get(namespace, {}).get(key, ('',))[0]
            if key in SECRET_KEYS or 'url' in kind or kind in {'secret', 'auth', 'observer_token'} or any(
                    part in key.lower() for part in ('password', 'username', 'token', 'uuid')):
                fields[key] = '已修改'
    return result


def effects(preview):
    changes = preview.get('changes', {})
    services = []
    if changes.get('api'): services.append('api')
    maintenance = changes.get('maintenance', {})
    if 'enabled' in changes.get('api', {}) or set(maintenance) & {'history_enabled', 'history_retention_days'}: services.append('history')
    if 'auto_update' in maintenance: services.append('maintenance_timer')
    if 'TZ' in changes.get('service', {}): services.extend(['subscription', 'history'])
    return {'core': preview['action'], 'services': list(dict.fromkeys(services)),
            'web_listen': changes.get('service', {}).get('PPGW_WEB_LISTEN'),
            'access_changed': bool(set(changes.get('ini', {})) & {'admin_cidr', 'clash_web_password'}),
            'source_reset': False}


def application(preview, system, layout):
    action = preview['action']
    states = {}
    try:
        if layout.live: states = system.status()
    except Exception: states = {}
    def is_active(unit):
        value = states.get(unit, {}).get('active') if isinstance(states, dict) else None
        return value if type(value) is bool else None
    active = is_active('paopaogw') if layout.live else False
    state = 'staged' if not layout.live else 'unknown' if active is None else 'active' if active else 'inactive'
    services, possible = [], []
    def affects(name, unit=None):
        enabled = is_active(unit) if unit else True
        if enabled is True: services.append(name)
        elif enabled is None: possible.append(name)
    # Match System.apply's auxiliary branches, including changes that only save
    # settings while a disabled/inactive auxiliary service remains stopped.
    if layout.live:
        changes = preview.get('changes', {})
        target = load_settings(layout)
        for namespace, fields in changes.items(): target[namespace].update(fields)
        api, maintenance = changes.get('api', {}), changes.get('maintenance', {})
        if api: affects('api', None if 'enabled' in api else 'paopaogw-api')
        if 'TZ' in changes.get('service', {}):
            affects('subscription', 'paopaogw-subscription')
            affects('history', 'paopaogw-observe')
        if 'enabled' in api or 'history_enabled' in maintenance: affects('history')
        if 'auto_update' in maintenance: affects('maintenance_timer')
        if ('history_retention_days' in maintenance and target['api']['enabled']
                and target['maintenance']['history_enabled']): affects('history', 'paopaogw-observe')
    services = list(dict.fromkeys(services))
    possible = [name for name in dict.fromkeys(possible) if name not in services]
    return {'action': action if action != 'none' else 'auxiliary' if services else 'none',
            'core_action': action, 'core_active': active, 'runtime_state': state,
            'pending_load': action in {'reload', 'restart'} and active is False,
            'services': services, 'possible_services': possible, 'services_uncertain': bool(possible),
            'reload_fallback_possible': action == 'reload' and active is True}


def _read_policy(layout):
    path = layout.path('etc/paopaogw/files/clash-policy.json')
    if not path.is_file(): return None
    try:
        if path.stat().st_size > 2 * 1024 * 1024: return False
        return json.loads(path.read_bytes())
    except (OSError, ValueError): return False


def source_details(layout, values=None, policy=_UNSET, native=None):
    values = values if values is not None else load_settings(layout)['ini']
    if native is None:
        native = values.get('mode') == 'suburl' and (str(values.get('suburl', '')).startswith('ppsub@')
            or layout.path('etc/paopaogw/files/ppsub.json').is_file())
    mode = values.get('mode')
    kind = 'native_ppsub' if native else 'local_yaml' if mode == 'yaml' else 'remote_yaml' if mode == 'suburl' else 'none'
    policy = _read_policy(layout) if policy is _UNSET else policy
    valid = (isinstance(policy, dict) and policy.get('schema') == 1
             and isinstance(policy.get('sections'), dict) and not set(policy['sections']) - set(SECTIONS)
             and isinstance(policy.get('features') or {}, dict))
    applies = valid and kind in {'local_yaml', 'remote_yaml', 'native_ppsub'} and policy.get('source_identity') == source_identity(values)
    sections = [key for key in SECTIONS if valid and key in policy['sections']]
    features = [key for key in _FEATURES if valid and (policy.get('features') or {}).get(key)]
    result = {'kind': kind, 'local_policy': {'present': policy is not None, 'applies': bool(applies),
        'sections': sections, 'feature_keys': features,
        'state': 'none' if policy is None else 'invalid' if not valid else 'active' if applies else 'source_mismatch',
        'scope': 'section_override'}}
    if kind == 'local_yaml': result['file'] = _name(values.get('yamlfile', ''))
    return result


def _finish(summary):
    summary['schema'] = 1
    summary['changed'] = any(section.get('changed', False) for section in summary.values() if isinstance(section, dict))
    return summary


def native_summary(before, after):
    before, after = before or {}, after or {}
    summary = {key: named_changes(before.get(native, []), after.get(native, []))
               for key, native in (('subscriptions', 'subs'), ('node_groups', 'node-groups'))}
    summary['rule_providers'] = named_changes(
        [row for row in before.get('rules', []) if isinstance(row, dict) and row.get('type') == 'rule-set'],
        [row for row in after.get('rules', []) if isinstance(row, dict) and row.get('type') == 'rule-set'])
    summary['rules'] = _native_rules(before.get('rules', []), after.get('rules', []))
    summary['features'] = field_changes(before, after, _FEATURES[:3])
    known = {'subs', 'node-groups', 'rules', 'exported_at', *_FEATURES[:3]}
    other = sum(before.get(key) != after.get(key) for key in set(before) | set(after) if key not in known)
    summary['other_fields'] = {'modified': other, 'changed': bool(other)}
    return _finish(summary)


def clash_summary(before, after, old_features, new_features, group_renames=None, provider_renames=None):
    summary = {'subscriptions': named_changes(_mapping_rows(before.get('proxy-providers')), _mapping_rows(after.get('proxy-providers'))),
        'node_groups': named_changes(before.get('proxy-groups', []), after.get('proxy-groups', []), group_renames),
        'rule_providers': named_changes(_mapping_rows(before.get('rule-providers')), _mapping_rows(after.get('rule-providers')), provider_renames),
        'rules': rule_changes(before.get('rules', []), after.get('rules', [])),
        'features': field_changes(old_features, new_features, _FEATURES)}
    known = set(SECTIONS)
    other = sum(before.get(key) != after.get(key) for key in set(before) | set(after) if key not in known)
    summary['other_fields'] = {'modified': other, 'changed': bool(other)}
    return _finish(summary)


def complete_preview(result, layout, system, summary=None, source=None):
    result.setdefault('effects', effects(result))
    result['application'] = application(result, system, layout)
    result['changes'] = safe_changes(result.get('changes', {}))
    summary = summary if summary is not None else native_summary({}, {})
    summary['settings'] = settings_changes(result['changes'])
    _finish(summary)
    result['change_summary'] = summary
    result['source_details'] = source if source is not None else source_details(layout)
    return result
