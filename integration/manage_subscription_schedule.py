"""Per-provider schedules; identities bind attempts to both name and URL."""
import hashlib
import json
import re
import time
import threading
from collections import OrderedDict
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
        entry = previous if previous.get('plan') == signature else {**previous, 'plan': signature, 'enabled_at': now}
        tracked[key] = entry
        target = next_update(row, settings, entry, now)
        if target is not None and now >= target: due.append(row['name'])
    if tracked != state: write_state(layout, tracked)
    return due


def next_update(row, settings, entry, now):
    mode = row.get('update_mode', 'inherit')
    interval = row.get('update_interval', '1d') if mode == 'auto' else settings['ini']['subtime']
    cron = settings['ini'].get('subcron', '') if mode == 'inherit' else ''
    if mode == 'manual' or interval == 'manual': return None
    base = max(entry.get('enabled_at', now), entry.get('attempted_at', 0))
    if re.fullmatch(r'(?:[0-9]|1[0-9]|2[0-3])', str(cron)):
        target = datetime.fromtimestamp(base).replace(hour=int(cron), minute=0, second=0, microsecond=0)
        if target.timestamp() <= base: target += timedelta(days=1)
        return target.timestamp()
    match = re.fullmatch(r'([1-9][0-9]{0,3})([mhd])', interval)
    if not match: raise ManagementError('INVALID_PPSUB')
    return base + int(match[1]) * {'m': 60, 'h': 3600, 'd': 86400}[match[2]]


def record_results(layout, configured, names, rows, error=None, now=None):
    now = time.time() if now is None else now
    state = read_state(layout)
    by_name = {row['name']: row for row in rows}
    for provider in configured:
        if provider['name'] not in names: continue
        entry = state.setdefault(identity(provider), {})
        row = by_name.get(provider['name'], {})
        fresh = row.get('source') == 'fresh' and not error
        entry['result'] = ('failed' if error else row.get('result', 'updated' if fresh else 'retained' if row.get('nodes') else 'unavailable'))
        entry['error_category'] = error or ('FETCH_FAILED' if row.get('update_failed') else None)
        if fresh: entry['succeeded_at'] = now
        if not error:
            entry['nodes'] = row.get('nodes', 0)
            entry['node_delta'] = row.get('node_delta')
    write_state(layout, state)


_saved_projections = OrderedDict()
_projection_lock = threading.Lock()


def saved_projection(layout, key):
    """Cache only safe metadata, never retain raw node YAML or credentials."""
    path = layout.path('var/lib/paopaogw/subscriptions/' + key + '.json')
    with _projection_lock:
        stat = path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cached = _saved_projections.get(str(path))
        if cached and cached[0] == signature:
            _saved_projections.move_to_end(str(path))
            return cached[1]
        value = json.loads(path.read_bytes())
        safe = {field: value.get(field) for field in ('name', 'url_sha256', 'saved_at')}
        after = path.stat()
        if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != signature:
            raise ValueError('CACHE_CHANGED_DURING_SAMPLE')
        _saved_projections[str(path)] = (signature, safe)
        _saved_projections.move_to_end(str(path))
        while len(_saved_projections) > 512: _saved_projections.popitem(last=False)
        return safe


def provider_status(layout, settings, now=None):
    now = time.time() if now is None else now
    state = read_state(layout)
    try: cached = json.loads(layout.path('var/lib/paopaogw/subscriptions/status.json').read_bytes())
    except (OSError, ValueError): cached = {}
    # Status is tied to the name AND URL. A stale name-only status cannot attest
    # to nodes for a newly edited subscription URL.
    result = []
    for provider in providers(layout):
        key = identity(provider); entry = state.get(key, {})
        try:
            saved = saved_projection(layout, key)
            has_cache = saved.get('name') == provider['name'] and saved.get('url_sha256') == hashlib.sha256(provider['url'].encode()).hexdigest()
        except (OSError, ValueError): saved, has_cache = {}, False
        row = next((r for r in cached.get('subscriptions', []) if r.get('name') == provider['name']), {}) if has_cache else {}
        mode = provider.get('update_mode', 'inherit')
        result.append({'name': provider['name'], 'nodes': entry.get('nodes', row.get('nodes', 0)) if has_cache else 0,
                       'source': row.get('source', 'unavailable'), 'fetched_at': saved.get('saved_at') if has_cache else None,
                       'update_failed': bool(entry.get('error_category') or row.get('update_failed')),
                       'attempted_at': entry.get('attempted_at'), 'succeeded_at': entry.get('succeeded_at', saved.get('saved_at') if has_cache else None),
                       'next_update_at': next_update(provider, settings, entry, now) if entry.get('plan') == [mode, provider.get('update_interval', '1d') if mode == 'auto' else settings['ini']['subtime'], settings['ini'].get('subcron', '') if mode == 'inherit' else ''] else None,
                       'update_mode': mode, 'result': entry.get('result', 'saved' if has_cache else 'unavailable'),
                       'error_category': entry.get('error_category'), 'node_delta': entry.get('node_delta')})
    return result


def record_attempt(layout, rows, names, now=None):
    now = time.time() if now is None else now
    state = read_state(layout)
    for row in rows:
        if row['name'] in names:
            state.setdefault(identity(row), {})['attempted_at'] = now
    write_state(layout, state)
