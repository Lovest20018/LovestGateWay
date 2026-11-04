#!/usr/bin/env python3
"""Exercise real Web settings transactions only in a marked disposable Debian fixture."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--rollback-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path('/run/management-validation-only').is_file():
        parser.error('requires root and a disposable fixture marker')
    sys.path.insert(0, '/usr/local/lib/paopaogw')
    from manage_common import Layout
    from manage_model import load_settings
    from manage_runtime import System
    layout = Layout(); system = System(layout, timeout=12)
    opener = build_opener(ProxyHandler({}))
    checks = []

    def record(name):
        checks.append(name)
        args.report.write_text(json.dumps({'complete': False, 'passed': checks}, indent=2) + '\n')
        print('PASS ' + name, flush=True)

    def base():
        host = load_settings(layout)['service']['PPGW_WEB_LISTEN'] or '0.0.0.0:80'
        return 'http://' + host.replace('0.0.0.0:', '127.0.0.1:')

    def request(path, body=None, token=None, expected=200):
        if token is None: token = Path('/var/lib/paopaogw/core-token').read_text().strip()
        req = Request(base() + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            response = opener.open(req, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(); status = response.code
        assert status == expected, (path, status, raw[:200])
        if not raw: return None
        try: return json.loads(raw)
        except json.JSONDecodeError: return raw

    def show(): return request('/sniffbox/settings/config')

    def submit(kind, payload, success=True):
        identifier = uuid.uuid4().hex
        body = {'id': identifier, 'kind': kind, 'payload': payload}
        request('/sniffbox/settings/apply', body, expected=202)
        deadline = time.monotonic() + 140
        while time.monotonic() < deadline:
            try: value = request('/sniffbox/settings/jobs/' + identifier)
            except (OSError, URLError, AssertionError):
                time.sleep(.3); continue
            if value['status'] in {'succeeded', 'failed'}:
                assert (value['status'] == 'succeeded') == success, value
                return value
            time.sleep(.3)
        raise AssertionError('Web job did not finish')

    def config(patch):
        payload = {'base_revision': show()['revision'], 'patch': patch}
        preview = request('/sniffbox/settings/preview', payload)
        return preview, submit('config', payload)

    system.wait(load_settings(layout)); system.wait_api(load_settings(layout))
    if args.rollback_only:
        config_file = Path('/etc/paopaogw/api.json')
        before = config_file.read_bytes()
        worker = system.command('systemctl', 'show', 'paopaogw-settings', '--property=MainPID', '--value').stdout.strip()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0)); occupied.listen()
            payload = {'base_revision': show()['revision'], 'patch': {'api': {'port': occupied.getsockname()[1]}}}
            job = submit('config', payload, success=False)
        assert job['error'] == 'APPLY_FAILED_ROLLED_BACK', job
        assert config_file.read_bytes() == before
        assert system.command('systemctl', 'show', 'paopaogw-settings', '--property=MainPID', '--value').stdout.strip() == worker
        system.wait_api(load_settings(layout))
        record('real_web_job_readiness_failure_rolls_back_without_restarting_worker')
        args.report.write_text(json.dumps({'complete': True, 'passed': checks, 'fixture_only': True}, indent=2) + '\n')
        print('COMPLETE real Web job rollback scenario', flush=True)
        return
    for path in ('/sniffbox/settings/config', '/sniffbox/settings/policy'):
        request(path, token='invalid', expected=401)
        request(path, token=Path('/etc/paopaogw/read-token').read_text().strip(), expected=401)
    request('/sniffbox/settings/config?url=http://example.com', expected=405)
    value = show()
    assert 'schema' in value and value['settings']['ini']['clash_web_password'] == '********'
    record('real_core_bridge_admin_auth_and_observer_rejection')

    preview, _ = config({'ini': {'net_rec': True, 'openport': True,
                               'openport_auth': 'fixture:private-fixture-password'}})
    assert preview['action'] == 'restart'
    record('worker_survives_core_restart_and_job_is_queryable')

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(4096): self.request.sendall(data)
    server = socketserver.ThreadingTCPServer(('0.0.0.0', 0), Echo)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stream = None
    try:
        interface = load_settings(layout)['service']['PPGW_INTERFACE']
        addresses = json.loads(system.command('ip', '-4', '-j', 'addr', 'show', 'dev', interface).stdout)
        proxy_host = next(a['local'] for row in addresses for a in row['addr_info'] if a['scope'] == 'global')
        stream = socket.create_connection((proxy_host, 1080), timeout=10)
        target = '127.0.0.1:' + str(server.server_address[1])
        auth = base64.b64encode(b'fixture:private-fixture-password').decode()
        stream.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n'.encode())
        response = b''
        while b'\r\n\r\n' not in response: response += stream.recv(4096)
        assert b'200' in response.split(b'\r\n')[0], response[:100]
        def echo(message):
            stream.sendall(message); assert stream.recv(len(message)) == message
        echo(b'before-hot-reload')
        boot = system.runtime()['boot_id']
        worker = system.command('systemctl', 'show', 'paopaogw-settings', '--property=MainPID', '--value').stdout.strip()
        old_token = Path('/var/lib/paopaogw/core-token').read_text().strip()
        preview, job = config({'ini': {'max_rec': value['settings']['ini']['max_rec'] + 1,
                                       'clash_web_password': 'rotated-private-fixture-password'}})
        assert preview['action'] == 'reload' and job['result']['action'] == 'reload'
        assert system.runtime()['boot_id'] == boot
        assert system.command('systemctl', 'show', 'paopaogw-settings', '--property=MainPID', '--value').stdout.strip() == worker
        request('/sniffbox/settings/config', token=old_token, expected=401)
        echo(b'after-hot-reload-and-password-rotation')
        record('password_and_statistics_hot_reload_keep_existing_proxy_stream')
    finally:
        if stream is not None: stream.close()
        server.shutdown(); server.server_close()

    boot = system.runtime()['boot_id']
    config({'maintenance': {'history_enabled': True, 'history_retention_days': 7}})
    assert system.runtime()['boot_id'] == boot and system.status()['paopaogw-observe']['active']
    record('history_worker_change_does_not_restart_core')

    document = {'proxies': [{'name': 'retained-node', 'type': 'ss', 'server': '192.0.2.100',
                            'port': 1234, 'cipher': 'aes-128-gcm', 'password': 'hidden-fixture-node-secret'}],
                'proxy-groups': [{'name': 'Fixture', 'type': 'select', 'proxies': ['DIRECT'], 'hidden': True}],
                'rules': ['MATCH,Fixture'], 'dns': {'enable': False}}
    import yaml
    submit('asset', {'type': 'yaml', 'name': 'web-fixture.yaml',
                     'content': yaml.safe_dump(document), 'overwrite': False})
    config({'ini': {'mode': 'yaml', 'yamlfile': 'web-fixture.yaml', 'fast_node': 'no'}})
    before = request('/sniffbox/settings/policy')
    assert before['available'] and 'hidden-fixture-node-secret' not in json.dumps(before)
    policy = {key: before[key] for key in ('groups', 'providers', 'rules')}
    policy['groups'][0]['name'] = 'RenamedFixture'
    payload = {'snapshot_id': before['snapshot_id'], 'base_revision': before['base_revision'], 'policy': policy}
    assert request('/sniffbox/settings/policy/preview', payload)['action'] == 'restart'
    submit('policy', payload)
    after = request('/sniffbox/settings/policy')
    assert after['groups'][0]['name'] == 'RenamedFixture' and 'MATCH,RenamedFixture' in after['rules']
    from clash_ppsub import running_document
    current = running_document()
    assert current['proxies'][0]['password'] == 'hidden-fixture-node-secret'
    assert current['proxy-groups'][0]['hidden'] is True
    request('/sniffbox/settings/policy/preview', payload, expected=409)
    record('real_mihomo_validation_native_policy_rename_and_hidden_fields_retained')
    pid = system.command('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout.strip()
    root = Path('/proc') / pid / 'root/tmp'
    clash_pid = system.command('pgrep', '-x', 'clash').stdout.strip()
    digest = hashlib.sha256((root / 'clash.yaml').read_bytes()).hexdigest()
    boot = system.runtime()['boot_id']
    config({'ini': {'splice_threshold': 105000}})
    assert system.runtime()['boot_id'] == boot
    assert hashlib.sha256((root / 'clash.yaml').read_bytes()).hexdigest() == digest
    assert system.command('pgrep', '-x', 'clash').stdout.strip() == clash_pid
    record('mihomo_mode_core_only_reload_keeps_generated_config_and_boot')
    submit('policy_reset', {'base_revision': show()['revision']})
    assert not Path('/etc/paopaogw/files/clash-policy.json').exists()
    assert request('/sniffbox/settings/policy')['groups'][0]['name'] == 'Fixture'
    record('policy_reset_restores_original_source')
    jobs = list(Path('/var/lib/paopaogw/admin/web-jobs').glob('*.json'))
    assert all('rotated-private-fixture-password' not in p.read_text() and 'hidden-fixture-node-secret' not in p.read_text() for p in jobs)
    record('persisted_job_receipts_do_not_store_credentials')
    args.report.write_text(json.dumps({'complete': True, 'passed': checks,
                                      'fixture_only': True}, indent=2) + '\n')
    print('COMPLETE ' + str(len(checks)) + ' Web settings scenarios', flush=True)


if __name__ == '__main__': main()
