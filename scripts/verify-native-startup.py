#!/usr/bin/env python3
"""Linux regression checks for native startup, without changing gateway state."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--address', default='127.0.0.1')
    parser.add_argument('--foreign-pid', type=int, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and args.foreign_pid > 1
    ip = ipaddress.ip_address(args.address)
    report = {}
    with tempfile.TemporaryDirectory(prefix='ppgw-startup-') as temporary:
        private = Path(temporary) / 'private.yaml'
        private.write_text('proxy-providers: {test: {url: "https://127.0.0.1/test"}}\n')
        private.chmod(0o600)
        result = subprocess.run([str(args.binary), 'ppgw', '-genhost', str(private)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and '127.0.0.1' in result.stdout, result.stderr
        assert private.stat().st_mode & 0o777 == 0o600
        report['genhost_reads_private_configuration_before_dropping_privileges'] = True
    # The endpoint keeps its loopback-only peer restriction on a LAN-bound UI.
    with socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET) as listener:
        listener.bind((str(ip), 0)); listener.listen(1); listener.settimeout(10)
        port = listener.getsockname()[1]
        peers = []
        def serve():
            stream, peer = listener.accept()
            with stream:
                stream.settimeout(5)
                request = stream.recv(8192)
                assert request.startswith(b'POST /sniffbox/clash/up ')
                peers.append(peer[0])
                stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        server = threading.Thread(target=serve, daemon=True); server.start()
        host = '[' + str(ip) + ']' if ip.version == 6 else str(ip)
        env = {**os.environ, 'PPGW_DEBIAN': '1', 'PPGW_WEB_LISTEN': f'{host}:{port}',
               'HTTP_PROXY': 'http://127.0.0.1:1', 'http_proxy': 'http://127.0.0.1:1'}
        result = subprocess.run([str(args.binary), 'ppgw', '-clash-up'], env=env,
                                capture_output=True, text=True, timeout=20)
        server.join(timeout=1)
        assert result.returncode == 0 and peers and ipaddress.ip_address(peers[0]).is_loopback
        report['custom_listener_uses_loopback_source_and_ignores_proxy_environment'] = True
    script = '''
. "$1"
ppgw_owned_pid "$$" sh || exit 10
if ppgw_owned_pid "$2" clash; then exit 11; fi
# Multicall helpers have arguments and must not be adopted as the server.
if ppgw_owned_pid "$$" sniffbox; then exit 12; fi
'''
    result = subprocess.run(['/bin/sh', '-c', script, 'verify', str(args.runtime), str(args.foreign_pid)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report['shell_ownership_rejects_foreign_cgroup_and_multicall_helpers'] = True
    print(json.dumps(report))


if __name__ == '__main__':
    main()
