#!/usr/bin/env python3
"""Exercise saved selections with a real Mihomo in a disposable isolated container.

No subscription or production config is needed. Use --binary to select the core;
the only controller is a temporary Unix socket and no proxy/DNS ports are opened.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from clash_selection import saved_global


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=3)
        self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ppgw-selection-') as temporary:
        root = Path(temporary); config = root / 'config.yaml'; cache = root / 'cache.db'
        control = root / 'control.sock'; log = (root / 'core.log').open('w')
        process = None

        def configure(extra=''):
            config.write_text('mode: global\nlog-level: error\nexternal-controller-unix: ' + str(control)
                              + '\nprofile:\n  store-selected: true\nproxies: []\nproxy-groups:\n'
                              + '  - name: "Saved Group"\n    type: select\n    proxies: [DIRECT, REJECT]\n'
                              + extra + 'rules: ["MATCH,DIRECT"]\n')

        def request(path='/proxies', method='GET', data=None):
            connection = UnixHTTP(control)
            try:
                connection.request(method, path, json.dumps(data) if data is not None else None,
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse(); body = response.read()
                assert response.status < 300, (response.status, body)
                return json.loads(body) if body else None
            finally: connection.close()

        def start(expected_group='Saved Group'):
            nonlocal process
            process = subprocess.Popen([args.binary, '-d', str(root), '-f', str(config)], stdout=log, stderr=log)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, 'Mihomo stopped before readiness'
                try:
                    if expected_group in request().get('proxies', {}).get('GLOBAL', {}).get('all', []):
                        return
                except (OSError, http.client.HTTPException): pass
                time.sleep(.05)
            raise AssertionError('Mihomo readiness timeout')

        def stop(kill=False):
            nonlocal process
            if process:
                process.kill() if kill else process.terminate()
                process.wait(timeout=10); process = None

        def choice(name):
            request('/proxies/GLOBAL', 'PUT', {'name': name})
            # The successful response follows Mihomo's cache update.
            assert request()['proxies']['GLOBAL']['now'] == name

        def read_unchanged(expected):
            before = hashlib.sha256(cache.read_bytes()).hexdigest()
            assert saved_global(cache) == expected
            assert hashlib.sha256(cache.read_bytes()).hexdigest() == before

        try:
            configure()
            subprocess.run([args.binary, '-t', '-d', str(root), '-f', str(config)], check=True, stdout=log, stderr=log)
            assert saved_global(cache) == ''; checks['preflight_cache_is_not_selection'] = True
            start(); stop()
            assert saved_global(cache) == ''; checks['unselected_cache_is_not_selection'] = True
            start(); choice('Saved Group'); stop(); read_unchanged('Saved Group')
            start(); assert request()['proxies']['GLOBAL']['now'] == 'Saved Group'; stop()
            checks['named_choice_survives_cold_restart'] = True
            config.write_text(config.read_text().replace('Saved Group', 'Replacement'))
            start('Replacement'); assert request()['proxies']['GLOBAL']['now'] != 'Saved Group'; stop()
            checks['removed_choice_does_not_match_restored_node'] = True
            configure()
            start(); choice('DIRECT'); stop(); read_unchanged('DIRECT')
            start(); assert request()['proxies']['GLOBAL']['now'] == 'DIRECT'; stop()
            checks['explicit_direct_survives_cold_restart'] = True
            start(); choice('REJECT'); stop(kill=True); read_unchanged('REJECT')
            start(); assert request()['proxies']['GLOBAL']['now'] == 'REJECT'; stop()
            checks['choice_survives_sigkill'] = True
            # Make selected bucket non-inline and large enough for branch pages.
            configure(''.join(f'  - name: "Fixture group {i:04d}"\n    type: select\n    proxies: [DIRECT, REJECT]\n' for i in range(450)))
            start()
            for i in range(450):
                request('/proxies/' + quote(f'Fixture group {i:04d}', safe=''), 'PUT', {'name': 'REJECT'})
            choice('Saved Group'); stop(); read_unchanged('Saved Group')
            checks['large_selected_bucket'] = True
            original = cache.read_bytes()
            # A destroyed newest metadata page must use the older valid snapshot.
            import struct
            page_size = struct.unpack_from('<I', original, 24)[0]
            latest = max(range(2), key=lambda i: struct.unpack_from('<Q', original, i * page_size + 64)[0])
            damaged = bytearray(original); damaged[latest * page_size + 72] ^= 1
            cache.write_bytes(damaged)
            assert saved_global(cache) in {'Saved Group', 'REJECT'}
            checks['bad_latest_meta_uses_valid_snapshot'] = True
            damaged[ (1 - latest) * page_size + 72] ^= 1; cache.write_bytes(damaged)
            assert saved_global(cache) == ''; checks['bad_both_meta_refused'] = True
            cache.write_bytes(original[:100]); assert saved_global(cache) == ''
            checks['truncated_cache_refused'] = True
        finally:
            stop(); log.close()
    report = {'synthetic_only': True, 'production_configuration_used': False,
              'binary_sha256': hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(), 'checks': checks}
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__': main()
