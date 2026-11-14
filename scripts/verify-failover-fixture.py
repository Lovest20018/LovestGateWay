#!/usr/bin/env python3
"""Exercise the real PPsub failover CLI against a synthetic Unix controller only."""
import argparse
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit


def verify(binary):
    evidence = []
    with tempfile.TemporaryDirectory(prefix='ppgw-failover-') as temporary:
        directory = Path(temporary)
        state = {'selected': 'Primary', 'primary_ok': True, 'backup_ok': True, 'calls': []}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                path = urlsplit(self.path).path
                state['calls'].append(('GET', path))
                if path == '/version': return self.reply({'version': 'synthetic'})
                if path == '/group/Main':
                    return self.reply({'type': 'Selector', 'now': state['selected'], 'all': ['Primary', 'Backup']})
                if path == '/proxies/Primary/delay':
                    return self.reply({'delay': 20 if state['primary_ok'] else 0})
                if path == '/group/Main/delay':
                    return self.reply({'Primary': 20 if state['primary_ok'] else 0, 'Backup': 30 if state['backup_ok'] else 0})
                self.reply({'error': 'unexpected request'}, 400)
            def do_PUT(self):
                path = urlsplit(self.path).path; state['calls'].append(('PUT', path))
                if path != '/proxies/Main': return self.reply({}, 400)
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if data.get('name') not in {'Primary', 'Backup'}: return self.reply({}, 400)
                state['selected'] = data['name']; self.reply({})
        socket = directory / 'controller.sock'
        server = socketserver.UnixStreamServer(str(socket), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        config = directory / 'ppsub.json'
        config.write_text(json.dumps({'global_monitor': {'enable': False}, 'node-groups': [
            {'name': 'Main', 'failover': True, 'monitor_url': 'https://example.invalid/check',
             'expected_status': '204', 'retries': 0}, {'name': 'Manual', 'failover': False}]}))
        try:
            for name, healthy, backup, expected in [('healthy_primary', True, True, 'Primary'),
                                                    ('failed_primary', False, True, 'Backup'),
                                                    ('all_failed', False, False, 'Primary')]:
                state.update(selected='Primary', primary_ok=healthy, backup_ok=backup, calls=[])
                result = subprocess.run([str(binary), 'ppgw', '-apiurl', 'unix://' + str(socket),
                                         '-healthcheck', str(config)], capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stderr
                assert state['selected'] == expected, (name, state['selected'], result.stdout)
                writes = [call for call in state['calls'] if call[0] != 'GET']
                assert writes == ([('PUT', '/proxies/Main')] if name == 'failed_primary' else []), writes
                assert not any('Manual' in path or 'connections' in path or 'configs' in path for _, path in state['calls'])
                evidence.append({'case': name, 'selected': expected, 'writes': len(writes)})
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)
    return {'synthetic_only': True, 'outbound_probes': False, 'real_cli': True, 'cases': evidence}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.binary.resolve()), indent=2))
