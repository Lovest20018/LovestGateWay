#!/usr/bin/env python3
"""Exercise the real startup function with delayed Mihomo cache restoration.

Run in an isolated Linux container with --network none. Only temporary synthetic
configuration, a Unix controller and a loopback HTTP target are used. No systemd,
production configuration, host listeners or production cache files are touched.
"""
import argparse
import fcntl
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from clash_selection import saved_global


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=3); self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3); self.sock.connect(self.path)


class Target(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204); self.send_header('Connection', 'close'); self.end_headers()
    def log_message(self, *args): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mihomo', type=Path, required=True)
    parser.add_argument('--sniffbox', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checks = {}
    server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source = (ROOT / 'FILES/usr/bin/ppg.sh').read_text()
    source = source[source.index('load_clash() {'):source.index('\nload_ovpn() {')]
    with tempfile.TemporaryDirectory(prefix='selection-startup-') as temporary:
        root = Path(temporary); root.chmod(0o755)
        config, control, cache = root/'clash.yaml', root/'clash.sock', root/'cache.db'
        log = (root/'core.log').open('w'); owned_pid = None
        def request(method='GET', path='/proxies/GLOBAL', data=None):
            connection = UnixHTTP(control)
            try:
                connection.request(method, path, json.dumps(data) if data is not None else None,
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse(); raw = response.read()
                assert response.status < 300
                return json.loads(raw) if raw else None
            finally: connection.close()
        def wait_choice(choice=None):
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                try:
                    group = request()
                    if 'Fixture Main' in group.get('all', []) and (choice is None or group.get('now') == choice):
                        return group
                except (OSError, http.client.HTTPException, AssertionError): pass
                time.sleep(.02)
            raise AssertionError('selection readiness timed out')
        def stop():
            nonlocal owned_pid
            if owned_pid is not None:
                os.kill(owned_pid, signal.SIGTERM)
                deadline = time.monotonic()+5
                while Path('/proc/'+str(owned_pid)).exists() and time.monotonic()<deadline:
                    status = Path('/proc/'+str(owned_pid)+'/stat').read_text().rsplit(')',1)[1].split()[0]
                    if status == 'Z': break
                    time.sleep(.02)
                else:
                    if Path('/proc/'+str(owned_pid)).exists(): os.kill(owned_pid, signal.SIGKILL)
                owned_pid = None
        def configure(removed=False):
            groups = ['Fixture Main', '香港 🇭🇰 Fixture'] + ([] if removed else ['Removed Fixture'])
            config.write_text('mode: global\nlog-level: error\nexternal-controller-unix: '+str(control)
                              +'\nprofile: {store-selected: true}\nproxies: []\nproxy-groups:\n'
                              +''.join('  - {name: '+json.dumps(name,ensure_ascii=False)+', type: select, proxies: [DIRECT]}\n' for name in groups)
                              +'rules: ["MATCH,DIRECT"]\n')
        transformed = source.replace('/tmp/', str(root)+'/').replace('/etc/hosts', str(root/'hosts'))
        transformed = transformed.replace('/etc/config/clash/', str(root)+'/')
        transformed = transformed.replace('/usr/bin/python3 /usr/local/lib/paopaogw/clash_selection.py',
                                          shlex.quote(sys.executable)+' '+shlex.quote(str(ROOT/'integration/clash_selection.py')))
        q = shlex.quote
        script = '''
set -e
log() { printf '%s\n' "$1"; }
ulimit() { echo 1048576; }; load_ini() { :; }; sync_ntp() { :; }
clash() { :; }; kill_clash() { echo unexpected-kill >>calls; }
pidof() { [ -f started ]; }
ppgw() {
    case "$1" in
        -clash-up) "$MIHOMO" -d "$PWD" -f "$PWD/clash.yaml" >>core.log 2>&1 & echo $! >core.pid; touch started;;
        -clash-ready) "$SNIFFBOX" ppgw "$@" -apiurl "unix://$PWD/clash.sock"; touch api-ready;;
        -fastnode) echo fastest >>calls; "$SNIFFBOX" ppgw "$@" -apiurl "unix://$PWD/clash.sock";;
        -genhost|-rulehash) :;;
        *) "$SNIFFBOX" ppgw "$@" -apiurl "unix://$PWD/clash.sock";;
    esac
}
dns_ip=127.0.0.1; dns_port=53; mode=yaml; fast_node=yes; fall_direct=no; ext_node=
PPGW_DEBIAN=1
''' + 'PPGWTTY='+q(str(root/'controller.log'))+'\ntest_node_url='+q('http://127.0.0.1:'+str(server.server_port)+'/')+'\n' + transformed + '\nload_clash yes\n'
        try:
            for choice in ['REJECT', '香港 🇭🇰 Fixture', 'DIRECT', 'Removed Fixture']:
                configure()
                initial = subprocess.Popen([str(args.mihomo), '-d', str(root), '-f', str(config)], stdout=log, stderr=log)
                owned_pid = initial.pid
                wait_choice(); request('PUT', data={'name': choice}); wait_choice(choice); stop(); initial.wait(timeout=5)
                assert saved_global(cache) == choice
                removed = choice == 'Removed Fixture'
                if removed: configure(removed=True)
                for name in ['started', 'api-ready', 'calls', 'core.pid', 'clash.sock']:
                    (root/name).unlink(missing_ok=True)
                with cache.open('rb') as locked:
                    if not removed: fcntl.flock(locked, fcntl.LOCK_EX)
                    process = subprocess.Popen(['sh','-s'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True, cwd=root,
                                               env={**os.environ,'MIHOMO':str(args.mihomo),'SNIFFBOX':str(args.sniffbox)})
                    process.stdin.write(script); process.stdin.close(); process.stdin=None
                    deadline = time.monotonic()+10
                    while not (root/'api-ready').exists():
                        assert process.poll() is None, 'startup exited before API readiness'
                        assert time.monotonic()<deadline
                        time.sleep(.005)
                    owned_pid = int((root/'core.pid').read_text())
                    if not removed:
                        # Expand the exact previously observed API/profile window.
                        time.sleep(.25)
                        assert not (root/'calls').exists(), 'startup raced native restore with speed selection'
                        fcntl.flock(locked, fcntl.LOCK_UN)
                    output, errors = process.communicate(timeout=25)
                    assert process.returncode == 0, output+errors
                actual = wait_choice()
                if removed:
                    assert (root/'calls').read_text().splitlines() == ['fastest']
                    assert actual['now'] != choice
                    checks['confirmed_removal_runs_existing_speed_selection'] = True
                else:
                    assert actual['now'] == choice
                    assert not (root/'calls').exists()
                    assert 'Preserved saved Clash node selection.' in output
                    checks['delayed_'+{'REJECT':'reject','DIRECT':'direct','香港 🇭🇰 Fixture':'unicode'}[choice]+'_preserved_without_speed_selection'] = True
                stop()
                assert saved_global(cache) == actual['now']
        finally:
            stop(); log.close(); server.shutdown(); server.server_close()
    report = {'complete':True,'synthetic_only':True,'production_configuration_used':False,
              'checks':checks,'mihomo_sha256':hashlib.sha256(args.mihomo.read_bytes()).hexdigest(),
              'sniffbox_sha256':hashlib.sha256(args.sniffbox.read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__': main()
