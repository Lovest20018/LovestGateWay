"""Conservative Mihomo policy reloads, verified through its private Unix socket.

Provider reloads, credential/transport changes and gateway lifecycle changes keep
the restart transaction. A failed or uncertain reload raises to that transaction,
which restores persistent inputs and restarts the previously accepted config.
"""
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import re
import socket
import struct
import time
from urllib.parse import quote

from manage_common import ManagementError

RULE_TYPES = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6',
              'SRC-IP-CIDR', 'SRC-PORT', 'DST-PORT', 'NETWORK', 'MATCH'}
GROUP_TYPES = {'select': 'Selector', 'url-test': 'URLTest', 'fallback': 'Fallback',
               'load-balance': 'LoadBalance'}
GROUP_OPTIONS = {'name', 'type', 'proxies', 'url', 'interval', 'lazy', 'hidden', 'tolerance',
                 'expected-status', 'max-failed-times', 'strategy'}


def rule_rows(document):
    result = []
    for rule in document.get('rules', []):
        if not isinstance(rule, str): raise ValueError('unsupported rule')
        parts = [part.strip() for part in rule.split(',')]
        kind = parts[0].upper()
        count = 2 if kind == 'MATCH' else 3
        if kind not in RULE_TYPES or len(parts) not in {count, count+1}:
            raise ValueError('unsupported rule')
        if len(parts) > count and parts[-1] != 'no-resolve': raise ValueError('unsupported rule')
        payload = '' if kind == 'MATCH' else parts[1]
        if kind == 'NETWORK': payload = payload.lower()
        normalized = 'ipcidr' if kind == 'IP-CIDR6' else re.sub('[^a-z0-9]', '', kind.lower())
        result.append((normalized, payload, parts[count-1]))
    return result


def plan(before, after):
    """An allowlist of proven changes, never a claim that arbitrary YAML is safe."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {'eligible': False, 'reason': 'snapshot_unavailable'}
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    if not changed: return {'eligible': False, 'reason': 'unchanged'}
    if changed - {'rules', 'proxy-groups'}:
        return {'eligible': False, 'reason': 'transport_credentials_or_features'}
    if (before.get('x-ppgw-features') or after.get('x-ppgw-features')) and changed != {'rules'}:
        return {'eligible': False, 'reason': 'managed_groups_require_restart'}
    if any(doc.get(key) for doc in (before, after) for key in ('proxy-providers', 'rule-providers', 'sub-rules')):
        return {'eligible': False, 'reason': 'provider_or_subrule_requires_restart'}
    try:
        rule_rows(before); rule_rows(after)
        for doc in (before, after):
            groups = doc.get('proxy-groups', [])
            if not isinstance(groups, list): raise ValueError()
            names = []
            for group in groups:
                if (not isinstance(group, dict) or not isinstance(group.get('name'), str)
                        or set(group) - GROUP_OPTIONS
                        or group.get('type') not in GROUP_TYPES or group.get('use')
                        or any(group.get(key) for key in ('include-all', 'include-all-proxies', 'include-all-providers'))
                        or not isinstance(group.get('proxies'), list)
                        or not all(isinstance(name, str) for name in group['proxies'])): raise ValueError()
                names.append(group['name'])
            if len(names) != len(set(names)): raise ValueError()
    except (TypeError, ValueError, IndexError):
        return {'eligible': False, 'reason': 'unsupported_rule_or_group'}
    return {'eligible': True, 'reason': 'static_policy_only'}


def policy_candidate(before, after, old_features, features, materialized=None):
    """Use identical physical representations and never widen the runtime allowlist."""
    from clash_ppsub import apply_features, logical_document
    if features != old_features:
        return {'eligible': False, 'reason': 'managed_features_changed'}, None
    expected = apply_features(before, old_features)
    raw = expected if materialized is None else materialized
    if logical_document(raw) != before or raw != expected:
        return {'eligible': False, 'reason': 'materialized_snapshot_changed'}, None
    result = plan(raw, after)
    return result, {'before': deepcopy(raw), 'after': deepcopy(after)} if result['eligible'] else None


class UnixController:
    def __init__(self, path): self.path = str(path)
    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('localhost', timeout=8)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(8)
        try:
            connection.sock.connect(self.path)
            peer = struct.unpack('3i', connection.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            identity = (peer[0], Path('/proc/'+str(peer[0])+'/stat').read_text().rsplit(')', 1)[1].split()[19])
            connection.request(method, path, body=None if body is None else json.dumps(body),
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse(); raw = response.read(8*1024*1024+1)
            if response.status not in {200, 204} or len(raw) > 8*1024*1024:
                raise ManagementError('MIHOMO_RELOAD_RESPONSE_FAILED')
            return (json.loads(raw) if raw else None), identity
        finally: connection.close()


def reload_verified(before, after, controller, validate, persist):
    if not plan(before, after)['eligible']: raise ManagementError('MIHOMO_RELOAD_UNSUPPORTED')
    config, identity = controller.request('GET', '/configs')
    proxies, peer = controller.request('GET', '/proxies')
    if peer != identity or config.get('mode') not in {'rule', 'global', 'direct'}:
        raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    previous = proxies.get('proxies', {})
    rules, peer = controller.request('GET', '/rules')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    def observed_rules(value):
        return [(re.sub('[^a-z0-9]', '', str(row.get('type', '')).lower()), str(row.get('payload', '')), row.get('proxy'))
                for row in value.get('rules', [])]
    if observed_rules(rules) != rule_rows(before): raise ManagementError('STALE_POLICY_SNAPSHOT')
    for group in before.get('proxy-groups', []):
        row = previous.get(group['name'], {})
        if row.get('type') != GROUP_TYPES[group['type']] or row.get('all') != group['proxies']:
            raise ManagementError('STALE_POLICY_SNAPSHOT')
    choices = {name: row.get('now') for name, row in previous.items()
               if isinstance(row, dict) and row.get('type') == 'Selector' and isinstance(row.get('now'), str)}
    locks = {name: (row.get('type'), row['fixed']) for name, row in previous.items()
             if isinstance(row, dict) and row.get('type') in {'URLTest', 'Fallback', 'LoadBalance'}
             and isinstance(row.get('fixed'), str) and row['fixed']}
    candidate = deepcopy(after); candidate['mode'] = config['mode']
    validate(candidate)
    # Recheck ownership immediately before the only mutating controller call.
    _, peer = controller.request('GET', '/version')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    _, peer = controller.request('PUT', '/configs?force=false', {'payload': json.dumps(candidate)})
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    current, peer = controller.request('GET', '/proxies')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    actual = current.get('proxies', {})
    for name, selected in choices.items():
        row = actual.get(name, {})
        if row.get('type') == 'Selector' and selected in row.get('all', []) and row.get('now') != selected:
            _, peer = controller.request('PUT', '/proxies/'+quote(name, safe=''), {'name': selected})
            if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    for name, (kind, selected) in locks.items():
        row = actual.get(name, {})
        if row.get('type') == kind and selected in row.get('all', []) and row.get('fixed') != selected:
            _, peer = controller.request('PUT', '/proxies/'+quote(name, safe=''), {'name': selected})
            if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    current, peer = controller.request('GET', '/proxies')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    actual = current.get('proxies', {})
    for group in candidate.get('proxy-groups', []):
        row = actual.get(group['name'], {})
        if row.get('type') != GROUP_TYPES[group['type']] or row.get('all') != group['proxies']:
            raise ManagementError('MIHOMO_RELOAD_GROUP_NOT_LOADED')
    for name, selected in choices.items():
        row = actual.get(name, {})
        if row.get('type') == 'Selector' and selected in row.get('all', []) and row.get('now') != selected:
            raise ManagementError('MIHOMO_RELOAD_SELECTION_NOT_PRESERVED')
    for name, (kind, selected) in locks.items():
        row = actual.get(name, {})
        if row.get('type') == kind and selected in row.get('all', []) and row.get('fixed') != selected:
            raise ManagementError('MIHOMO_RELOAD_SELECTION_NOT_PRESERVED')
    rules, peer = controller.request('GET', '/rules')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    if observed_rules(rules) != rule_rows(candidate): raise ManagementError('MIHOMO_RELOAD_RULES_NOT_LOADED')
    loaded, peer = controller.request('GET', '/configs')
    if peer != identity or loaded.get('mode') != config['mode']:
        raise ManagementError('MIHOMO_RELOAD_MODE_NOT_PRESERVED')
    persist(candidate)
    _, peer = controller.request('GET', '/version')
    if peer != identity: raise ManagementError('MIHOMO_RELOAD_IDENTITY_CHANGED')
    return {'method': 'mihomo_reload', 'verified': ['process_identity', 'rules', 'groups', 'mode', 'selections'],
            'connection_continuity': 'not_measured', 'provider_refresh': False}


def apply_policy(system, update, settings):
    import yaml
    from clash_ppsub import source_identity
    from clash_policy import protected_revision
    from manage_common import atomic_write, write_json, read_bytes
    pid = system.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
    if not pid.isascii() or not pid.isdigit() or int(pid) <= 0: raise ManagementError('CORE_NOT_RUNNING')
    target = Path('/proc')/pid/'root/tmp/clash.yaml'; original = target.read_bytes()
    if len(original) > 2*1024*1024: raise ManagementError('CONFIG_TOO_LARGE')
    raw = yaml.safe_load(original)
    if raw != update['before']: raise ManagementError('STALE_POLICY_SNAPSHOT')
    policy = json.loads(read_bytes(system.layout, 'etc/paopaogw/files/clash-policy.json'))
    status = json.loads(read_bytes(system.layout, 'var/lib/paopaogw/admin/policy-status.json', b'{}'))
    key = read_bytes(system.layout, 'etc/paopaogw/revision-key', b'').strip()
    if not key or status.get('status') != 'loaded' or status.get('source_identity') != source_identity(settings['ini']):
        raise ManagementError('SOURCE_NOT_CONFIRMED')
    def validate(document):
        if target.read_bytes() != original: raise ManagementError('STALE_POLICY_SNAPSHOT')
        system.validate_policy(document)
        if target.read_bytes() != original: raise ManagementError('STALE_POLICY_SNAPSHOT')
    def persist(document):
        if target.read_bytes() != original: raise ManagementError('STALE_POLICY_SNAPSHOT')
        # The transaction journal is already durable. Any failure below uses its
        # existing conflict-aware rollback and restarts the accepted persistent inputs.
        data = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode()
        pending = target.with_name('clash.yaml.reload-new')
        try:
            descriptor = os.open(pending, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if target.read_bytes() != original: raise ManagementError('STALE_POLICY_SNAPSHOT')
            pending.replace(target)
        finally: pending.unlink(missing_ok=True)
        rule_hash = system.command('nsenter', '-t', pid, '-m', '--', '/usr/local/lib/paopaogw/bin/sniffbox',
                                   'ppgw', '-rulehash', '-yaml', '/tmp/clash.yaml').stdout.strip()
        if not re.fullmatch('[a-f0-9]{32,64}', rule_hash): raise ManagementError('MIHOMO_RELOAD_HASH_FAILED')
        # Keep the native health/update loop's accepted baseline in step. A
        # later identical refresh must not close connections for these old rules.
        previous = target.with_name('clash.yaml.last'); previous.write_bytes(data); previous.chmod(0o600)
        state = target.with_name('ppgw_state.last')
        lines = state.read_text().splitlines() if state.exists() else []
        state.write_text('last_rule_hash='+rule_hash+'\n'+'\n'.join(line for line in lines if not line.startswith('last_rule_hash='))+'\n')
        state.chmod(0o600)
        atomic_write(system.layout, 'var/lib/paopaogw/admin/clash.last-good.yaml', data)
        write_json(system.layout, 'var/lib/paopaogw/admin/policy-status.json', {
            **status, 'status': 'loaded', 'policy_revision': protected_revision(policy, key),
            'effective_revision': protected_revision(document, key), 'apply_method': 'mihomo_reload',
            'loaded_at': time.time(), 'local_sections': list(policy.get('sections', {})),
            'policy_matches_source': policy.get('source_identity') == source_identity(settings['ini'])})
    return reload_verified(update['before'], update['after'], UnixController(Path('/proc')/pid/'root/tmp/clash.sock'), validate, persist)
