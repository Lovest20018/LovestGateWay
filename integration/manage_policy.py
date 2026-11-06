"""Native Clash policy editing: bounded snapshots, retained fields and shared transactions."""
from collections import OrderedDict
from copy import deepcopy
import json
import secrets
import threading
import time
from manage_common import ManagementError, operation_lock, read_bytes
from manage_config import ConfigManager
from manage_model import load_settings, settings_revision
from clash_policy import build_policy
from clash_ppsub import fingerprint, running_document, source_identity, rename_rule, rename_references, apply_features

POLICY = 'etc/paopaogw/files/clash-policy.json'
GROUP_FIELDS = ('name', 'type', 'proxies', 'use', 'url', 'interval')
PROVIDER_FIELDS = ('name', 'url', 'behavior', 'format', 'interval', 'proxy')
GROUP_TYPES = {'select', 'url-test', 'fallback', 'load-balance'}


def text(value, maximum=8192):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ManagementError('INVALID_POLICY_VALUE')
    return value


def name(value):
    value = text(value, 200)
    if not value or value != value.strip() or ',' in value:
        raise ManagementError('INVALID_POLICY_NAME')
    return value


def strings(value):
    if not isinstance(value, list) or len(value) > 5000:
        raise ManagementError('INVALID_POLICY_LIST')
    return [text(item) for item in value]


def group_view(row, identifier):
    return {'id': identifier, 'name': row['name'], 'type': row.get('type', 'select'),
            'proxies': deepcopy(row.get('proxies', [])), 'use': deepcopy(row.get('use', [])),
            'url': row.get('url', ''), 'interval': row.get('interval', 600)}


def provider_view(key, row):
    return {'id': key, 'name': key, 'url': row.get('url', ''), 'behavior': row.get('behavior', 'classical'),
            'format': row.get('format', 'yaml'), 'interval': row.get('interval', 86400),
            'proxy': row.get('proxy', ''), 'type': row.get('type', 'http')}


def merge_native(document, submitted):
    if not isinstance(submitted, dict) or set(submitted) != {'groups', 'providers', 'rules'}:
        raise ManagementError('INVALID_POLICY_PATCH')
    for key in ('groups', 'providers'):
        if not isinstance(submitted[key], list) or len(submitted[key]) > 500:
            raise ManagementError('INVALID_POLICY_LIST')
    result = deepcopy(document)
    originals = document.get('proxy-groups', [])
    groups, seen, renames = [], set(), {}
    for row in submitted['groups']:
        if not isinstance(row, dict) or set(row) != {'id', *GROUP_FIELDS}:
            raise ManagementError('INVALID_POLICY_GROUP')
        identifier = text(row['id'], 20)
        if identifier:
            if not identifier.isdigit() or str(int(identifier)) != identifier or int(identifier) >= len(originals) or identifier in seen:
                raise ManagementError('STALE_POLICY_SNAPSHOT')
            seen.add(identifier)
            original = originals[int(identifier)]
            previous = group_view(original, identifier)
        else:
            original, previous = {}, {}
        group = deepcopy(original)
        name(row['name']); strings(row['proxies']); strings(row['use']); text(row['url'])
        if row['url']:
            from manage_model import validate
            validate('ini', 'test_node_url', row['url'])
        if type(row['interval']) is not int or not 30 <= row['interval'] <= 86400:
            raise ManagementError('INVALID_POLICY_INTERVAL')
        if row['type'] != previous.get('type') and row['type'] not in GROUP_TYPES:
            raise ManagementError('UNSUPPORTED_GROUP_TYPE')
        for key in GROUP_FIELDS:
            if not identifier or row[key] != previous[key]:
                if key in {'use', 'url'} and not row[key]: group.pop(key, None)
                else: group[key] = deepcopy(row[key])
        if original and row['name'] != original['name']:
            renames[original['name']] = row['name']
        groups.append(group)
    if len({row['name'] for row in groups}) != len(groups):
        raise ManagementError('DUPLICATE_GROUP_NAME')
    providers, seen, provider_renames = {}, set(), {}
    old_providers = document.get('rule-providers', {})
    for row in submitted['providers']:
        if not isinstance(row, dict) or set(row) != {'id', 'type', *PROVIDER_FIELDS}:
            raise ManagementError('INVALID_RULE_PROVIDER')
        identifier = text(row['id'], 200)
        if identifier and (identifier not in old_providers or identifier in seen):
            raise ManagementError('STALE_POLICY_SNAPSHOT')
        seen.add(identifier)
        original = old_providers.get(identifier, {})
        previous = provider_view(identifier, original) if identifier else {}
        provider = deepcopy(original) if identifier else {'type': 'http'}
        key = name(row['name'])
        if key in providers: raise ManagementError('DUPLICATE_RULE_PROVIDER')
        if row['type'] != original.get('type', 'http'):
            raise ManagementError('UNSUPPORTED_RULE_PROVIDER_TYPE')
        # File/inline provider payloads remain server-side; their names may still be changed.
        if row['type'] != 'http' and any(row[k] != previous[k] for k in PROVIDER_FIELDS if k != 'name'):
            raise ManagementError('UNSUPPORTED_RULE_PROVIDER_TYPE')
        if row['type'] == 'http':
            from manage_model import validate
            validate('ini', 'test_node_url', row['url'])
            if row['behavior'] not in {'domain', 'ipcidr', 'classical'} or row['format'] not in {'yaml', 'text', 'mrs'}:
                raise ManagementError('INVALID_RULE_PROVIDER')
            if type(row['interval']) is not int or not 60 <= row['interval'] <= 604800:
                raise ManagementError('INVALID_POLICY_INTERVAL')
            text(row['proxy'], 200)
        for field in PROVIDER_FIELDS[1:]:
            if not identifier or row[field] != previous[field]:
                if field == 'proxy' and not row[field]: provider.pop(field, None)
                else: provider[field] = row[field]
        providers[key] = provider
        if identifier and identifier != key: provider_renames[identifier] = key
    rules = strings(submitted['rules'])
    if any(len(rule) > 8192 for rule in rules): raise ManagementError('INVALID_POLICY_RULE')
    rules = [rule.strip() for rule in rules if rule.strip()]
    # Keep absent sections and untouched hidden options byte-for-byte equivalent as data.
    if groups != originals: result['proxy-groups'] = groups
    if providers != old_providers: result['rule-providers'] = providers
    current_rules = document.get('rules', [])
    if rules != current_rules: result['rules'] = rules
    rename_references(result, renames)
    for group in result.get('proxy-groups', []):
        if 'proxies' in group:
            group['proxies'] = [renames.get(item, item) for item in group['proxies']]
    result_rules = [rename_rule(rule, renames) for rule in result.get('rules', [])]
    for index, rule in enumerate(result_rules):
        parts = rule.split(',')
        if len(parts) >= 2 and parts[0].strip().upper() == 'RULE-SET' and parts[1].strip() in provider_renames:
            parts[1] = provider_renames[parts[1].strip()]
            result_rules[index] = ','.join(parts)
    if result_rules != result.get('rules', []): result['rules'] = result_rules
    return result, renames


class PolicyManager:
    def __init__(self, layout, system, reader=None):
        self.layout, self.system = layout, system
        self.reader = reader or running_document
        self.snapshots = OrderedDict()
        self.lock = threading.Lock()

    def show(self):
        settings = load_settings(self.layout)
        if settings['ini']['mode'] not in {'yaml', 'suburl'}:
            return {'available': False, 'reason': 'MIHOMO_MODE_REQUIRED'}
        document = self.reader()
        if not isinstance(document, dict): raise ManagementError('POLICY_UNAVAILABLE')
        groups = document.get('proxy-groups', [])
        providers = document.get('rule-providers', {})
        if not isinstance(groups, list) or not isinstance(providers, dict):
            raise ManagementError('INVALID_POLICY_DOCUMENT')
        identifier = secrets.token_hex(16)
        revision = settings_revision(self.layout)
        with self.lock:
            self.snapshots[identifier] = {'document': deepcopy(document), 'revision': revision, 'created': time.monotonic()}
            while len(self.snapshots) > 16: self.snapshots.popitem(last=False)
        return {'available': True, 'snapshot_id': identifier, 'base_revision': revision,
                'groups': [group_view(row, str(index)) for index, row in enumerate(groups)],
                'providers': [provider_view(key, row) for key, row in providers.items()],
                'rules': list(document.get('rules', [])),
                'node_names': [row['name'] for row in document.get('proxies', []) if isinstance(row, dict) and 'name' in row],
                'source_names': list((document.get('proxy-providers') or {}).keys()),
                'has_local_policy': self.layout.path(POLICY).exists()}

    def candidate(self, body):
        if not isinstance(body, dict) or set(body) != {'snapshot_id', 'base_revision', 'policy'}:
            raise ManagementError('INVALID_POLICY_REQUEST')
        identifier = body['snapshot_id']
        if not isinstance(identifier, str): raise ManagementError('STALE_POLICY_SNAPSHOT')
        with self.lock:
            snapshot = deepcopy(self.snapshots.get(identifier))
        if not snapshot or time.monotonic() - snapshot['created'] > 1800:
            raise ManagementError('STALE_POLICY_SNAPSHOT')
        if body['base_revision'] != snapshot['revision'] or settings_revision(self.layout) != snapshot['revision']:
            raise ManagementError('CONFIG_REVISION_CONFLICT')
        if fingerprint(self.reader()) != fingerprint(snapshot['document']):
            raise ManagementError('STALE_POLICY_SNAPSHOT')
        merged, renames = merge_native(snapshot['document'], body['policy'])
        original = read_bytes(self.layout, POLICY)
        existing = json.loads(original) if original else None
        identity = source_identity(load_settings(self.layout)['ini'])
        policy = build_policy(snapshot['document'], merged, existing, identity, renames)
        features = policy.get('features') or {}
        # Feature group names must follow a native group rename as well.
        for group in features.get('node-groups', []):
            group['name'] = renames.get(group.get('name'), group.get('name'))
            if group.get('pre_proxy_group') in renames:
                group['pre_proxy_group'] = renames[group['pre_proxy_group']]
        return snapshot, merged, policy, apply_features(merged, features)

    def preview(self, body):
        with operation_lock(self.layout):
            snapshot, merged, policy, effective = self.candidate(body)
            changed = merged != snapshot['document']
            if changed: self.system.validate_policy(effective)
            from mihomo_reload import plan
            reload_plan = plan(snapshot['document'], effective)
            return {'status': 'preview', 'action': ('mihomo_reload' if reload_plan['eligible'] else 'restart') if changed else 'none',
                    'mihomo_reload': reload_plan,
                    'base_revision': snapshot['revision'], 'changed': changed,
                    'counts': {'groups': len(merged.get('proxy-groups', [])), 'rules': len(merged.get('rules', [])),
                               'providers': len(merged.get('rule-providers', {}))}}

    def apply(self, body):
        with operation_lock(self.layout):
            snapshot, merged, policy, effective = self.candidate(body)
            if merged == snapshot['document']: return {'status': 'unchanged', 'revision': snapshot['revision']}
            self.system.validate_policy(effective)
            def verify():
                current = self.reader()
                if any(current.get(key) != merged.get(key) for key in policy['sections']):
                    raise ManagementError('POLICY_NOT_LOADED')
            from mihomo_reload import plan
            options = {'mihomo_update': {'before': snapshot['document'], 'after': effective}} if plan(snapshot['document'], effective)['eligible'] else {}
            result = ConfigManager(self.layout, self.system).apply({}, snapshot['revision'],
                policy_update=(json.dumps(policy, ensure_ascii=False, indent=2) + '\n').encode(), verify=verify, **options)
            with self.lock: self.snapshots.pop(body['snapshot_id'], None)
            return result

    def reset(self, body):
        if not isinstance(body, dict) or set(body) != {'base_revision'}:
            raise ManagementError('INVALID_POLICY_REQUEST')
        with operation_lock(self.layout):
            if settings_revision(self.layout) != body['base_revision']:
                raise ManagementError('CONFIG_REVISION_CONFLICT')
            if not self.layout.path(POLICY).exists(): return {'status': 'unchanged'}
            return ConfigManager(self.layout, self.system).apply({}, body['base_revision'], policy_update=False)
