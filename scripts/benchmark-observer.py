#!/usr/bin/env python3
"""Compare history collectors with synthetic records in disposable databases."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time
from unittest.mock import patch


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def io_bytes():
    try:
        return int(dict(line.split(': ', 1) for line in Path('/proc/self/io').read_text().splitlines())['write_bytes'])
    except (OSError, KeyError):
        return None


def records(count, tick=0, prefix='active'):
    return [{'id': f'{prefix}-{index}', 'domain': f'node-{index}.example.test',
             'source_address': '192.0.2.10', 'upload': index + tick * 1024,
             'download': index * 2 + tick * 2048, 'remote_target_verified': False}
            for index in range(count)]


def run(module, path, optimized, rows, ticks, seed_rows):
    module.store('synthetic', 'sniffbox_lifecycle', records(seed_rows, prefix='seed'), path=path, now=100)
    sql = {'schema': 0, 'commits': 0, 'capacity_scans': 0}
    connect = sqlite3.connect

    def trace(statement):
        if statement.startswith('CREATE '): sql['schema'] += 1
        if statement == 'COMMIT': sql['commits'] += 1
        if 'OFFSET' in statement: sql['capacity_scans'] += 1

    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(trace)
        return db

    samples = []
    cpu = time.process_time()
    before_io = io_bytes()
    with patch.object(sqlite3, 'connect', traced):
        writer = module.HistoryWriter(path) if optimized else None
        try:
            for tick in range(ticks):
                batches = [(source, records(rows, tick)) for source in ('sniffbox_lifecycle', 'mihomo_snapshot')]
                start = time.perf_counter()
                if writer:
                    writer.store('synthetic', batches, now=200 + tick)
                else:
                    for source, values in batches:
                        module.store('synthetic', source, values, path=path, now=200 + tick)
                samples.append((time.perf_counter() - start) * 1000)
        finally:
            if writer: writer.close()
    after_io = io_bytes()
    cpu_ms = (time.process_time() - cpu) * 1000
    with closing(connect(path)) as db:
        final = db.execute('SELECT boot,id,source,first_seen,last_seen,domain,client,value FROM observations ORDER BY boot,id,source').fetchall()
    ordered = sorted(samples)
    return {'ticks': ticks, 'rows_per_source': rows, 'seed_rows': seed_rows,
            'p50_ms': statistics.median(samples), 'p95_ms': ordered[math.ceil(len(ordered) * .95) - 1],
            'cpu_ms': cpu_ms, 'write_bytes': after_io - before_io if after_io is not None and before_io is not None else None,
            'sql': sql, 'final_rows': len(final), 'records_sha256': hashlib.sha256(json.dumps(final, ensure_ascii=False).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Previous history_store.py')
    parser.add_argument('--candidate', type=Path, default=Path(__file__).resolve().parents[1] / 'integration/history_store.py')
    parser.add_argument('--rows', type=int, default=500)
    parser.add_argument('--ticks', type=int, default=30)
    parser.add_argument('--seed-rows', type=int, default=10000)
    parser.add_argument('--directory', type=Path, help='Scratch directory on the filesystem being measured')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.rows <= 5000 or not 5 <= args.ticks <= 200 or not 0 <= args.seed_rows <= 90000:
        parser.error('keep the synthetic benchmark bounded')
    with tempfile.TemporaryDirectory(prefix='ppgw-history-benchmark-', dir=args.directory) as temporary:
        root = Path(temporary)
        baseline = run(load(args.baseline, 'baseline_history'), root / 'before.sqlite3', False, args.rows, args.ticks, args.seed_rows)
        candidate = run(load(args.candidate, 'candidate_history'), root / 'after.sqlite3', True, args.rows, args.ticks, args.seed_rows)
    if baseline['records_sha256'] != candidate['records_sha256']:
        raise AssertionError('History contents differ; a faster run with lost data is invalid')
    report = {'synthetic_only': True, 'same_final_records': True, 'explicit_scratch_filesystem': args.directory is not None,
              'baseline': baseline, 'candidate': candidate}
    raw = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    print(raw)


if __name__ == '__main__':
    main()
