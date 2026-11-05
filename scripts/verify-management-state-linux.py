#!/usr/bin/env python3
"""Real service acceptance in a marked disposable Debian fixture, using loopback data."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path('/run/management-validation-only').is_file():
        parser.error('requires root and a marked disposable Debian fixture')
    sys.path.insert(0, '/usr/local/lib/paopaogw')
    from manage_common import Layout, operation_lock
    from manage_model import load_settings, settings_revision
    from manage_runtime import System
    from manage_subscription_schedule import identity
    from subscription_admin import update_config, atomic_write
    from manage_config import ConfigManager
    layout = Layout(); system = System(layout, timeout=25)
    config = Path('/etc/paopaogw/files/ppgw.ini')
    checks = []
    report = {'environment': 'isolated Debian on server 41', 'synthetic_only': True, 'passed': checks}
    opener = build_opener(ProxyHandler({}))
    passwords = {'A': 'fixture-password-one', 'B': 'fixture-password-one'}
    denied = set(); invalid = set(); blocked = set(); release_fetch = threading.Event()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            name = self.path.lstrip('/')
            requests.append(name)
            if name in blocked: release_fetch.wait(timeout=30)
            if name in denied:
                self.send_response(403); self.end_headers(); return
            cipher = 'invalid-fixture-cipher' if name in invalid else 'aes-128-gcm'
            body = ('proxies:\n  - {name: Node, type: ss, server: 192.0.2.10, port: 443, cipher: ' + cipher + ', password: ' + passwords[name] + '}\n').encode()
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def passed(name):
        checks.append(name)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print('PASS ' + name, flush=True)

    def request(path, body=None, timeout=10):
        base = system.client().base_url
        token = Path('/var/lib/paopaogw/core-token').read_text().strip()
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json', 'Origin':base})
        with opener.open(req, timeout=timeout) as response: return json.load(response)

    def running():
        return {'pid': subprocess.check_output(['systemctl','show','paopaogw','--property=MainPID','--value'], text=True).strip(),
                'boot_id': request('/sniffbox/info')['integration']['boot_id']}

    def refresh(names=None, expected='applied'):
        current = request('/sniffbox/subscription/config')
        identifier = 'fixture_' + uuid.uuid4().hex
        body = {'kind':'refresh_nodes','base_revision':current['base_revision'],
                'settings_revision':current['settings_revision'],'operation_id':identifier}
        if names is not None: body['providers'] = names
        request('/sniffbox/subscription/apply', body)
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                status = request('/sniffbox/subscription/status')
                operation = status.get('operation', {})
                if operation.get('id') == identifier and operation.get('status') != 'applying':
                    assert operation['status'] == expected, operation
                    return status
            except (OSError, ValueError): pass
            time.sleep(.3)
        raise AssertionError('subscription operation did not complete')

    try:
        cache = Path('/var/lib/paopaogw/subscriptions'); cache.mkdir(parents=True, exist_ok=True)
        providers = [{'name':name, 'url':f'http://127.0.0.1:{server.server_port}/{name}',
                      'update_mode':'manual'} for name in ('A','B')]
        source = config.parent / 'ppsub.json'
        source.write_text(json.dumps({'exported_at':'synthetic-fixture','subs':providers,
                                    'node-groups':[{'name':'Main','subs':['all']}], 'rules':[]}))
        source.chmod(0o600)
        subprocess.run(['/usr/local/lib/paopaogw/bin/sniffbox','ppgw','-ppsub',str(source),'-output','/var/tmp/seed.yaml'],
                       env=dict(os.environ, PPGW_SUB_CACHE_DIR=str(cache), PPGW_SUB_CACHE_ONLY='0', dns_burn='no'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
        atomic_write(config, update_config(config.read_bytes(), {'mode':'suburl', 'suburl':'ppsub@http://127.0.0.1/ppsub.json',
                            'fast_node':'no','subtime':'manual','dns_burn':'no'}))
        subprocess.run(['systemctl','restart','paopaogw'], check=True, timeout=45)
        system.wait(load_settings(layout))
        initial = running(); report['initial'] = initial
        status = refresh(expected='unchanged')
        assert running() == initial
        assert all(r['result']=='unchanged' for r in status['node_cache']['subscriptions'])
        passed('same_payload_keeps_core_pid_and_boot_id')
        completed_id = status['operation']['id']
        subprocess.run(['systemctl','restart','paopaogw-subscription'], check=True, timeout=15)
        for _ in range(50):
            try:
                restored = request('/sniffbox/subscription/status')['operation']
                break
            except OSError: time.sleep(.2)
        assert restored['id']==completed_id and restored['status']=='unchanged'
        assert running()==initial
        passed('completed_subscription_operation_survives_helper_restart')

        passwords['B'] = 'fixture-password-two'
        changed = refresh(['B'])
        assert running()['boot_id'] != initial['boot_id']
        assert json.loads((cache/(identity(providers[1])+'.json')).read_bytes())['raw_yaml'].find(passwords['B']) >= 0
        passed('password_only_change_applies_and_restarts_core')

        old_a = (cache/(identity(providers[0])+'.json')).read_bytes()
        success = next(r for r in changed['node_cache']['subscriptions'] if r['name']=='A')['succeeded_at']
        denied.add('A'); passwords['B'] = 'fixture-password-three'
        partial = refresh()
        assert (cache/(identity(providers[0])+'.json')).read_bytes()==old_a
        a = next(r for r in partial['node_cache']['subscriptions'] if r['name']=='A')
        b = next(r for r in partial['node_cache']['subscriptions'] if r['name']=='B')
        assert a['result']=='retained' and a['succeeded_at']==success and b['result']=='updated'
        passed('partial_failure_keeps_old_nodes_and_last_success')

        before = running(); denied.add('B')
        refresh()
        assert running()==before
        passed('all_fetches_failed_keep_running_service')

        denied.clear(); invalid.add('B')
        old_b = (cache/(identity(providers[1])+'.json')).read_bytes()
        refresh(['B'], expected='failed')
        assert running()==before and (cache/(identity(providers[1])+'.json')).read_bytes()==old_b
        passed('invalid_candidate_keeps_live_cache_and_core')
        invalid.clear()

        value = request('/sniffbox/settings/config')
        job_id = uuid.uuid4().hex
        request('/sniffbox/settings/apply', {'id':job_id,'kind':'config','payload':{
            'base_revision':value['revision'], 'patch':{'ini':{'splice_threshold':20}}}})
        for _ in range(100):
            result = request('/sniffbox/settings/jobs/'+job_id)
            if result['status'] in {'succeeded','failed'}: break
            time.sleep(.3)
        assert result['status']=='succeeded', result
        value = request('/sniffbox/settings/config')
        assert value['management']['state']=='loaded', value['management']
        assert any(r['id']==job_id for r in value['management']['recent'])
        assert any(r['source']=='subscription' for r in value['management']['recent'])
        passed('settings_and_subscription_share_persistent_operation_history')

        status = request('/sniffbox/settings/status')
        assert 'settings' not in status and 'schema' not in status and 'management' in status
        provider_data = request('/sniffbox/subscription/providers')
        assert provider_data['subscriptions']
        for endpoint in ('/sniffbox/settings/status', '/sniffbox/subscription/providers'):
            for authorization in (None, 'Bearer synthetic-observer-token'):
                headers = {} if authorization is None else {'Authorization':authorization}
                try:
                    opener.open(Request(system.client().base_url + endpoint,headers=headers),timeout=5)
                    raise AssertionError('unauthorized state request accepted')
                except HTTPError as error:
                    assert error.code == 401, error.code
                    error.close()
        passed('lightweight_status_routes_require_administrator_and_omit_full_configuration')

        # A synthetic updater exercises the real independent systemd lifecycle,
        # without external downloads or any binary installation.
        fixture_worker = Path('/var/tmp/management-lifecycle-worker.py')
        gate = Path('/var/lib/paopaogw/admin/fixture-maintenance-gate')
        fixture_worker.write_text('''import sys,time
from pathlib import Path
sys.path.insert(0,"/usr/local/lib/paopaogw")
import maintenance
class SyntheticUpdater:
 def check(self):
  while Path("/var/lib/paopaogw/admin/fixture-maintenance-gate").exists(): time.sleep(.1)
maintenance.Updater=SyntheticUpdater
maintenance.main()
''')
        dropin = Path('/etc/systemd/system/paopaogw-core-action@check.service.d/validation.conf')
        dropin.parent.mkdir(parents=True,exist_ok=True)
        dropin.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 /var/tmp/management-lifecycle-worker.py check\nTimeoutStartSec=4\nPrivateTmp=no\n')
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
        def task_status(expected):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                data=request('/sniffbox/subscription/maintenance')['operation']
                if data and data.get('phase')==expected:return data
                time.sleep(.1)
            raise AssertionError('maintenance phase not observed: '+expected)
        try:
            gate.touch()
            submitted=request('/sniffbox/subscription/core/check',{})
            active=task_status('validating')
            assert active['id']==submitted['id'] and active['execution']['invocation']
            assert active['execution']['service']=='paopaogw-core-action@check.service'
            subprocess.run(['systemctl','kill','--signal=SIGKILL','paopaogw-core-action@check'],check=True,timeout=10)
            dead=task_status('interrupted')
            assert dead['id']==active['id'] and dead['result']=='unknown' and dead['recovery']=='check_required'
            assert dead['status']=='failed'  # Legacy pages must never treat an interruption as success.
            passed('killed_mihomo_systemd_invocation_reports_unknown_without_replay')
            subprocess.run(['systemctl','reset-failed','paopaogw-core-action@check'],check=True,timeout=10)
            submitted=request('/sniffbox/subscription/core/check',{})
            timed=task_status('interrupted')
            assert timed['id']==submitted['id'] and timed['id']!=dead['id']
            passed('timed_out_mihomo_task_preserves_identity_and_next_request_gets_new_id')
            subprocess.run(['systemctl','reset-failed','paopaogw-core-action@check'],check=True,timeout=10)
            gate.unlink()
            complete=request('/sniffbox/subscription/core/check',{})
            done=task_status('complete')
            assert done['id']==complete['id'] and done['id']!=timed['id'] and done['result']=='succeeded'
            passed('new_mihomo_invocation_finishes_after_interrupted_predecessors')

            dropin.write_text(dropin.read_text().replace('TimeoutStartSec=4','TimeoutStartSec=60'))
            subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
            gate.touch()
            independent=request('/sniffbox/subscription/core/check',{})
            active=task_status('validating')
            subprocess.run(['systemctl','restart','paopaogw'],check=True,timeout=45)
            system.wait(load_settings(layout))
            survived=request('/sniffbox/subscription/maintenance')['operation']
            assert survived['id']==independent['id'] and survived['status']=='running'
            assert survived['execution']==active['execution']
            gate.unlink()
            done=task_status('complete')
            assert done['id']==independent['id'] and done['result']=='succeeded'
            passed('independent_mihomo_result_survives_real_core_restart')
        finally:
            gate.unlink(missing_ok=True)
            subprocess.run(['systemctl','stop','paopaogw-core-action@check'],check=False,timeout=10)
            dropin.unlink(missing_ok=True)
            subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)

        # Exercise a real restart followed by an injected readiness failure and
        # the existing transaction rollback, in this disposable fixture only.
        old_ini = config.read_bytes(); manager = ConfigManager(layout, system)
        original_apply = system.apply
        def fail_after_apply(*args):
            original_apply(*args)
            raise RuntimeError('synthetic post-apply failure')
        system.apply = fail_after_apply
        try:
            manager.apply({'ini':{'dns_ip':'1.1.1.1'}}, settings_revision(layout))
            raise AssertionError('rollback was not exercised')
        except ValueError as error:
            assert str(error)=='APPLY_FAILED_ROLLED_BACK', str(error)
        finally: system.apply = original_apply
        assert config.read_bytes()==old_ini and manager.journal()['phase']=='rolled_back'
        system.wait(load_settings(layout))
        passed('failed_readiness_restores_configuration_and_verifies_service')

        from manage_operations import Operation
        interrupted_id = 'fixture_interrupted_' + uuid.uuid4().hex
        Operation(layout, {'id':interrupted_id, 'kind':'refresh_nodes', 'status':'applying', 'stage':'verifying'})
        previous_requests = list(requests); previous_core = running()
        subprocess.run(['systemctl','restart','paopaogw-subscription'], check=True, timeout=15)
        for _ in range(50):
            try:
                interrupted = request('/sniffbox/subscription/status')['operation']
                break
            except OSError: time.sleep(.2)
        assert interrupted['id']==interrupted_id and interrupted['status']=='interrupted'
        assert running()==previous_core and requests==previous_requests
        passed('interrupted_operation_keeps_identity_and_never_replays')

        # Terminate an actual subscription worker while its synthetic provider
        # is blocked; systemd must kill the worker subprocess with its service.
        previous_core=running();previous_a=(cache/(identity(providers[0])+'.json')).read_bytes()
        before_fetches=requests.count('A');blocked.add('A');release_fetch.clear()
        current=request('/sniffbox/subscription/config');fetch_id='fixture_killed_'+uuid.uuid4().hex
        request('/sniffbox/subscription/apply',{'kind':'refresh_nodes','providers':['A'],
            'base_revision':current['base_revision'],'settings_revision':current['settings_revision'],
            'operation_id':fetch_id})
        deadline=time.monotonic()+20
        while requests.count('A')==before_fetches and time.monotonic()<deadline:time.sleep(.1)
        assert requests.count('A')>before_fetches
        subprocess.run(['systemctl','restart','paopaogw-subscription'],check=True,timeout=20)
        blocked.clear();release_fetch.set()
        for _ in range(50):
            try:
                interrupted=request('/sniffbox/subscription/status')['operation']
                break
            except OSError:time.sleep(.2)
        assert interrupted['id']==fetch_id and interrupted['status']=='interrupted'
        assert (cache/(identity(providers[0])+'.json')).read_bytes()==previous_a and running()==previous_core
        assert requests.count('A')==before_fetches+1
        passed('subscription_service_interrupted_during_fetch_keeps_cache_and_never_replays')

        # Pause a real settings transaction after its journal and saved files
        # exist, then restart only the helper and explicitly recover it.
        settings_worker=Path('/var/tmp/management-settings-worker.py')
        settings_gate=Path('/var/lib/paopaogw/admin/fixture-settings-gate')
        settings_worker.write_text('''import sys,time
from pathlib import Path
sys.path.insert(0,"/usr/local/lib/paopaogw")
import manage_web
original=manage_web.System.apply
def hold(self,*args,**kwargs):
 while Path("/var/lib/paopaogw/admin/fixture-settings-gate").exists(): time.sleep(.1)
 return original(self,*args,**kwargs)
manage_web.System.apply=hold
server=manage_web.ThreadingHTTPServer(("127.0.0.1",8790),manage_web.Handler)
server.daemon_threads=True
server.admin=manage_web.SettingsAdmin()
server.serve_forever()
''')
        settings_dropin=Path('/etc/systemd/system/paopaogw-settings.service.d/validation.conf')
        settings_dropin.parent.mkdir(parents=True,exist_ok=True)
        settings_dropin.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 /var/tmp/management-settings-worker.py\nPrivateTmp=no\n')
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
        subprocess.run(['systemctl','restart','paopaogw-settings'],check=True,timeout=20)
        try:
            for _ in range(50):
                try:current=request('/sniffbox/settings/config');break
                except OSError:time.sleep(.2)
            original_ini=config.read_bytes();settings_gate.touch();settings_id=uuid.uuid4().hex
            request('/sniffbox/settings/apply',{'id':settings_id,'kind':'config','payload':{
                'base_revision':current['revision'],'patch':{'ini':{'splice_threshold':21}}}})
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                job=request('/sniffbox/settings/jobs/'+settings_id)
                if job.get('stage')=='applying':break
                time.sleep(.1)
            assert job['stage']=='applying'
            subprocess.run(['systemctl','restart','paopaogw-settings'],check=True,timeout=20)
            settings_gate.unlink()
            for _ in range(50):
                try:job=request('/sniffbox/settings/jobs/'+settings_id);break
                except OSError:time.sleep(.2)
            assert job['stage']=='interrupted' and job['operation_result']=='unknown'
            assert request('/sniffbox/settings/status')['recovery_required']
            settings_dropin.unlink()
            subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
            subprocess.run(['systemctl','restart','paopaogw-settings'],check=True,timeout=20)
            ConfigManager(layout,system).recover()
            assert config.read_bytes()==original_ini
            for _ in range(50):
                try:
                    status=request('/sniffbox/settings/status')
                    if not status['recovery_required']:break
                except OSError:pass
                time.sleep(.2)
            assert not status['recovery_required']
            assert request('/sniffbox/settings/jobs/'+settings_id)['operation_result']=='unknown'
            passed('settings_service_interrupted_after_save_requires_explicit_recovery_without_replay')
        finally:
            settings_gate.unlink(missing_ok=True);settings_dropin.unlink(missing_ok=True)
            subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
            subprocess.run(['systemctl','restart','paopaogw-settings'],check=True,timeout=20)

        public = ''.join(p.read_text() for p in Path('/var/lib/paopaogw/admin/operations').glob('*.json'))
        assert all(password not in public for password in passwords.values())
        assert 'http://127.0.0.1:' not in public
        passed('operation_history_excludes_subscription_urls_and_node_passwords')
        report.update(complete=True, final=running())
        args.report.write_text(json.dumps(report, indent=2)+'\n')
    finally:
        blocked.clear();release_fetch.set()
        server.shutdown(); server.server_close()


if __name__ == '__main__': main()
