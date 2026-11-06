"""Exercise the product reload path with real Mihomo in an isolated Linux network.

Use --binary and a nonexistent --work-dir. No host networking, production mounts
or published ports are needed. Credentials, listeners and traffic are synthetic.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'integration'))
from mihomo_reload import UnixController, plan, policy_candidate, reload_verified
from manage_common import ManagementError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--work-dir', required=True, type=Path)
    args = parser.parse_args(); binary = args.binary.resolve(); work = args.work_dir.resolve()
    work.mkdir(exist_ok=False)
    fixture = runpy.run_path(str(ROOT/'scripts/evaluate-mihomo-provider-reload.py'))
    receive = fixture['receive']
    servers = []
    for handler in (fixture['Echo'], fixture['Socks']):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler); server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start(); servers.append(server)
    echo, upstream = servers
    config = {'socks-port': 19080, 'allow-lan': False, 'bind-address': '127.0.0.1',
              'mode': 'rule', 'log-level': 'warning', 'external-controller-unix': str(work/'controller.sock'),
              'dns': {'enable': False}, 'hosts': {'probe.test': '127.0.0.1'},
              'profile': {'store-selected': True}, 'proxies': [
                  {'name': 'Fixture', 'type': 'socks5', 'server': '127.0.0.1',
                   'port': upstream.server_address[1], 'username': 'fixture', 'password': 'one'}],
              'proxy-groups': [{'name': 'MAIN', 'type': 'select', 'proxies': ['Fixture', 'DIRECT']}],
              'rules': ['MATCH,MAIN']}
    for kind in ('url-test', 'fallback', 'load-balance'):
        config['proxy-groups'].append({'name':kind, 'type':kind, 'proxies':['DIRECT','Fixture'],
                                       'url':'http://127.0.0.1:1/health', 'interval':3600, 'lazy':True})
    loaded = work/'loaded.json'; loaded.write_text(json.dumps(config))
    log = (work/'mihomo.log').open('w'); process = None; keep = None
    report = {'synthetic_only': True, 'production_touched': False, 'checks': [],
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'version': subprocess.check_output([str(binary), '-v'], text=True).strip()}
    def save(): (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    def passed(name): report['checks'].append(name); save(); print('PASS '+name, flush=True)
    controller = UnixController(work/'controller.sock')
    def api(method, path, body=None): return controller.request(method, path, body)[0]
    def start():
        nonlocal process
        process = subprocess.Popen([str(binary), '-d', str(work/'state'), '-f', str(loaded)], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic()+20
        while True:
            try:
                assert 'MAIN' in api('GET', '/proxies')['proxies']
                break
            except Exception:
                if process.poll() is not None or time.monotonic()>deadline: raise
                time.sleep(.1)
    def stop():
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    def validate(document):
        candidate = work/'candidate.json'; candidate.write_text(json.dumps(document))
        result = subprocess.run([str(binary), '-t', '-d', str(work/'state'), '-f', str(candidate)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if result.returncode: raise ManagementError('INVALID_POLICY')
    def persist(document): loaded.write_text(json.dumps(document))
    def connect(host='127.0.0.1'):
        sock = socket.create_connection(('127.0.0.1', 19080), timeout=4); sock.settimeout(4)
        sock.sendall(b'\x05\x01\x00'); assert receive(sock, 2)==b'\x05\x00'
        name = host.encode(); sock.sendall(b'\x05\x01\x00\x03'+bytes([len(name)])+name+struct.pack('!H', echo.server_address[1]))
        reply = receive(sock, 4)
        if reply[1]: sock.close(); return None
        size = {1: 4, 4: 16}.get(reply[3]); size = receive(sock, 1)[0] if reply[3]==3 else size
        receive(sock, size+2); return sock
    def echoed(sock, value): sock.sendall(value); assert receive(sock, len(value))==value
    def rejected():
        sock = connect('probe.test')
        if sock is None: return True
        try: sock.sendall(b'blocked'); return sock.recv(64)==b''
        except (ConnectionResetError, BrokenPipeError): return True
        finally: sock.close()
    try:
        start(); original_pid = process.pid
        api('PUT', '/proxies/MAIN', {'name': 'Fixture'}); api('PUT', '/proxies/GLOBAL', {'name': 'MAIN'})
        keep = connect(); assert keep; echoed(keep, b'before')
        port = keep.getsockname()[1]
        connection_id = next(row['id'] for row in api('GET', '/connections')['connections']
                             if str(row['metadata']['sourcePort'])==str(port))
        passed('authenticated_static_proxy_and_long_lived_tcp_established')
        api('PUT', '/proxies/url-test', {'name': 'Fixture'})
        assert api('GET', '/proxies/url-test')['fixed']=='Fixture'
        before = deepcopy(config)
        for mode in ('rule', 'global', 'direct'):
            api('PATCH', '/configs', {'mode': mode})
            after = deepcopy(before); after['rules'].insert(0, 'DOMAIN,probe.test,REJECT')
            if mode=='rule': after['rules'][1:1] = [
                'DOMAIN-SUFFIX,example.invalid,DIRECT', 'DOMAIN-KEYWORD,unused,DIRECT',
                'IP-CIDR,192.0.2.0/24,DIRECT,no-resolve', 'IP-CIDR6,2001:db8::/32,DIRECT,no-resolve',
                'SRC-IP-CIDR,192.0.2.0/24,DIRECT', 'SRC-PORT,1,DIRECT', 'DST-PORT,1,DIRECT', 'NETWORK,UDP,DIRECT']
            if mode=='global':
                # Edit membership and add/remove a group as well as rules.
                after['proxy-groups'][0]['proxies'].reverse()
                after['proxy-groups'].append({'name': 'Added', 'type': 'select', 'proxies': ['DIRECT', 'Fixture']})
            if mode=='direct': after['proxy-groups'] = after['proxy-groups'][:1]
            evidence = reload_verified(before, after, controller, validate, persist)
            assert evidence['method']=='mihomo_reload' and process.pid==original_pid and process.poll() is None
            echoed(keep, ('after-'+mode).encode())
            assert any(row['id']==connection_id for row in api('GET', '/connections')['connections'])
            assert api('GET', '/configs')['mode']==mode
            assert api('GET', '/proxies')['proxies']['MAIN']['now']=='Fixture'
            assert api('GET', '/proxies')['proxies']['GLOBAL']['now']=='MAIN'
            if any(row['name']=='url-test' for row in after['proxy-groups']):
                assert api('GET', '/proxies/url-test')['fixed']=='Fixture'
            after['mode']=mode; before=after
            fresh=connect(); assert fresh; echoed(fresh, b'fresh-'+mode.encode()); fresh.close()
            passed(mode+'_reload_preserves_process_tcp_identity_mode_choices_and_new_connections')
        api('PATCH', '/configs', {'mode': 'rule'}); assert rejected()
        assert fixture['auth_attempts'] and set(fixture['auth_attempts'])=={'one'}
        passed('new_matching_connection_rejected_and_static_credentials_used')
        # Explicit DIRECT survives too, including the native GLOBAL selector.
        api('PUT', '/proxies/MAIN', {'name': 'DIRECT'}); api('PUT', '/proxies/GLOBAL', {'name': 'DIRECT'})
        after = deepcopy(before); after['rules'].insert(0, 'DOMAIN,other.test,DIRECT')
        reload_verified(before, after, controller, validate, persist); before=after
        assert api('GET', '/proxies')['proxies']['MAIN']['now']=='DIRECT'
        assert api('GET', '/proxies')['proxies']['GLOBAL']['now']=='DIRECT'
        passed('explicit_direct_selections_preserved')
        # Failed native validation must happen before any mutating controller call.
        after=deepcopy(before); after['proxy-groups'][0]['proxies'].append('missing-fixture-node')
        class Recording:
            def __init__(self): self.puts=0
            def request(self, method, path, body=None):
                if method=='PUT': self.puts+=1
                return controller.request(method, path, body)
        recording=Recording(); disk=loaded.read_bytes()
        try: reload_verified(before, after, recording, validate, persist); raise AssertionError('invalid candidate accepted')
        except ManagementError as error: assert str(error)=='INVALID_POLICY'
        assert recording.puts==0 and loaded.read_bytes()==disk; echoed(keep, b'after-invalid')
        passed('invalid_candidate_rejected_before_put_without_interrupting_tcp')
        for key, value in [('proxies', []), ('dns', {'enable': True}), ('listeners', [{}]),
                           ('proxy-providers', {'unsafe': {}}), ('rule-providers', {'unsafe': {}})]:
            after=deepcopy(before); after[key]=value
            assert not plan(before, after)['eligible']
            try: reload_verified(before, after, recording, validate, persist); raise AssertionError('unsafe reload attempted')
            except ManagementError as error: assert str(error)=='MIHOMO_RELOAD_UNSUPPORTED'
        assert recording.puts==0; echoed(keep, b'after-unsupported')
        passed('providers_credentials_dns_and_listeners_never_use_reload')
        # Simulate losing the acknowledgement after the real PUT; the shared
        # transaction must restore disk and restart the accepted configuration.
        from manage_common import Layout
        from manage_config import ConfigManager
        from manage_model import settings_revision
        layout=Layout(work/'transaction'); ini=layout.path('etc/paopaogw/files/ppgw.ini')
        ini.parent.mkdir(parents=True); ini.write_text('#paopao-gateway\nmode=yaml\nclash_web_password=fixture-password-long\n')
        policy=ini.parent/'clash-policy.json'; policy.write_bytes(b'{"accepted":true}')
        after=deepcopy(before); after['rules'].insert(0, 'DOMAIN,uncertain.test,REJECT')
        class Uncertain:
            def request(self, method, path, body=None):
                result=controller.request(method, path, body)
                if method=='PUT' and path.startswith('/configs?'): raise OSError('synthetic lost acknowledgement')
                return result
        class FixtureSystem:
            def preflight(self, settings): pass
            def status(self): return {'paopaogw': {'active': True, 'enabled': True}}
            def apply(self, action, settings, changes, services):
                assert action=='mihomo_reload'
                return reload_verified(before, after, Uncertain(), validate, persist)
            def restore_services(self, services): stop(); loaded.write_bytes(disk); start()
        old_pid=process.pid
        try:
            ConfigManager(layout, FixtureSystem()).apply({}, settings_revision(layout), policy_update=b'{"candidate":true}',
                mihomo_update={'before':before,'after':after})
            raise AssertionError('uncertain reload reported success')
        except ManagementError as error: assert str(error)=='APPLY_FAILED_ROLLED_BACK'
        assert process.pid!=old_pid and policy.read_bytes()==b'{"accepted":true}' and loaded.read_bytes()==disk
        assert ConfigManager(layout, FixtureSystem()).journal()['phase']=='rolled_back'
        assert not any(row['payload']=='uncertain.test' for row in api('GET', '/rules')['rules'])
        fresh=connect(); assert fresh; echoed(fresh, b'after-rollback'); fresh.close()
        passed('lost_acknowledgement_rolls_back_persistent_policy_and_restarts_accepted_runtime')
        from clash_ppsub import apply_features, feature_options
        keep.close(); keep = None
        for label, options in [
            ('monitor', {'global_monitor': {'enable': True}}),
            ('cron', {'restart_cron': {'enable': True, 'hour': 4}}),
            ('ss_listener', {'shadowsocks_in': {'enable': True, 'cipher': 'aes-128-gcm', 'password': 'fixture-listener-password'}}),
            ('smart_static', {'node-groups': [{'name': 'MAIN', 'smart': True}]}),
        ]:
            stop()
            logical = deepcopy(config)
            if label == 'smart_static': logical['proxy-groups'][0]['proxies'].append('smartspeedtest@hide')
            features = feature_options(options)
            physical = apply_features(logical, features)
            persist(physical); start(); managed_pid = process.pid
            api('PUT', '/proxies/MAIN', {'name': 'Fixture'})
            api('PUT', '/proxies/GLOBAL', {'name': 'MAIN'})
            keep = connect(); assert keep; echoed(keep, b'managed-before')
            edited = deepcopy(logical); edited['rules'].insert(0, 'DOMAIN,probe.test,REJECT')
            decision, update = policy_candidate(logical, apply_features(edited, features), features, features, physical)
            assert decision['eligible'] and update['before'] == physical
            assert not policy_candidate(logical, update['after'], features, {}, physical)[0]['eligible']
            evidence = reload_verified(update['before'], update['after'], controller, validate, persist)
            assert evidence['method'] == 'mihomo_reload' and process.pid == managed_pid
            echoed(keep, b'managed-after'); assert rejected()
            assert api('GET', '/proxies')['proxies']['MAIN']['now'] == 'Fixture'
            assert api('GET', '/proxies')['proxies']['GLOBAL']['now'] == 'MAIN'
            assert json.loads(loaded.read_text()).get('x-ppgw-features') == physical.get('x-ppgw-features')
            keep.close(); keep = None
            passed('managed_'+label+'_static_rules_preserve_pid_tcp_selections_and_reject_matching_connection')
        report.update(complete=True, connection_identity_preserved_on_success=True,
                      uncertain_reload_requires_restart=True, providers_use_restart=True)
        save()
    except Exception as error:
        report.update(complete=False, error_type=type(error).__name__, error=str(error)); save(); raise
    finally:
        if keep: keep.close()
        stop(); log.close()
        for server in servers: server.shutdown(); server.server_close()


if __name__=='__main__': main()
