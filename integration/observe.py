#!/usr/bin/env python3
"""Read-only history collection for the optional observer API."""
from pathlib import Path
import datetime
import json
import logging
import re
import time
from gateway_api import ApiError, CoreClient, metric_number, mihomo_connections, project
from history_store import HistoryWriter


class ErrorReporter:
    def __init__(self, clock=time.monotonic, emit=None):
        self.clock = clock
        self.emit = emit or logging.getLogger('paopaogw.history').warning
        self.next_report = {}

    def __call__(self, stage, error):
        code = error.code if isinstance(error, ApiError) else 'COLLECTION_FAILED'
        if not re.fullmatch(r'[A-Z_]{3,64}', str(code)): code = 'COLLECTION_FAILED'
        now = self.clock()
        if now < self.next_report.get((stage, code), 0): return
        self.next_report[stage, code] = now + 60
        self.emit('history sample failed: stage=%s code=%s', stage, code)


def selected_latency(client, now):
    """Read existing default-outbound checks; collecting history never starts probes."""
    rules = client.request('/rules').get('rules', [])
    target = next((row.get('proxy') for row in rules if row.get('type') in {'Match', 'MATCH'}), None)
    if not target: return None
    proxies = client.request('/proxies').get('proxies', {})
    seen = set()
    while target in proxies and target not in seen:
        seen.add(target); node = proxies[target]
        history = node.get('history') or []
        for check in history[-1:]:
            try:
                at = datetime.datetime.fromisoformat(check['time'].replace('Z', '+00:00')).timestamp()
                delay = metric_number(check.get('delay'))
                if 0 <= now - at <= 90:
                    return delay if delay is not None and delay > 0 else None
            except (KeyError, TypeError, ValueError): continue
        if isinstance(node.get('all'), list): target = node.get('now'); continue
        break
    return None


def collect_once(client, writer, retention_days, report):
    info = client.request('/sniffbox/info')
    boot = info['integration']['boot_id']
    cursor = writer.cursor(boot)
    rows, gap = [], False
    for _ in range(6):
        response = client.request('/sniffbox/evidence', {'resource': 'connections', 'limit': 200, 'after': cursor})
        if response.get('boot_id') != boot: raise ApiError(502, 'CORE_IDENTITY_CHANGED')
        data = response.get('data', {})
        rows.extend(data.get('items', [])); gap = gap or data.get('gap', False)
        next_cursor = data.get('next_sequence')
        if type(next_cursor) is not int or next_cursor < cursor: break
        if next_cursor == cursor: break
        cursor = next_cursor
        if not data.get('truncated'): break
    batches = [('sniffbox_lifecycle', rows)]
    cpu, memory, traffic, connections = [info.get(key) or {} for key in ('cpu', 'memory', 'traffic', 'connections')]
    sample = {'download': traffic.get('downloadTotal'), 'upload': traffic.get('uploadTotal'),
              'cpu': cpu.get('usage'), 'active': connections.get('active'), 'gap': bool(gap),
              'outbound_uptime': memory.get('clash_uptime') if info.get('mode') in {'yaml', 'suburl'} else memory.get('ovpn_uptime')}
    total, available = metric_number(memory.get('total')), metric_number(memory.get('available'))
    if total is not None and available is not None and total >= available: sample['memory'] = total - available
    if info.get('mode') in {'yaml', 'suburl'}:
        try:
            connections = mihomo_connections(client.request('/connections'))
            rows = [project(row, ('id', 'start', 'upload', 'download', 'chains', 'rule', 'rulePayload')) |
                    {'metadata': project(row.get('metadata'), ('network', 'type', 'sourceIP', 'sourcePort', 'destinationIP', 'destinationPort', 'host')),
                     'decision_source': 'mihomo_connection_snapshot', 'remote_target_verified': False}
                    for row in connections]
            batches.append(('mihomo_snapshot', rows))
        except Exception as error:
            # Keep successful native samples; failed reads are never empty snapshots.
            report('mihomo', error)
        if writer.metric_due():
            try: sample['latency_ms'] = selected_latency(client, time.time())
            except Exception as error: report('latency', error)
            sample['latency_checked'] = True
    # Optional snapshots can span a restart even when every native page matched.
    latest = client.request('/sniffbox/info/dynamic')
    if latest.get('integration', {}).get('boot_id') != boot:
        raise ApiError(502, 'CORE_IDENTITY_CHANGED')
    writer.store(boot, batches, retention_days, metrics=sample, cursor=cursor)


def main():
    api = json.loads(Path('/etc/paopaogw/api.json').read_bytes())
    if api.get('enabled') is not True: return
    client = CoreClient(Path('/var/lib/paopaogw/core-token'))
    report = ErrorReporter()
    writer = None
    try:
        while True:
            try:
                config = json.loads(Path('/etc/paopaogw/maintenance.json').read_bytes())
                if config.get('history_enabled', False):
                    if writer is None: writer = HistoryWriter()
                    collect_once(client, writer, max(1, min(365, int(config.get('history_retention_days', 30)))), report)
                elif writer is not None:
                    writer.close(); writer = None
            except Exception as error:
                report('sample', error)
                if writer is not None: writer.close(); writer = None
            # Identity changes and failures also wait, avoiding busy retry loops.
            time.sleep(1)
    finally:
        if writer is not None: writer.close()


if __name__ == '__main__':
    main()
