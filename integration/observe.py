#!/usr/bin/env python3
"""Read-only history collection for the optional observer API."""
from pathlib import Path
import json
import logging
import re
import time
from gateway_api import ApiError, CoreClient, mihomo_connections, project
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


def collect_once(client, writer, retention_days, report):
    info = client.request('/sniffbox/info')
    boot = info['integration']['boot_id']
    response = client.request('/sniffbox/evidence', {'resource': 'connections', 'limit': 200})
    if response.get('boot_id') != boot:
        raise ApiError(502, 'CORE_IDENTITY_CHANGED')
    batches = [('sniffbox_lifecycle', response.get('data', {}).get('items', []))]
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
    writer.store(boot, batches, retention_days)


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
