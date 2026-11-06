"""Isolated crash fixture: no network, services, or real core are invoked."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integration'))
from manage_common import Layout, atomic_write
from manage_subscription import refresh_nodes
from manage_subscription_schedule import providers, identity

layout = Layout(sys.argv[1])
point = sys.argv[2]
rows = providers(layout)

def run(command, **kwargs):
    if '-ppsub' in command:
        stage = Path(kwargs['env']['PPGW_SUB_CACHE_DIR'])
        for row in rows:
            (stage / (identity(row) + '.json')).write_text(json.dumps({'raw_yaml': 'new ' + row['name']}))
        (stage / 'status.json').write_text(json.dumps({'subscriptions': [
            {'name': row['name'], 'source': 'fresh', 'nodes': 1} for row in rows]}))
    return SimpleNamespace(returncode=0)

def publish(layout, path, raw, *args, **kwargs):
    atomic_write(layout, path, raw, *args, **kwargs)
    if point == 'first_provider' and path.startswith('var/lib/paopaogw/subscriptions/') and len(Path(path).stem) == 64:
        os._exit(77)

def progress(stage):
    if point == 'before_verification' and stage == 'verifying':
        (layout.path('var/lib/paopaogw/subscriptions/status.json')).write_text('{"at":123,"source":"cache"}')
        os._exit(77)

system = SimpleNamespace(command=lambda *args: SimpleNamespace(stdout='123'), wait=lambda _: None)
with patch('manage_subscription.subprocess.run', side_effect=run), patch('manage_subscription.atomic_write', side_effect=publish):
    refresh_nodes(layout, system, progress=progress)
