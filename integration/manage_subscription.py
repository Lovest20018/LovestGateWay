"""Fetch PPsub nodes in isolation, then publish and verify them with rollback."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from manage_common import ManagementError, atomic_write
from manage_model import load_settings


def payload_content(raw):
    """Compare complete ordered nodes and hosts, ignoring fetch/account metadata.

    Unknown payloads compare byte-for-byte; uncertainty must cause an apply.
    """
    import yaml
    value = json.loads(raw)
    text = value['raw_yaml']
    try:
        document = yaml.compose(text)
        def canonical(node, seen):
            if id(node) in seen: raise ValueError('recursive YAML')
            seen = seen | {id(node)}
            if isinstance(node, yaml.ScalarNode): return ['scalar', node.tag, node.value]
            if isinstance(node, yaml.SequenceNode): return ['sequence', [canonical(n, seen) for n in node.value]]
            if isinstance(node, yaml.MappingNode):
                # Retain duplicate keys and their order: loaders can disagree on
                # which value wins. Only unique mappings may ignore key order.
                pairs = [[canonical(k, seen), canonical(v, seen)] for k, v in node.value]
                keys = [json.dumps(k) for k, v in pairs]
                if len(set(keys)) != len(keys): raise ValueError('duplicate YAML key')
                return ['mapping', sorted(pairs, key=lambda pair: json.dumps(pair[0]))]
            raise ValueError('unknown YAML node')
        if isinstance(document, yaml.MappingNode):
            pairs = {key.value: value for key, value in document.value if isinstance(key, yaml.ScalarNode)}
            if len(pairs) != len(document.value): raise ValueError('duplicate YAML key')
            if isinstance(pairs.get('proxies'), yaml.SequenceNode) and pairs['proxies'].value:
                return json.dumps({k: canonical(pairs[k], set()) if k in pairs else None
                                   for k in ('proxies', 'hosts')}, ensure_ascii=False)
    except (ValueError, TypeError, RecursionError, yaml.YAMLError): pass
    return text


def refresh_nodes(layout, system, names=None, progress=None):
    from manage_subscription_schedule import providers, source_path, record_attempt, record_results, identity
    progress = progress or (lambda stage: None)
    settings = load_settings(layout)
    configured = providers(layout)
    selected = [row['name'] for row in configured] if names is None else names
    if (not isinstance(selected, list) or not selected or any(not isinstance(n, str) for n in selected)
            or len(set(selected)) != len(selected) or set(selected) - {row['name'] for row in configured}):
        raise ManagementError('INVALID_EDITOR_REQUEST')
    pid = system.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
    if not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
        raise ManagementError('CORE_NOT_RUNNING')
    cache = layout.path('var/lib/paopaogw/subscriptions')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    original = {p.name: p.read_bytes() for p in cache.glob('*.json')
                if re.fullmatch(r'(?:[0-9a-f]{64}|status)\.json', p.name)}
    candidates = layout.path('var/lib/paopaogw/admin/candidates')
    candidates.mkdir(parents=True, exist_ok=True, mode=0o700)
    published = False
    with tempfile.TemporaryDirectory(prefix='subscription-', dir=candidates) as name:
        work = Path(name)
        stage = work / 'cache'
        stage.mkdir(mode=0o700)
        for filename, raw in original.items():
            target = stage / filename
            target.write_bytes(raw)
            target.chmod(0o600)
        source = source_path(layout)
        output = work / 'candidate.yaml'
        merged = work / 'validated.yaml'
        env = dict(os.environ, PPGW_SUB_CACHE_DIR=str(stage), PPGW_SUB_CACHE_ONLY='0',
                   PPGW_SUB_REFRESH_NAMES=json.dumps(selected, ensure_ascii=False),
                   ppgw_retry_dir=str(work / 'retry'), dns_burn='no')
        prefix = ['nsenter', '-t', pid, '-m', '--']
        code = layout.path('usr/local/lib/paopaogw/bin')
        record_attempt(layout, configured, selected)
        phase = 'fetching'
        try:
            progress('fetching')
            # No controller script runs in another service's cgroup. The live
            # controller keeps forwarding while these downloads are validated.
            subprocess.run(prefix + [str(code / 'sniffbox'), 'ppgw', '-ppsub',
                str(source), '-output', str(output)],
                env=env, check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            progress('validating')
            phase = 'validating'
            # Match the controller's merge, including its existing geodata mode,
            # listeners and paths. A raw PPsub fragment lacks those defaults.
            subprocess.run(prefix + [str(code / 'sniffbox'), 'ppgw', '-input', str(output),
                '-input', '/tmp/clash_base.yaml', '-output', str(merged)],
                check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(prefix + [str(code / 'clash'), '-t', '-d', '/etc/config/clash', '-f', str(merged)],
                check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            status = json.loads((stage / 'status.json').read_bytes())
            rows = status.get('subscriptions', [])
            # A cache-only read of unselected providers must preserve their last update result.
            old_status = json.loads(original.get('status.json', b'{}'))
            old_rows = {row['name']: row for row in old_status.get('subscriptions', [])}
            for row in rows:
                old = old_rows.get(row['name'], {})
                if row['name'] not in selected and row.get('fetched_at') == old.get('fetched_at'):
                    row['update_failed'] = old.get('update_failed', False)
            status['subscriptions'] = rows
            if not rows or not any(row.get('nodes', 0) > 0 for row in rows):
                raise ManagementError('SUBSCRIPTION_NODES_UNAVAILABLE')
            if not any(row['name'] in selected and row.get('source') == 'fresh' for row in rows):
                # Every download failed: no restart or cache replacement needed.
                atomic_write(layout, 'var/lib/paopaogw/subscriptions/status.json',
                             (json.dumps(status, ensure_ascii=False) + '\n').encode())
                record_results(layout, configured, selected, rows)
                return rows
            changed = False
            by_name = {row['name']: row for row in rows}
            for provider in configured:
                row = by_name.get(provider['name'], {})
                if provider['name'] not in selected or row.get('source') != 'fresh': continue
                filename = identity(provider) + '.json'
                same = False
                try: same = payload_content(original[filename]) == payload_content((stage / filename).read_bytes())
                except (KeyError, OSError, ValueError): pass
                row['result'] = 'unchanged' if same else 'updated'
                old_row = old_rows.get(provider['name'], {})
                row['node_delta'] = row.get('nodes', 0) - (old_row.get('nodes', 0) if filename in original else 0)
                changed = changed or not same
            published = True
            progress('saving')
            for target in stage.glob('*.json'):
                if re.fullmatch(r'[0-9a-f]{64}\.json', target.name):
                    atomic_write(layout, 'var/lib/paopaogw/subscriptions/' + target.name, target.read_bytes())
            if changed:
                progress('applying')
                system.command('systemctl', 'restart', 'paopaogw')
                progress('verifying')
                running = system.wait(settings)
                if isinstance(running, dict):
                    from manage_operations import remember_loaded
                    from manage_model import settings_revision
                    remember_loaded(layout, settings_revision(layout), running)
            # Startup reads the cache; keep the actual manual update result in UI.
            atomic_write(layout, 'var/lib/paopaogw/subscriptions/status.json',
                         (json.dumps(status, ensure_ascii=False) + '\n').encode())
            record_results(layout, configured, selected, rows)
            return rows
        except Exception:
            if published:
                progress('rolling_back')
                for target in cache.glob('*.json'):
                    if re.fullmatch(r'(?:[0-9a-f]{64}|status)\.json', target.name) and target.name not in original:
                        target.unlink()
                for filename, raw in original.items():
                    atomic_write(layout, 'var/lib/paopaogw/subscriptions/' + filename, raw)
                system.command('systemctl', 'restart', 'paopaogw')
                system.wait(settings)
            record_results(layout, configured, selected, [], error='APPLY_FAILED_ROLLED_BACK' if published else 'FETCH_FAILED' if phase == 'fetching' else 'VALIDATION_FAILED')
            raise
