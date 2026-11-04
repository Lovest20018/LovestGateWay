#!/usr/bin/env python3
"""Use loopback fixture providers to count requests made by the real Rust binary."""
import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binary = str(args.binary.resolve())
    requests = Counter()
    denied = set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            key = self.path.lstrip('/')
            requests[key] += 1
            if key in denied:
                self.send_response(403); self.end_headers(); return
            raw = ('proxies:\n  - {name: ' + key + ', type: ss, server: 127.0.0.1, port: 443, cipher: aes-128-gcm, password: fixture}\n').encode()
            self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); cache = root / 'cache'; cache.mkdir()
            subs = [{'name': name, 'url': 'http://127.0.0.1:' + str(server.server_port) + '/' + name} for name in ['ProviderA', 'ProviderB', 'ProviderC']]
            source = root / 'ppsub.json'; source.write_text(json.dumps({'exported_at': 'fixture', 'subs': subs,
                'node-groups': [{'name': 'Main', 'subs': ['all']}], 'rules': []}))
            def run(selected=None, only=False):
                env = dict(os.environ, PPGW_SUB_CACHE_DIR=str(cache), PPGW_SUB_CACHE_ONLY='1' if only else '0',
                           dns_burn='no', ppgw_retry_dir=str(root / 'retry'))
                env.pop('PPGW_SUB_REFRESH_NAMES', None)
                if selected is not None: env['PPGW_SUB_REFRESH_NAMES'] = json.dumps(selected)
                subprocess.run([binary, 'ppgw', '-ppsub', str(source), '-output', str(root / 'out.yaml')],
                    env=env, check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return json.loads((cache / 'status.json').read_bytes())['subscriptions']
            def key(row): return hashlib.sha256((row['name'] + '\n' + row['url']).encode()).hexdigest() + '.json'
            run()
            assert dict(requests) == {'ProviderA': 1, 'ProviderB': 1, 'ProviderC': 1}
            nex = (cache / key(subs[0])).read_bytes()
            run(['ProviderB', 'ProviderC'])
            assert dict(requests) == {'ProviderA': 1, 'ProviderB': 2, 'ProviderC': 2}, dict(requests)
            assert (cache / key(subs[0])).read_bytes() == nex
            before = requests.copy()
            run(only=True)
            assert requests == before
            rows = run(['ProviderA'])
            assert requests['ProviderA'] == 2 and requests['ProviderB'] == 2 and requests['ProviderC'] == 2
            denied.add('ProviderB')
            old = (cache / key(subs[1])).read_bytes()
            rows = run(['ProviderB'])
            assert (cache / key(subs[1])).read_bytes() == old
            row = next(r for r in rows if r['name'] == 'ProviderB')
            assert row['update_failed'] and row['nodes'] == 1 and row['source'] == 'cache'
            assert requests['ProviderA'] == 2 and requests['ProviderC'] == 2
            before = requests.copy()
            (cache / key(subs[0])).unlink()
            rows = run(['ProviderC'])
            assert requests['ProviderA'] == before['ProviderA']
            assert next(r for r in rows if r['name'] == 'ProviderA')['nodes'] == 0
            report = {'synthetic_only': True, 'auto_targets_do_not_fetch_manual_provider': True,
                'single_manual_refresh_does_not_fetch_other_providers': True, 'restart_extra_http_requests': 0,
                'failed_selected_refresh_preserves_cache_bytes': True,
                'unselected_missing_cache_does_not_trigger_download': True, 'request_counts': dict(requests)}
            args.report.write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report))
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': main()
