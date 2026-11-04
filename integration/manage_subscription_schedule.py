"""Per-provider schedules; identities bind attempts to both name and URL."""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from manage_common import ManagementError, atomic_write

STATE = 'var/lib/paopaogw/subscriptions/schedule.json'


def identity(row):
    return hashlib.sha256((row['name'] + '\n' + row['url']).encode()).hexdigest()


def source_path(layout):
    local = layout.path('etc/paopaogw/files/ppsub.json')
    return local if local.is_file() else layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json')


def providers(layout):
    return json.loads(source_path(layout).read_bytes())['subs']


def validate_plan(row):
    mode = row.get('update_mode', 'inherit')
    if not isinstance(mode, str) or mode not in {'inherit', 'manual', 'auto'}:
        raise ManagementError('INVALID_PPSUB')
    interval = row.get('update_interval', '1d')
    if not isinstance(interval, str) or not re.fullmatch(r'[1-9][0-9]{0,3}[mhd]', interval):
        raise ManagementError('INVALID_PPSUB')


def read_state(layout):
    try:
        value = json.loads(layout.path(STATE).read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(layout, value):
    atomic_write(layout, STATE, (json.dumps(value, ensure_ascii=False) + '\n').encode())


def due_providers(layout, settings, now=None):
    now = time.time() if now is None else now
    state = read_state(layout)
    due = []
    tracked = {}
    for row in providers(layout):
        validate_plan(row)
        mode = row.get('update_mode', 'inherit')
        interval = row.get('update_interval', '1d') if mode == 'auto' else settings['ini']['subtime']
        cron = settings['ini'].get('subcron', '') if mode == 'inherit' else ''
        key = identity(row)
        signature = [mode, interval, cron]
        previous = state.get(key, {})
        entry = previous if previous.get('plan') == signature else {'plan': signature, 'enabled_at': now}
        tracked[key] = entry
        if mode == 'manual' or (mode == 'inherit' and interval == 'manual'):
            continue
        base = max(entry.get('enabled_at', now), entry.get('attempted_at', 0))
        if re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-3])', str(cron)):
            target = datetime.fromtimestamp(now).replace(hour=int(cron), minute=0, second=0, microsecond=0)
            if target.timestamp() > now: target -= timedelta(days=1)
            ready = base < target.timestamp()
        else:
            match = re.fullmatch(r'([1-9][0-9]{0,3})([mhd])', interval)
            if not match: raise ManagementError('INVALID_PPSUB')
            ready = now >= base + int(match[1]) * {'m': 60, 'h': 3600, 'd': 86400}[match[2]]
        if ready: due.append(row['name'])
    if tracked != state: write_state(layout, tracked)
    return due


def record_attempt(layout, rows, names, now=None):
    now = time.time() if now is None else now
    state = read_state(layout)
    for row in rows:
        if row['name'] in names:
            state.setdefault(identity(row), {})['attempted_at'] = now
    write_state(layout, state)
