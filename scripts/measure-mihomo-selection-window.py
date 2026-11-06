"""Measure fresh-connection routing during reload, using only isolated loopback fixtures.

Run inside a network-none container. Each destination returns a distinct marker,
so a successful TCP connection to the wrong exit is a failure too. The optional
controller delay is an explicitly labelled stress case, not a natural latency.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from mihomo_reload import UnixController, reload_verified


def receive(sock, size):
    result = b''
    while len(result) < size:
        part = sock.recv(size - len(result))
        if not part:
            raise EOFError()
        result += part
    return result


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while self.request.recv(1):
                self.request.sendall(b'D')
        except OSError:
            pass


class Exit(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            assert receive(self.request, 1) == b'\x05'
            receive(self.request, receive(self.request, 1)[0])
            self.request.sendall(b'\x05\x00')
            head = receive(self.request, 4)
            receive(self.request, 4 if head[3] == 1 else receive(self.request, 1)[0])
            receive(self.request, 2)
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            while self.request.recv(1):
                self.request.sendall(self.server.marker)
        except (OSError, EOFError, AssertionError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--rounds', type=int, default=12)
    args = parser.parse_args()
    binary = args.binary.resolve()
    work = args.work_dir.resolve()
    work.mkdir(exist_ok=False)
    report = {'synthetic_only': True, 'production_touched': False,
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'version': subprocess.check_output([str(binary), '-v'], text=True).strip(), 'cases': []}
    servers = []
    for handler, marker in [(Echo, b'D'), (Exit, b'A'), (Exit, b'B')]:
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
        server.daemon_threads = True
        server.marker = marker
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    target_port = servers[0].server_address[1]
    def save():
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    try:
        for mode in ['rule', 'global']:
            for default in ['REJECT', 'DIRECT', 'Other']:
                for delay_ms in [0, 50]:
                    directory = work / f'{mode}-{default}-{delay_ms}'
                    directory.mkdir()
                    # No prior state directory: the production default is explicitly on.
                    config = {'socks-port': 19080, 'allow-lan': False, 'bind-address': '127.0.0.1',
                              'mode': mode, 'log-level': 'silent', 'dns': {'enable': False},
                              'external-controller-unix': str(directory / 'controller.sock'),
                              'profile': {'store-selected': True},
                              'proxies': [{'name': name, 'type': 'socks5', 'server': '127.0.0.1',
                                           'port': server.server_address[1]} for name, server in zip(['Chosen', 'Other'], servers[1:])],
                              'proxy-groups': [{'name': 'MAIN', 'type': 'select', 'proxies': [default, 'Chosen']}],
                              'rules': ['MATCH,MAIN']}
                    if mode == 'global':
                        config['proxy-groups'].append({'name': 'GLOBAL', 'type': 'select', 'proxies': [default, 'Chosen', 'MAIN']})
                    loaded = directory / 'config.json'
                    loaded.write_text(json.dumps(config))
                    log = (directory / 'mihomo.log').open('w')
                    process = subprocess.Popen([str(binary), '-d', str(directory / 'state'), '-f', str(loaded)], stdout=log, stderr=subprocess.STDOUT)
                    controller = UnixController(directory / 'controller.sock')
                    stop = threading.Event()
                    threads = []
                    keep = None
                    try:
                        for _ in range(200):
                            try:
                                ready, _ = controller.request('GET', '/proxies')
                                if 'MAIN' in ready.get('proxies', {}):
                                    break
                            except (OSError, ValueError):
                                time.sleep(.025)
                        else:
                            raise AssertionError('fixture controller did not start')
                        for group in (['MAIN', 'GLOBAL'] if mode == 'global' else ['MAIN']):
                            controller.request('PUT', '/proxies/' + group, {'name': 'Chosen' if group == 'MAIN' or mode == 'global' else 'MAIN'})
                        def connect():
                            sock = socket.create_connection(('127.0.0.1', 19080), timeout=1)
                            sock.settimeout(1)
                            try:
                                sock.sendall(b'\x05\x01\x00')
                                assert receive(sock, 2) == b'\x05\x00'
                                sock.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', target_port))
                                head = receive(sock, 4)
                                if head[1]:
                                    raise OSError('rejected')
                                receive(sock, (4 if head[3] == 1 else 16) + 2)
                                return sock
                            except BaseException:
                                sock.close()
                                raise
                        keep = connect()
                        keep.sendall(b'k')
                        assert receive(keep, 1) == b'A'
                        baseline = []
                        for _ in range(20):
                            with connect() as sock:
                                sock.sendall(b'b')
                                baseline.append(receive(sock,1).decode())
                        assert baseline == ['A'] * 20, 'baseline routing must be healthy'
                        observations = []
                        windows = []
                        def probe():
                            while not stop.is_set():
                                started = time.monotonic_ns()
                                try:
                                    with connect() as sock:
                                        sock.sendall(b'p')
                                        marker = receive(sock, 1).decode()
                                except (OSError, EOFError, AssertionError) as error:
                                    marker = 'failed'
                                    failure = type(error).__name__+':'+str(error)
                                observations.append({'started_ns': started, 'ended_ns': time.monotonic_ns(), 'exit': marker, **({'error':failure} if marker=='failed' else {})})
                                time.sleep(.002)
                        for _ in range(2):
                            thread = threading.Thread(target=probe, daemon=True)
                            thread.start()
                            threads.append(thread)
                        class Measured:
                            def request(self, method, path, body=None):
                                result = controller.request(method, path, body)
                                if method == 'PUT' and path.startswith('/configs?'):
                                    windows[-1]['put_ack_ns'] = time.monotonic_ns()
                                    if delay_ms:
                                        time.sleep(delay_ms / 1000)
                                return result
                        before = deepcopy(config)
                        for index in range(args.rounds):
                            after = deepcopy(before)
                            after['rules'] = [f'DOMAIN,unused-{index}.invalid,DIRECT', 'MATCH,MAIN']
                            windows.append({'start_ns': time.monotonic_ns()})
                            reload_verified(before, after, Measured(), lambda doc: None,
                                            lambda doc: loaded.write_text(json.dumps(doc)))
                            windows[-1]['verified_ns'] = time.monotonic_ns()
                            keep.sendall(b'k')
                            assert receive(keep, 1) == b'A', 'existing connection changed'
                            before = after
                        stop.set()
                        for thread in threads:
                            thread.join(2)
                        assert all(not thread.is_alive() for thread in threads)
                        bad = [row for row in observations if row['exit'] != 'A']
                        result = {'mode': mode, 'default': default, 'post_put_stress_delay_ms': delay_ms,
                                  'store_selected': True, 'healthy_baseline_connections':len(baseline), 'rounds': args.rounds, 'fresh_connections': len(observations),
                                  'wrong_exit': sum(row['exit'] in ['B', 'D'] for row in bad),
                                  'failed': sum(row['exit'] == 'failed' for row in bad),
                                  'existing_connection_preserved': True,
                                  'max_put_ack_to_verified_ms': max((w['verified_ns'] - w['put_ack_ns']) / 1e6 for w in windows),
                                  'windows': windows, 'observations': observations}
                        report['cases'].append(result)
                        save()
                        print(json.dumps({key: value for key, value in result.items() if key not in ['windows', 'observations']}), flush=True)
                    finally:
                        stop.set()
                        for thread in threads:
                            thread.join(2)
                        if keep:
                            keep.close()
                        process.terminate()
                        process.wait(timeout=10)
                        log.close()
        report['complete'] = True
        save()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
