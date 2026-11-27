"""Project a loaded Clash document into PPsub for inspection, without inventing policy."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import re
import subprocess
from manage_common import ManagementError

MAX_YAML_BYTES = 2 * 1024 * 1024
DEFAULT_RULE_BEHAVIOR = 'classical'
DEFAULT_RULE_INTERVAL = 86400
RULE_FORMATS = {'yaml', 'text', 'mrs'}
BUILTIN_TARGETS = {'DIRECT', 'REJECT', 'REJECT-DROP', 'COMPATIBLE', 'PASS', 'PASS-RULE', 'GLOBAL'}
LOGIC_RULES = {'AND', 'OR', 'NOT'}
COMMA_PAYLOAD_RULES = LOGIC_RULES | {'SUB-RULE', 'DOMAIN-REGEX', 'PROCESS-NAME-REGEX', 'PROCESS-PATH-REGEX'}


def running_materialized_document():
    result = subprocess.run(['systemctl', 'show', 'paopaogw.service', '--property=MainPID', '--value'],
                            capture_output=True, text=True, check=True, timeout=5)
    pid = result.stdout.strip()
    if not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
        raise ValueError('CORE_NOT_RUNNING')
    # MainPID is the controller in the core's private mount namespace.
    path = Path('/proc') / pid / 'root/tmp/clash.yaml'
    with path.open('rb') as stream:
        raw = stream.read(MAX_YAML_BYTES + 1)
    if len(raw) > MAX_YAML_BYTES:
        raise ValueError('CONFIG_TOO_LARGE')
    import yaml
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError('INVALID_CLASH_DOCUMENT')
    return document


def running_document():
    return logical_document(running_materialized_document())


def logical_document(document):
    """Undo only our materialized helpers before taking another editor snapshot."""
    result = deepcopy(document)
    managed = result.pop('x-ppgw-features', None)
    if not isinstance(managed, dict) or managed.get('schema') != 1:
        return result
    originals = managed.get('groups', {})
    result['proxy-groups'] = [deepcopy(originals.get(row.get('name'), row))
                              for row in result.get('proxy-groups', [])]
    for name in managed.get('providers', []):
        result.get('proxy-providers', {}).pop(name, None)
    if managed.get('smart_proxy'):
        result['proxies'] = [row for row in result.get('proxies', []) if row.get('name') != 'smartspeedtest@hide']
    if managed.get('ss_listener'):
        result['listeners'] = [row for row in result.get('listeners', []) if row.get('name') != 'ppgw-shadowsocks-in']
    # Preserve the absence of optional sections for a stable no-op fingerprint.
    for name in managed.get('absent_sections', []):
        if not result.get(name): result.pop(name, None)
    return result


def editor_config(document, subscription_url=''):
    groups = document.get('proxy-groups', [])
    rules = document.get('rules', [])
    providers = document.get('rule-providers', {})
    if (not isinstance(groups, list) or not isinstance(rules, list)
            or not isinstance(providers, dict) or not all(isinstance(row, str) for row in rules)):
        raise ValueError('INVALID_CLASH_POLICY')
    subs = []
    for name, provider in (document.get('proxy-providers') or {}).items():
        if isinstance(provider, dict) and isinstance(provider.get('url'), str):
            subs.append({'name': str(name), 'url': provider['url']})
    if not subs and subscription_url:
        subs.append({'name': '当前订阅', 'url': subscription_url})
    for index, row in enumerate(subs):
        row['_clash_id'] = 's:' + str(index)
    node_groups = []
    for index, row in enumerate(groups):
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            raise ValueError('INVALID_CLASH_GROUP')
        members = row.get('proxies') or []
        if not isinstance(members, list) or not all(isinstance(name, str) for name in members):
            raise ValueError('INVALID_CLASH_MEMBERS')
        group = {'_clash_id': 'g:' + str(index), 'name': row['name'],
                 'subs': list(row.get('use') or []), 'include': list(dict.fromkeys(members)),
                 'keywords': ['exp#' + row['filter']] if row.get('filter') else [],
                 'exclude_keywords': ['exp#' + row['exclude-filter']] if row.get('exclude-filter') else []}
        if row.get('include-all') or row.get('include-all-proxies') or row.get('include-all-providers'):
            group['subs'] = ['all']
        if 'smartspeedtest@hide' in group['include']:
            group['include'].remove('smartspeedtest@hide')
            group['smart'] = True
        if row.get('dialer-proxy'):
            group.update(use_pre_proxy=True, pre_proxy_group=row['dialer-proxy'])
        if row.get('type') == 'url-test':
            group.update(speedtest_url=row.get('url', ''), interval=row.get('interval', 600))
        elif row.get('type') == 'fallback':
            group.update(failover=True, monitor_url=row.get('url', ''),
                         expected_status=str(row.get('expected-status', '0')))
        node_groups.append(group)
    rule_sets = []
    for name, row in providers.items():
        if not isinstance(row, dict):
            raise ValueError('INVALID_CLASH_RULE_PROVIDER')
        rule_sets.append({'_clash_id': 'p:' + str(name), 'priority': (len(rule_sets) + 1) * 10, 'type': 'rule-set',
                          'name': str(name), 'url': row.get('url', ''),
                          'behavior': row.get('behavior', DEFAULT_RULE_BEHAVIOR),
                          'interval': row.get('interval', DEFAULT_RULE_INTERVAL),
                          'format': row.get('format', 'yaml'), 'proxy': row.get('proxy', '')})
    if rules:
        rule_sets.append({'_clash_id': 'rules', 'priority': (len(rule_sets) + 1) * 10,
                          'remark': '当前完整分流规则（保持原顺序）', 'fixrule': list(rules)})
    config = {'version': 'PaoPaoGateWay 3.1', 'exported_at': 'running-clash-snapshot',
              'subs': subs, 'node-groups': node_groups, 'rules': rule_sets}
    counts = {'node_groups': len(groups), 'rules': len(rules), 'rule_providers': len(providers)}
    return config, counts


def fingerprint(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def source_identity(values):
    return fingerprint({key: values.get(key, '') for key in ('mode', 'suburl', 'yamlfile')})


def strings(value):
    if not isinstance(value, list) or not all(isinstance(item, str) and len(item) <= 8192 for item in value):
        raise ValueError('INVALID_EDITOR_LIST')
    return value


def group_view(row):
    members = strings(row.get('include', []))
    subs = strings(row.get('subs', [])) or ([] if members else ['all'])
    mode = ('smart' if row.get('smart') else 'url-test' if row.get('speedtest_url') or row.get('interval')
            else 'fallback' if row.get('failover') else 'select')
    return {'name': row.get('name', ''), 'include': members, 'subs': subs,
            'keywords': strings(row.get('keywords', [])), 'exclude_keywords': strings(row.get('exclude_keywords', [])),
            'mode': mode, 'url': row.get('speedtest_url', '') if mode == 'url-test' else
                (row.get('monitor_url') or 'https://www.youtube.com/generate_204') if mode == 'fallback' else '',
            'interval': int(row.get('interval') or 600) if mode == 'url-test' else None,
            'expected': str(row.get('expected_status') or '0') if mode == 'fallback' else None,
            'retries': row.get('retries', 3) if mode == 'fallback' else None,
            'pre_proxy': row.get('pre_proxy_group', '') if row.get('use_pre_proxy') else ''}


def provider_view(row):
    return {key: default if row.get(key) is None else row[key] for key, default in
            (('name', ''), ('url', ''), ('behavior', DEFAULT_RULE_BEHAVIOR), ('format', 'yaml'),
             ('interval', DEFAULT_RULE_INTERVAL), ('proxy', ''))}


def rule_lines(row):
    if row.get('url') and row.get('type') != 'rule-set':
        raise ValueError('CLASH_RULE_URL_UNSUPPORTED')
    return [line.strip() for line in strings(row.get('fixrule', [])) if line.strip() and not line.lstrip().startswith('#')]


def regex_keywords(items):
    return '|'.join('(?:' + (item[4:] if item.startswith('exp#') else re.escape(item)) + ')' for item in items)


class EditorIssue(ManagementError):
    def __init__(self, code, section, index, field, line=None):
        super().__init__(code)
        self.issue = {'section': section, 'index': index, 'field': field}
        if line is not None:
            self.issue['line'] = line


def check_http_rule_provider(row, index, *, native=False):
    from manage_model import validate
    try:
        validate('ini', 'test_node_url', row['url'])
    except ManagementError:
        raise EditorIssue('INVALID_RULE_PROVIDER_URL', 'rules', index, 'url') from None
    behavior, fmt = row['behavior'], row['format']
    formats = RULE_FORMATS | {'auto'} if native else RULE_FORMATS
    if not isinstance(behavior, str) or behavior not in {'classical', 'domain', 'ipcidr'}:
        raise EditorIssue('INVALID_RULE_BEHAVIOR', 'rules', index, 'behavior')
    if not isinstance(fmt, str) or fmt not in formats or (fmt == 'mrs' and behavior == 'classical'):
        raise EditorIssue('INVALID_RULE_FORMAT', 'rules', index, 'format')
    interval = row['interval']
    if type(interval) is not int or not ((-(2**63) if native else 60) <= interval <= 604800):
        raise EditorIssue('INVALID_POLICY_INTERVAL', 'rules', index, 'interval')
    proxy = row['proxy']
    if not isinstance(proxy, str) or len(proxy) > 200 or any(ord(c) < 32 for c in proxy):
        raise EditorIssue('INVALID_RULE_PROVIDER_PROXY', 'rules', index, 'proxy')


def trimmed_span(rule, start, end):
    while start < end and rule[start].isspace():
        start += 1
    while end > start and rule[end-1].isspace():
        end -= 1
    return start, end


def rule_fields(rule):
    """Locate target and payload using the Mihomo rule-type delimiter rules."""
    first = rule.find(',')
    if first < 1 or not (kind := rule[:first].strip().upper()):
        raise ValueError('INVALID_RULE_LINE')
    if kind == 'MATCH':
        payload = None
        start = first + 1
    else:
        separator = rule.rfind(',') if kind in COMMA_PAYLOAD_RULES else rule.find(',', first + 1)
        if separator <= first:
            raise ValueError('INVALID_RULE_LINE')
        payload = trimmed_span(rule, first + 1, separator)
        if payload[0] == payload[1]:
            raise ValueError('INVALID_RULE_LINE')
        start = separator + 1
    end = -1 if kind in COMMA_PAYLOAD_RULES else rule.find(',', start)
    target = trimmed_span(rule, start, len(rule) if end < 0 else end)
    if target[0] == target[1]:
        raise ValueError('INVALID_RULE_LINE')
    return kind, payload, target


def logical_children(rule, start, end):
    if start >= end or rule[start] != '(' or rule[end-1] != ')':
        raise ValueError('INVALID_RULE_LINE')
    children, depth = [], 0
    for pos in range(start + 1, end - 1):
        char = rule[pos]
        if char == '(':
            if depth == 0:
                child = pos + 1
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise ValueError('INVALID_RULE_LINE')
            if depth == 0:
                children.append((child, pos))
        elif depth == 0 and char != ',' and not char.isspace():
            raise ValueError('INVALID_RULE_LINE')
    if depth != 0 or not children:
        raise ValueError('INVALID_RULE_LINE')
    return children


def rule_provider_spans(rule, kind, payload):
    if payload is None:
        return []
    if kind == 'SUB-RULE':
        # The core wraps a SUB-RULE condition in its own outer parentheses.
        start, end = payload
        if rule[start] != '(' or rule[end-1] != ')':
            raise ValueError('INVALID_RULE_LINE')
        first = rule.find(',', start + 1, end - 1)
        if first < 0:
            raise ValueError('INVALID_RULE_LINE')
        kind, payload = rule[start+1:first].strip().upper(), (first+1, end-1)
    result = []
    pending = [(kind, payload)]
    while pending:
        kind, (start, end) = pending.pop()
        if kind == 'RULE-SET':
            separator = rule.find(',', start, end)
            span = trimmed_span(rule, start, end if separator < 0 else separator)
            if span[0] == span[1]:
                raise ValueError('INVALID_RULE_LINE')
            result.append(span)
        elif kind in LOGIC_RULES:
            start, end = trimmed_span(rule, start, end)
            children = logical_children(rule, start, end)
            if kind == 'NOT' and len(children) != 1:
                raise ValueError('INVALID_RULE_LINE')
            for start, end in reversed(children):
                first = rule.find(',', start, end)
                if first < 0 or not (child_kind := rule[start:first].strip().upper()):
                    raise ValueError('INVALID_RULE_LINE')
                if child_kind in {'MATCH', 'SUB-RULE'} or not rule[first+1:end].strip():
                    raise ValueError('INVALID_RULE_LINE')
                pending.append((child_kind, (first+1, end)))
    return result


def rename_rule(rule, names):
    if not names:
        return rule
    try:
        kind, _, (start, end) = rule_fields(rule)
    except ValueError:
        return rule
    target = rule[start:end]
    return rule[:start] + names[target] + rule[end:] if kind != 'SUB-RULE' and target in names else rule


def rename_rule_providers(rule, names):
    if not names:
        return rule
    try:
        kind, payload, _ = rule_fields(rule)
        spans = rule_provider_spans(rule, kind, payload)
    except ValueError:
        return rule
    for start, end in reversed(spans):
        provider = rule[start:end]
        if provider in names:
            rule = rule[:start] + names[provider] + rule[end:]
    return rule


def rename_references(value, names):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'dialer-proxy', 'proxy'} and isinstance(item, str) and item in names:
                value[key] = names[item]
            else:
                rename_references(item, names)
    elif isinstance(value, list):
        for item in value:
            rename_references(item, names)


def merge_editor(document, baseline, submitted):
    """Apply only changed editor fields to copies of original YAML objects."""
    if not isinstance(submitted, dict):
        raise ValueError('INVALID_EDITOR_CONFIG')
    for key in ('subs', 'node-groups', 'rules'):
        if not isinstance(submitted.get(key, []), list) or len(submitted.get(key, [])) > 5000:
            raise ValueError('INVALID_EDITOR_LIST')
    result = deepcopy(document)
    old_groups = {row['_clash_id']: (row, original) for row, original in
                  zip(baseline['node-groups'], document.get('proxy-groups', []))}
    groups, seen, renames = [], set(), {}
    for row in submitted.get('node-groups', []):
        if not isinstance(row, dict):
            raise ValueError('INVALID_EDITOR_GROUP')
        identity = row.get('_clash_id', '')
        if not isinstance(identity, str) or (identity and (identity not in old_groups or identity in seen)):
            raise ValueError('INVALID_EDITOR_ID')
        seen.add(identity) if identity else None
        old, original = old_groups[identity] if identity else ({'include': [], 'subs': ['all']}, {})
        previous, edited = group_view(old), group_view(row)
        name = edited['name']
        if not isinstance(name, str) or not name or len(name) > 256 or any(c in name for c in '\r\n,'):
            raise ValueError('INVALID_GROUP_NAME')
        group = deepcopy(original)
        group['name'] = name
        if identity and name != original['name']:
            renames[original['name']] = name
        if edited['include'] != previous['include'] or not identity:
            group['proxies'] = list(dict.fromkeys(edited['include']))
        if edited['subs'] != previous['subs'] or not identity:
            for key in ('use', 'include-all', 'include-all-proxies', 'include-all-providers'):
                group.pop(key, None)
            if 'all' in edited['subs']:
                group['include-all-proxies'] = True
                if document.get('proxy-providers'):
                    group['include-all-providers'] = True
            elif edited['subs']:
                if set(edited['subs']) - set(document.get('proxy-providers', {})):
                    raise ValueError('UNKNOWN_PROXY_PROVIDER')
                group['use'] = edited['subs']
        for field, native in (('keywords', 'filter'), ('exclude_keywords', 'exclude-filter')):
            if edited[field] != previous[field]:
                if edited[field]:
                    group[native] = regex_keywords(edited[field])
                else:
                    group.pop(native, None)
        # Pre-proxy is materialized as provider override, not an ignored group field.
        mode_changed = edited['mode'] != previous['mode']
        if not identity or mode_changed:
            group['type'] = 'select' if edited['mode'] == 'smart' else edited['mode']
        if mode_changed:
            if edited['mode'] in {'select', 'smart'} and original.get('type') in {'url-test', 'fallback'}:
                for key in ('url', 'interval'):
                    group.pop(key, None)
            if original.get('type') == 'fallback':
                for key in ('expected-status', 'max-failed-times'):
                    group.pop(key, None)
        if edited['mode'] == 'smart':
            group['proxies'] = list(dict.fromkeys(group.get('proxies', []) + ['smartspeedtest@hide']))
        elif 'proxies' in group and 'smartspeedtest@hide' in group['proxies']:
            group['proxies'].remove('smartspeedtest@hide')
        if edited['mode'] in {'url-test', 'fallback'}:
            if not identity or edited['url'] != previous['url']:
                group['url'] = edited['url'] or 'https://www.youtube.com/generate_204'
            if edited['mode'] == 'url-test' and (not identity or edited['interval'] != previous['interval']):
                if not 30 <= edited['interval'] <= 86400:
                    raise ValueError('INVALID_TEST_INTERVAL')
                group['interval'] = edited['interval']
            if edited['mode'] == 'fallback':
                for field, native in (('expected', 'expected-status'), ('retries', 'max-failed-times')):
                    if edited[field] != previous[field]:
                        group[native] = edited[field]
        groups.append(group)
    if len({row['name'] for row in groups}) != len(groups):
        raise ValueError('DUPLICATE_GROUP_NAME')
    old_rules = {row['_clash_id']: row for row in baseline['rules']}
    providers, lines, seen, provider_renames = {}, [], set(), {}
    for index, row in enumerate(submitted.get('rules', [])):
        if not isinstance(row, dict):
            raise ValueError('INVALID_EDITOR_RULE')
        identity = row.get('_clash_id', '')
        if not isinstance(identity, str) or (identity and (identity not in old_rules or identity in seen)):
            raise ValueError('INVALID_EDITOR_ID')
        seen.add(identity) if identity else None
        if row.get('type') == 'rule-set':
            old = old_rules.get(identity, {})
            if identity and old.get('type') != 'rule-set':
                raise ValueError('CLASH_RULE_KIND_CHANGED')
            previous, edited = provider_view(old), provider_view(row)
            original = document.get('rule-providers', {}).get(old.get('name'), {})
            provider = deepcopy(original) if identity else {'type': 'http'}
            name = edited['name']
            if (not isinstance(name, str) or not name.strip() or name != name.strip()
                    or len(name) > 200 or any(c in name for c in '\n\r,')):
                raise EditorIssue('INVALID_RULE_PROVIDER_NAME', 'rules', index, 'name')
            if name in providers:
                raise EditorIssue('DUPLICATE_RULE_PROVIDER', 'rules', index, 'name')
            changed = [key for key in ('url', 'behavior', 'format', 'interval', 'proxy')
                       if not identity or edited[key] != previous[key]]
            if provider.get('type', 'http') != 'http':
                if changed:
                    raise EditorIssue('UNSUPPORTED_RULE_PROVIDER_TYPE', 'rules', index, changed[0])
            elif changed or edited['format'] == 'auto':
                check_http_rule_provider(edited, index)
                for key in changed:
                    if key == 'proxy' and not edited[key]:
                        provider.pop(key, None)
                    else:
                        provider[key] = edited[key]
            providers[name] = provider
            if identity and name != old['name']:
                provider_renames[old['name']] = name
        else:
            if identity and old_rules[identity].get('type') == 'rule-set':
                raise ValueError('CLASH_RULE_KIND_CHANGED')
            try:
                lines.extend(rule_lines(row))
            except ValueError as error:
                code = str(error)
                field = 'url' if code == 'CLASH_RULE_URL_UNSUPPORTED' else 'fixrule'
                raise EditorIssue(code, 'rules', index, field) from None
    if groups != document.get('proxy-groups', []):
        result['proxy-groups'] = groups
    if providers != document.get('rule-providers', {}):
        result['rule-providers'] = providers
    # Whitespace-only normalization must not cause a policy rewrite.
    if lines != [line.strip() for line in document.get('rules', []) if line.strip()]:
        result['rules'] = lines
    for group in result.get('proxy-groups', []):
        if 'proxies' in group:
            group['proxies'] = [renames.get(name, name) for name in group['proxies']]
    if 'rules' in result:
        result['rules'] = [rename_rule_providers(rename_rule(line, renames), provider_renames)
                           for line in result['rules']]
    rename_references(result, renames)
    source_url = None
    old_subs = {row.get('_clash_id', row['name']): row for row in baseline.get('subs', [])}
    new_subs = submitted.get('subs', [])
    if len(new_subs) != len(old_subs):
        raise ValueError('CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED')
    for row in new_subs:
        if not isinstance(row, dict):
            raise ValueError('CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED')
        key = row.get('_clash_id', row.get('name'))
        if not isinstance(key, str) or key not in old_subs:
            raise ValueError('CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED')
        old = old_subs[key]
        if row.get('name') != old.get('name'):
            raise ValueError('CLASH_SUBSCRIPTION_RENAME_UNSUPPORTED')
        if row.get('url') != old.get('url'):
            url = row.get('url', '')
            from urllib.parse import urlsplit
            target = urlsplit(url)
            if target.scheme not in {'https', 'http'} or not target.hostname or target.username or target.password or target.fragment or any(ord(c) < 33 for c in url):
                raise ValueError('INVALID_URL')
            if old['name'] in result.get('proxy-providers', {}):
                result['proxy-providers'][old['name']]['url'] = url
            else:
                source_url = url
    return result, source_url, renames


def feature_options(config):
    result = {key: deepcopy(config.get(key) or {'enable': False})
              for key in ('global_monitor', 'restart_cron', 'shadowsocks_in')}
    for key, value in list(result.items()):
        if not value.get('enable'): result[key] = {'enable': False}
    result['node-groups'] = [{key: row[key] for key in ('name', 'smart', 'speedtest_url', 'use_pre_proxy', 'pre_proxy_group') if key in row}
                             for row in config.get('node-groups', []) if row.get('smart') or row.get('use_pre_proxy')]
    names = {row.get('name') for row in config.get('node-groups', [])} | {'DIRECT'}
    for row in result['node-groups']:
        if row.get('use_pre_proxy') and (row.get('pre_proxy_group') not in names or row.get('pre_proxy_group') == row.get('name')):
            raise ValueError('INVALID_PRE_PROXY')
    restart = result['restart_cron']
    if restart.get('enable') and (type(restart.get('hour')) is not int or not 0 <= restart['hour'] <= 23):
        raise ValueError('INVALID_RESTART_HOUR')
    ss = result['shadowsocks_in']
    if ss.get('enable'):
        import base64
        if ss.get('cipher') not in {'aes-128-gcm', '2022-blake3-aes-128-gcm'} or not isinstance(ss.get('password'), str) or len(ss['password']) < 16:
            raise ValueError('INVALID_SS_CONFIG')
        if ss['cipher'].startswith('2022-'):
            try:
                if len(base64.b64decode(ss['password'], validate=True)) < 16: raise ValueError()
            except Exception: raise ValueError('INVALID_SS_CONFIG') from None
    return result


def apply_features(document, features):
    result = logical_document(document)
    managed = {'schema': 1, 'groups': {}, 'providers': [], 'absent_sections': [
        key for key in ('proxy-providers', 'proxies', 'listeners') if key not in result]}
    if any(group.get('smart') for group in features.get('node-groups', [])):
        proxies = result.setdefault('proxies', [])
        if not any(row.get('name') == 'smartspeedtest@hide' for row in proxies):
            proxies.append({'name': 'smartspeedtest@hide', 'type': 'reject'})
            managed['smart_proxy'] = True
    inline = {row['name']: row for row in result.get('proxies', []) if isinstance(row, dict) and row.get('name')}
    original_providers = deepcopy(result.get('proxy-providers', {}))
    options = {row['name']: row for row in features.get('node-groups', [])}
    for group in result.get('proxy-groups', []):
        feature = options.get(group['name'], {})
        if not feature.get('use_pre_proxy'): continue
        managed['groups'][group['name']] = deepcopy(group)
        prefix = 'ppgw-chain-' + hashlib.sha256(group['name'].encode()).hexdigest()[:12]
        provider_names = list(original_providers) if group.get('include-all') or group.get('include-all-providers') else list(group.get('use', []))
        selected = list(inline) if group.get('include-all') or group.get('include-all-proxies') else list(group.get('proxies', []))
        payload = [deepcopy(inline[name]) for name in selected if name in inline]
        generated = []
        providers = result.setdefault('proxy-providers', {})
        for index, provider_name in enumerate(provider_names):
            if provider_name not in original_providers: raise ValueError('UNKNOWN_PROXY_PROVIDER')
            name = prefix + '-' + str(index)
            if name in providers: raise ValueError('RESERVED_PROVIDER_NAME')
            provider = deepcopy(original_providers[provider_name])
            provider['override'] = {**provider.get('override', {}), 'dialer-proxy': feature['pre_proxy_group']}
            if provider.get('path'):
                provider['path'] = './providers/' + name + '.yaml'
            providers[name] = provider; generated.append(name)
        if payload:
            name = prefix + '-inline'
            if name in providers: raise ValueError('RESERVED_PROVIDER_NAME')
            providers[name] = {'type': 'inline', 'payload': payload,
                               'override': {'dialer-proxy': feature['pre_proxy_group']}}
            generated.append(name)
        for key in ('include-all', 'include-all-proxies', 'include-all-providers'):
            group.pop(key, None)
        group['proxies'] = [name for name in group.get('proxies', []) if name not in inline]
        group['use'] = generated
        managed['providers'].extend(generated)
    listeners = result.get('listeners', [])
    ss = features.get('shadowsocks_in') or {}
    if ss.get('enable'):
        listeners = [row for row in listeners if row.get('name') != 'ppgw-shadowsocks-in']
        listeners.append({'name': 'ppgw-shadowsocks-in', 'type': 'shadowsocks', 'listen': '127.0.0.1',
                          'port': 8080, 'udp': True, 'password': ss['password'], 'cipher': ss['cipher']})
        result['listeners'] = listeners
        managed['ss_listener'] = True
    elif any(row.get('name') == 'ppgw-shadowsocks-in' for row in listeners):
        result['listeners'] = [row for row in listeners if row.get('name') != 'ppgw-shadowsocks-in']
    if managed['groups'] or managed['providers'] or managed.get('smart_proxy') or managed.get('ss_listener'):
        result['x-ppgw-features'] = managed
    return result
