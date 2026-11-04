#!/usr/bin/env python3
"""Real Token/listener transactions only inside a marked disposable Debian container."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if (os.geteuid() != 0 or not Path('/run/management-validation-only').is_file()
            or not Path('/run/systemd/container').is_file()):
        parser.error('requires a marked disposable systemd container')
    sys.path.insert(0, '/usr/local/lib/paopaogw')
    from manage_common import Layout
    from manage_model import load_settings
    from manage_runtime import System
    layout = Layout(); system = System(layout)
    opener = build_opener(ProxyHandler({}))
    report = {'fixture_only': True, 'complete': False, 'passed': []}

    def record(name):
        report['passed'].append(name)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print('PASS ' + name, flush=True)

    def pid(unit):
        return subprocess.check_output(['systemctl', 'show', unit, '--property=MainPID', '--value'], text=True).strip()

    settings = load_settings(layout)
    web = 'http://' + settings['service']['PPGW_WEB_LISTEN'].replace('0.0.0.0:', '127.0.0.1:')
    admin = Path('/var/lib/paopaogw/core-token').read_text().strip()
    original = Path('/etc/paopaogw/read-token').read_text().strip()
    core_pid, worker_pid = pid('paopaogw'), pid('paopaogw-settings')

    def call(base, path, token, body=None, expected=200):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
        if body is not None: headers['Origin'] = base
        request = Request(base + path, None if body is None else json.dumps(body).encode(), headers)
        try: response = opener.open(request, timeout=8)
        except HTTPError as error: response = error
        with response:
            raw = response.read()
            assert response.status == expected, (path, response.status, expected)
            try: value = json.loads(raw) if raw else None
            except json.JSONDecodeError: value = raw
            return value, response.headers

    def show(): return call(web, '/sniffbox/settings/config', admin)[0]

    def apply(patch, success=True):
        payload = {'base_revision': show()['revision'], 'patch': patch}
        preview = call(web, '/sniffbox/settings/preview', admin, payload)[0]
        assert preview['effects']['core'] == 'none'
        assert preview['effects']['services'] == ['api']
        if 'read_token' in patch['api']:
            assert patch['api']['read_token'] not in json.dumps(preview)
        identifier = uuid.uuid4().hex
        call(web, '/sniffbox/settings/apply', admin,
             {'id': identifier, 'kind': 'config', 'payload': payload}, expected=202)
        deadline = time.monotonic() + 145
        while time.monotonic() < deadline:
            job = call(web, '/sniffbox/settings/jobs/' + identifier, admin)[0]
            if job['status'] in {'succeeded', 'failed'}:
                assert (job['status'] == 'succeeded') == success, job.get('error')
                assert pid('paopaogw') == core_pid and pid('paopaogw-settings') == worker_pid
                return job
            time.sleep(.5)
        raise AssertionError('settings job timeout')

    assert show()['settings']['api']['read_token'] == '********'
    for token in (original, 'incorrect'):
        call(web, '/sniffbox/settings/observer-token', token, {}, expected=401)
    call(web, '/sniffbox/settings/observer-token', admin, expected=405)
    call(web, '/sniffbox/settings/observer-token?x=1', admin, {}, expected=405)
    value, headers = call(web, '/sniffbox/settings/observer-token', admin, {})
    assert value == {'token': original} and headers['Cache-Control'] == 'no-store'
    record('core_bridge_admin_post_reveal_observer_rejected_no_store')

    addresses = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0']))
    address = next(item['local'] for link in addresses for item in link['addr_info'] if item['scope'] == 'global')
    replacement = secrets.token_hex(32)
    with socket.socket() as available:
        available.bind((address, 0)); port = available.getsockname()[1]
    apply({'api': {'listen': address, 'port': port, 'read_token': replacement}})
    api = f'http://{address}:{port}'
    call(api, '/api/v1/instance', original, expected=401)
    call(api, '/api/v1/instance', replacement)
    call(api, '/api/v1/instance', admin, expected=401)
    assert 'read_token' not in json.loads(Path('/etc/paopaogw/api.json').read_bytes())
    assert Path('/etc/paopaogw/read-token').read_text().strip() == replacement
    assert call(web, '/sniffbox/settings/observer-token', admin, {})[0]['token'] == replacement
    assert replacement not in json.dumps(show())
    report['lan_address'] = address
    report['lan_port'] = port
    record('real_lan_listener_rotation_old_token_invalid_core_worker_unchanged')

    previous_api = Path('/etc/paopaogw/api.json').read_bytes()
    previous_token = Path('/etc/paopaogw/read-token').read_bytes()
    rejected = secrets.token_hex(32)
    with socket.socket() as occupied:
        occupied.bind((address, 0)); occupied.listen()
        job = apply({'api': {'port': occupied.getsockname()[1], 'read_token': rejected}}, success=False)
    assert job['error'] == 'APPLY_FAILED_ROLLED_BACK'
    assert Path('/etc/paopaogw/api.json').read_bytes() == previous_api
    assert Path('/etc/paopaogw/read-token').read_bytes() == previous_token
    call(api, '/api/v1/instance', replacement)
    call(api, '/api/v1/instance', rejected, expected=401)
    record('occupied_lan_port_restores_listener_token_without_core_worker_restart')

    jobs = Path('/var/lib/paopaogw/admin/web-jobs')
    assert all(not any(token in p.read_text() for token in (original, replacement, rejected)) for p in jobs.glob('*.json'))
    record('public_configuration_previews_and_persistent_jobs_redacted')
    report['complete'] = True
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('COMPLETE HomeNet real Debian fixture', flush=True)


if __name__ == '__main__': main()
