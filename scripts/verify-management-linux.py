#!/usr/bin/env python3
"""Destructive management acceptance ONLY inside a disposable, marked Linux fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=Path('/var/tmp/management-validation.json'))
    args = parser.parse_args()
    if not Path('/run/management-validation-only').is_file() or os.geteuid() != 0:
        parser.error('requires root and disposable fixture marker /run/management-validation-only')
    sys.path.insert(0, '/usr/local/lib/paopaogw')
    from manage_common import Layout, ManagementError, operation_lock
    from manage_model import load_settings, settings_revision, render_updates
    from manage_config import ConfigManager
    from manage_runtime import System
    from manage_deploy import Deployer
    from manage import create_backup, restore_backup
    from manage_assets import import_asset
    layout = Layout(); system = System(layout, timeout=8); manager = ConfigManager(layout, system)
    results = []
    def record(name, function):
        function(); results.append(name); print('PASS ' + name, flush=True)
        args.report.write_text(json.dumps({'passed': results, 'complete': False}, indent=2) + '\n')
    def apply(patch, **kwargs): return manager.apply(patch, settings_revision(layout), **kwargs)
    ini = Path('/etc/paopaogw/files/ppgw.ini')
    def initial():
        system.wait(load_settings(layout)); system.wait_api(load_settings(layout))
        result = subprocess.run(['runuser', '-u', 'paopaogw-api', '--', 'python3', '-c',
             "import sys;sys.path.insert(0,'/usr/local/lib/paopaogw');from gateway_api import CoreClient;from pathlib import Path;CoreClient(Path('/var/lib/paopaogw/core-token')).request('/sniffbox/info')"], capture_output=True)
        assert result.returncode == 0, 'observer must read service.env and core token'
    record('installed_core_api_observer_credentials', initial)
    def hot():
        old = system.runtime(); count = load_settings(layout)['ini']['max_rec'] + 1
        result = apply({'ini': {'max_rec': count}}); current = system.runtime()
        assert result['action'] == 'reload' and old['boot_id'] == current['boot_id'] and old['config_revision'] != current['config_revision']
        system.verify_loaded(load_settings(layout))
    record('real_hot_reload_snapshot_revision', hot)
    def no_op():
        raw = ini.read_bytes(); boot = system.runtime()['boot_id']
        assert apply({'ini': {'max_rec': load_settings(layout)['ini']['max_rec']}})['status'] == 'unchanged'
        assert raw == ini.read_bytes() and boot == system.runtime()['boot_id']
        try: manager.apply({'ini': {'max_rec': 13}}, 'stale')
        except ManagementError: pass
        else: raise AssertionError('stale revision accepted')
        assert raw == ini.read_bytes()
    record('noop_and_stale_revision', no_op)
    def listeners():
        apply({'service': {'PPGW_WEB_LISTEN': '0.0.0.0:8088'}})
        apply({'api': {'port': 8798, 'name': '管理验证'}})
        system.wait(load_settings(layout)); system.wait_api(load_settings(layout))
    record('changed_core_and_api_listener', listeners)
    def toggles():
        apply({'maintenance': {'history_enabled': True}})
        apply({'maintenance': {'history_enabled': False, 'history_retention_days': 7, 'auto_update': False}})
        states = system.status()
        assert not states['paopaogw-observe']['active'] and not states['paopaogw-observe']['enabled']
        assert not states['paopaogw-maintenance.timer']['active'] and not states['paopaogw-maintenance.timer']['enabled']
    record('feature_off_and_retention_combination', toggles)
    def invalid_and_occupied_api():
        revision = settings_revision(layout)
        for patch in ({'api': {'name': 'x'*101}}, {'ini': {'dns_port': True}}, {'ini': {'openport_auth': 'invalid'}}, {'ini': {'box': 'dns.shards=4'}}):
            try: manager.apply(patch, revision)
            except ManagementError: pass
            else: raise AssertionError('invalid settings accepted')
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 8799)); occupied.listen()
            try: apply({'api': {'port': 8799}})
            except ManagementError: pass
            else: raise AssertionError('unready API accepted')
        assert load_settings(layout)['api']['port'] == 8798
        assert manager.journal()['phase'] == 'rolled_back'
        system.wait_api(load_settings(layout))
    record('invalid_and_unready_api_rollback', invalid_and_occupied_api)
    def cross_process_and_web_lock():
        import subscription_admin
        admin = subscription_admin.Admin(); admin.operation = {'status': 'applying'}
        before = ini.read_bytes()
        with operation_lock(layout):
            child = subprocess.run(['python3', '-c', "import sys;sys.path.insert(0,'/usr/local/lib/paopaogw');from manage_common import operation_lock;\nwith operation_lock(): pass"], capture_output=True)
            assert child.returncode != 0 and b'MANAGEMENT_BUSY' in child.stderr
            worker = threading.Thread(target=admin.worker, args=(before, before+b'# not written\n', None))
            worker.start(); worker.join(5)
            assert not worker.is_alive() and admin.operation['status'] == 'failed'
        assert before == ini.read_bytes()
    record('cross_process_and_web_exclusion', cross_process_and_web_lock)
    def assets():
        source = Path('/var/tmp/unsafe.ovpn')
        for directive in ('"up" "/bin/sh"', '"config" "/etc/secret"', 'script-security 2'):
            source.write_text('client\nremote 192.0.2.1 1194\n' + directive + '\n')
            try: import_asset(layout, source, 'ovpn', 'verify.ovpn')
            except ManagementError: pass
            else: raise AssertionError('unsafe VPN accepted')
        source.unlink()
        symlink = Path('/etc/paopaogw/files/verify-link.yaml'); symlink.symlink_to('/etc/passwd')
        try:
            try: settings_revision(layout)
            except ManagementError: pass
            else: raise AssertionError('symlink accepted')
        finally: symlink.unlink()
    record('unsafe_vpn_and_symlinks_rejected', assets)
    archive_sha = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    def upgrade():
        identity = {name: Path('/etc/paopaogw/' + name).read_bytes() for name in ('instance-id', 'read-token', 'revision-key', 'files/ppgw.ini', 'service.env', 'api.json', 'maintenance.json')}
        states = system.status()
        Deployer(layout, system).deploy(args.archive, archive_sha)
        assert all(Path('/etc/paopaogw/' + name).read_bytes() == raw for name,raw in identity.items())
        assert system.status() == states
    record('actual_upgrade_preserves_identity_settings_services', upgrade)
    def failed_upgrade():
        original = Path('/usr/local/lib/paopaogw/build.json').read_bytes(); states = system.status()
        class FailOnce(System):
            def wait(self, *args, **kwargs): raise ManagementError('INJECTED_READINESS_FAILURE')
        try: Deployer(layout, FailOnce(layout, timeout=1)).deploy(args.archive, archive_sha)
        except ManagementError: pass
        else: raise AssertionError('injected failure accepted')
        assert manager.journal()['phase'] == 'rolled_back'
        assert Path('/usr/local/lib/paopaogw/build.json').read_bytes() == original and system.status() == states
        system.wait(load_settings(layout)); system.wait_api(load_settings(layout))
    record('real_services_failed_upgrade_rollback', failed_upgrade)
    def killed_transaction():
        original = ini.read_bytes()
        script = """import sys,os,signal
sys.path.insert(0,'/usr/local/lib/paopaogw')
from manage_common import Layout
from manage_model import settings_revision,load_settings
from manage_runtime import System
from manage_config import ConfigManager
class Killed(System):
 def apply(self,*args): os.kill(os.getpid(),signal.SIGKILL)
layout=Layout(); value=load_settings(layout)['ini']['max_rec']+3
ConfigManager(layout,Killed(layout)).apply({'ini':{'max_rec':value}},settings_revision(layout))
"""
        child = subprocess.run(['python3', '-c', script], capture_output=True)
        assert child.returncode == -signal.SIGKILL and ini.read_bytes() != original
        assert manager.journal()['phase'] == 'applying'
        manager.recover()
        assert ini.read_bytes() == original and manager.journal()['phase'] == 'rolled_back'
        system.wait(load_settings(layout))
    record('sigkill_and_recover_persistent_config', killed_transaction)
    def external_conflict():
        original = ini.read_bytes(); external = original+b'# external edit\n'
        class External(System):
            def apply(self,*args): ini.write_bytes(external); raise ManagementError('INJECTED')
        try: ConfigManager(layout,External(layout)).apply({'ini':{'max_rec':load_settings(layout)['ini']['max_rec']+9}},settings_revision(layout))
        except ManagementError: pass
        else: raise AssertionError('injected conflict accepted')
        assert ini.read_bytes() == external and manager.journal()['phase'] == 'conflict'
        ini.write_bytes(original)  # explicit fixture operator resolves its own external edit
        manager.recover(); system.wait(load_settings(layout))
    record('external_conflict_preserved_then_resolved', external_conflict)
    def backup_restore():
        identifier = create_backup(layout, system)['backup']; count = load_settings(layout)['ini']['max_rec']
        apply({'ini': {'max_rec': count+2}})
        restore_backup(layout, system, identifier)
        assert load_settings(layout)['ini']['max_rec'] == count
        system.wait(load_settings(layout)); system.wait_api(load_settings(layout))
    record('real_backup_and_restore', backup_restore)
    args.report.write_text(json.dumps({'complete': True, 'passed': results, 'archive_sha256': archive_sha,
        'core_build_commit': system.runtime()['build_commit'], 'host_network_modified': False,
        'fixture': 'paopaogw-management-validation'}, indent=2) + '\n')
    print('COMPLETE ' + str(len(results)) + ' acceptance scenarios', flush=True)


if __name__ == '__main__': main()
