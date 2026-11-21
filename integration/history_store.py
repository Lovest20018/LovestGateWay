#!/usr/bin/env python3
"""Bounded persisted observations; polling gaps and unknown remote decisions stay explicit."""
from pathlib import Path
from contextlib import contextmanager
import json
import math
import os
import sqlite3
import threading
import time

DATABASE = Path('/var/lib/paopaogw/history/observations.sqlite3')
MAX_ROWS = 100000


def open_database(path=DATABASE, readonly=False):
    if readonly:
        db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        db = sqlite3.connect(path, timeout=3)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('CREATE TABLE IF NOT EXISTS observations (boot TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, domain TEXT, client TEXT, value TEXT NOT NULL, PRIMARY KEY(boot,id,source))')
        db.execute('CREATE INDEX IF NOT EXISTS seen ON observations(last_seen)')
        db.execute('CREATE INDEX IF NOT EXISTS domain ON observations(domain)')
        db.execute('CREATE TABLE IF NOT EXISTS metric_minutes (at INTEGER PRIMARY KEY, samples INTEGER NOT NULL, seconds REAL NOT NULL, down REAL NOT NULL, up REAL NOT NULL, cpu REAL NOT NULL, cpu_n INTEGER NOT NULL, memory REAL NOT NULL, memory_n INTEGER NOT NULL, active REAL NOT NULL, active_n INTEGER NOT NULL, latency REAL NOT NULL, latency_n INTEGER NOT NULL, connected INTEGER NOT NULL, failed INTEGER NOT NULL, gaps INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS collector_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        os.chmod(path, 0o640)
    return db


@contextmanager
def connect(path=DATABASE, readonly=False):
    db = open_database(path, readonly)
    try:
        with db:
            yield db
    finally:
        db.close()


class HistoryWriter:
    """Reuse a connection; commit all successful sources in one sample together."""
    def __init__(self, path=DATABASE):
        self.db = open_database(path)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    def cursor(self, boot):
        row = self.db.execute("SELECT value FROM collector_state WHERE key='cursor'").fetchone()
        value = json.loads(row[0]) if row else {}
        return value.get('after', 0) if value.get('boot') == boot else 0

    def metric_due(self, now=None):
        now = time.time() if now is None else now
        return int(now // 60) != getattr(self, '_latency_minute', None)

    def store(self, boot, batches, retention_days=30, now=None, *, metrics=None, cursor=None):
        now = time.time() if now is None else now
        values = []
        for source, rows in batches:
            for row in rows:
                if not isinstance(row, dict) or not row.get('id'): continue
                metadata = row.get('metadata')
                if not isinstance(metadata, dict): metadata = {}
                domain = row.get('domain') or metadata.get('host')
                client = row.get('source_address') or metadata.get('sourceIP')
                values.append((boot, str(row['id']), source, now, now, domain, client,
                               json.dumps(row, ensure_ascii=False, separators=(',', ':'))))
        with self.db as db:
            connected = failed = 0
            if metrics is not None:
                for source, rows in batches:
                    if source != 'sniffbox_lifecycle': continue
                    for row in rows:
                        if not row.get('id') or row.get('ended_at') is None: continue
                        known = db.execute('SELECT 1 FROM observations WHERE boot=? AND id=? AND source=?',
                                           (boot, str(row['id']), source)).fetchone()
                        if known: continue
                        result = (row.get('decision') or {}).get('connect_result')
                        connected += result == 'connected'
                        failed += result == 'failed'
            db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(boot,id,source) DO UPDATE SET last_seen=excluded.last_seen,domain=excluded.domain,client=excluded.client,value=excluded.value', values)
            if metrics is not None:
                self._metrics(db, boot, metrics, now, connected, failed)
                db.execute('DELETE FROM metric_minutes WHERE at < ?', (now - retention_days * 86400,))
            if cursor is not None:
                db.execute("INSERT OR REPLACE INTO collector_state VALUES ('cursor',?)", (json.dumps({'boot': boot, 'after': cursor}),))
            # Indexed expiry stays exact. Avoid the full offset eviction scan
            # unless the hard capacity is actually exceeded.
            db.execute('DELETE FROM observations WHERE last_seen < ?', (now - retention_days * 86400,))
            if db.execute('SELECT COUNT(*) FROM observations').fetchone()[0] > MAX_ROWS:
                db.execute('DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY last_seen DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))

    def _metrics(self, db, boot, sample, now, connected, failed):
        def number(value): return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        down, up = number(sample.get('download')), number(sample.get('upload'))
        row = db.execute("SELECT value FROM collector_state WHERE key='sample'").fetchone()
        previous = json.loads(row[0]) if row else {}
        elapsed = now - previous.get('at', now)
        uptime = number(sample.get('outbound_uptime'))
        previous_uptime = number(previous.get('outbound_uptime'))
        reset_generation = sample.get('reset_generation')
        same_outbound = uptime is None or previous_uptime is None or uptime >= previous_uptime
        valid = (previous.get('boot') == boot and previous.get('reset_generation') == reset_generation
                 and 0 < elapsed <= 10 and down is not None and up is not None
                 and previous.get('down') is not None and previous.get('up') is not None
                 and down >= previous['down'] and up >= previous['up'] and same_outbound)
        cpu, memory, active, latency = [number(sample.get(key)) for key in ('cpu', 'memory', 'active', 'latency_ms')]
        minute = int(now // 60) * 60
        data = (minute, 1, elapsed if valid else 0, down - previous['down'] if valid else 0,
                up - previous['up'] if valid else 0, cpu or 0, int(cpu is not None), memory or 0, int(memory is not None),
                active or 0, int(active is not None), latency or 0, int(latency is not None), connected, failed,
                int(sample.get('gap', False)) + int(bool(previous) and not valid))
        db.execute('INSERT INTO metric_minutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(at) DO UPDATE SET '
                   'samples=samples+excluded.samples,seconds=seconds+excluded.seconds,down=down+excluded.down,up=up+excluded.up,'
                   'cpu=cpu+excluded.cpu,cpu_n=cpu_n+excluded.cpu_n,memory=memory+excluded.memory,memory_n=memory_n+excluded.memory_n,'
                   'active=active+excluded.active,active_n=active_n+excluded.active_n,latency=latency+excluded.latency,'
                   'latency_n=latency_n+excluded.latency_n,connected=connected+excluded.connected,failed=failed+excluded.failed,gaps=gaps+excluded.gaps', data)
        db.execute("INSERT OR REPLACE INTO collector_state VALUES ('sample',?)", (json.dumps({
            'boot': boot, 'at': now, 'down': down, 'up': up, 'outbound_uptime': uptime,
            'reset_generation': reset_generation}),))
        if sample.get('latency_checked'): self._latency_minute = int(now // 60)


def metrics_summary(path=DATABASE, now=None):
    """Bounded, read-only last-day aggregates; no client/domain/credential projection."""
    now = time.time() if now is None else now
    empty = {'status': 'no_records', 'points': [], 'connection_rows': 0, 'capacity': MAX_ROWS,
             'oldest_record_at': None, 'latest_record_at': None, 'last_sample_at': None,
             'window_seconds': 86400, 'bucket_seconds': 300, 'coverage_seconds': 0,
             'download_bytes': 0, 'upload_bytes': 0, 'connected': 0, 'failed': 0, 'failure_percent': None}
    if not path.is_file(): return empty
    with connect(path, True) as db:
        count, oldest, latest = db.execute('SELECT COUNT(*),MIN(first_seen),MAX(last_seen) FROM observations').fetchone()
        empty.update(connection_rows=count, oldest_record_at=oldest, latest_record_at=latest)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'metric_minutes' not in tables: return empty
        sample = db.execute("SELECT value FROM collector_state WHERE key='sample'").fetchone()
        empty['last_sample_at'] = json.loads(sample[0]).get('at') if sample else None
        rows = db.execute('SELECT CAST(at/300 AS INTEGER)*300,SUM(samples),SUM(seconds),SUM(down),SUM(up),'
                          'SUM(cpu),SUM(cpu_n),SUM(memory),SUM(memory_n),SUM(active),SUM(active_n),SUM(latency),SUM(latency_n),'
                          'SUM(connected),SUM(failed),SUM(gaps) FROM metric_minutes WHERE at>=? AND at<=? GROUP BY 1 ORDER BY 1',
                          (int((now - 86400) // 300) * 300, now)).fetchall()
    points = []
    for at, samples, seconds, down, up, cpu, cpu_n, memory, memory_n, active, active_n, latency, latency_n, connected, failed, gaps in rows:
        points.append({'at': at, 'samples': samples, 'coverage_seconds': seconds, 'download_bytes': down, 'upload_bytes': up,
                       'download_rate': down / seconds if seconds else None, 'upload_rate': up / seconds if seconds else None,
                       'cpu_percent': cpu / cpu_n if cpu_n else None, 'memory_bytes': memory / memory_n if memory_n else None,
                       'active_connections': active / active_n if active_n else None,
                       'latency_ms': latency / latency_n if latency_n else None,
                       'connected': connected, 'failed': failed,
                       'failure_percent': failed * 100 / (connected + failed) if connected + failed else None, 'gaps': gaps})
    result = {**empty, 'status': 'available' if points else 'no_records', 'points': points}
    for key in ('coverage_seconds', 'download_bytes', 'upload_bytes', 'connected', 'failed'):
        result[key] = sum(point[key] for point in points)
    total = result['connected'] + result['failed']
    result['failure_percent'] = result['failed'] * 100 / total if total else None
    return result


class HistorySummary:
    """Cache aggregates so normal settings polling never scans history every second."""
    def __init__(self, layout):
        self.layout, self.lock, self.cached, self.checked = layout, threading.Lock(), None, 0

    def invalidate(self):
        with self.lock: self.cached = None

    def read(self):
        with self.lock:
            if self.cached is not None and time.monotonic() - self.checked < 30: return self.cached
            def config(name):
                try:
                    value = json.loads(self.layout.path('etc/paopaogw/' + name + '.json').read_bytes())
                    return value if isinstance(value, dict) else {}
                except (OSError, ValueError): return {}
            api, maintenance = config('api'), config('maintenance')
            enabled = api.get('enabled') is True and maintenance.get('history_enabled') is True
            try: value = metrics_summary(self.layout.path('var/lib/paopaogw/history/observations.sqlite3'))
            except (OSError, ValueError, sqlite3.Error): value = {'status': 'unavailable', 'points': []}
            age = time.time() - value['last_sample_at'] if value.get('last_sample_at') is not None else None
            retention = maintenance.get('history_retention_days', 30)
            if type(retention) is not int or not 1 <= retention <= 90: retention = 30
            value.update(enabled=enabled, retention_days=retention,
                         collector_state='disabled' if not enabled else 'running' if age is not None and 0 <= age <= 15 else 'waiting' if age is None else 'stale')
            self.cached, self.checked = value, time.monotonic()
            return value


def store(boot, source, rows, path=DATABASE, retention_days=30, now=None):
    with HistoryWriter(path) as writer:
        writer.store(boot, [(source, rows)], retention_days, now)


def query(limit=100, before=None, domain=None, client=None, source=None, path=DATABASE):
    if not 1 <= limit <= 500: raise ValueError('INVALID_LIMIT')
    if not path.is_file():
        return {'status': 'no_records', 'items': [], 'next_before': None, 'complete_history': False}
    conditions, args = [], []
    for column, value in (('domain', domain), ('client', client), ('source', source)):
        if value is not None:
            conditions.append(column + '=?'); args.append(value)
    if before is not None:
        conditions.append('rowid < ?'); args.append(int(before))
    sql = ('SELECT rowid,boot,id,source,first_seen,last_seen,value FROM observations' +
           (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY rowid DESC LIMIT ?')
    with connect(path, True) as db:
        rows = db.execute(sql, args + [limit + 1]).fetchall()
    truncated = len(rows) > limit
    items = [{'boot_id': row[1], 'id': row[2], 'source': row[3], 'first_seen_at': row[4],
              'last_seen_at': row[5], 'record': json.loads(row[6])} for row in rows[:limit]]
    return {'status': 'available' if items else 'no_records', 'items': items,
            'next_before': rows[limit - 1][0] if truncated else None,
            'complete_history': False, 'max_rows': MAX_ROWS,
            'limitations': ['Polling can miss short mihomo connections; absence is not proof of no traffic.',
                            'A missing later snapshot does not prove a connection ended or succeeded.',
                            'Remote proxy DNS and final remote target addresses are not inferred.']}
