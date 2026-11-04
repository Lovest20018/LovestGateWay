"""Fetch PPsub nodes in isolation, then publish and verify them with rollback."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from manage_common import ManagementError, atomic_write
from manage_model import load_settings


def refresh_nodes(layout, system, names=None):
    from manage_subscription_schedule import providers, source_path, record_attempt
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
        try:
            # No controller script runs in another service's cgroup. The live
            # controller keeps forwarding while these downloads are validated.
            subprocess.run(prefix + [str(code / 'sniffbox'), 'ppgw', '-ppsub',
                str(source), '-output', str(output)],
                env=env, check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                return rows
            published = True
            for target in stage.glob('*.json'):
                if re.fullmatch(r'[0-9a-f]{64}\.json', target.name):
                    atomic_write(layout, 'var/lib/paopaogw/subscriptions/' + target.name, target.read_bytes())
            system.command('systemctl', 'restart', 'paopaogw')
            system.wait(settings)
            # Startup reads the cache; keep the actual manual update result in UI.
            atomic_write(layout, 'var/lib/paopaogw/subscriptions/status.json',
                         (json.dumps(status, ensure_ascii=False) + '\n').encode())
            return rows
        except Exception:
            if published:
                for target in cache.glob('*.json'):
                    if re.fullmatch(r'(?:[0-9a-f]{64}|status)\.json', target.name) and target.name not in original:
                        target.unlink()
                for filename, raw in original.items():
                    atomic_write(layout, 'var/lib/paopaogw/subscriptions/' + filename, raw)
                system.command('systemctl', 'restart', 'paopaogw')
                system.wait(settings)
            raise
