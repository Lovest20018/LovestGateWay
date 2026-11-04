#!/usr/bin/env python3
"""Validate installation and boot behavior only in a marked disposable systemd container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

PRIMARY = ('paopaogw', 'paopaogw-subscription', 'paopaogw-settings')
OPTIONAL = ('paopaogw-api', 'paopaogw-observe', 'paopaogw-maintenance.timer')


def run(*args, check=True, timeout=150):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode: raise AssertionError(result.stderr or result.stdout)
    return result


def states():
    return {unit: {'active': run('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0,
                   'enabled': run('systemctl', 'is-enabled', unit, check=False).stdout.strip() == 'enabled'}
            for unit in (*PRIMARY, *OPTIONAL)}


def assert_states(active, enabled):
    value = states()
    for unit in PRIMARY: assert value[unit] == {'active': active, 'enabled': enabled}, (unit, value[unit])
    for unit in OPTIONAL: assert value[unit] == {'active': False, 'enabled': False}, (unit, value[unit])


def config_digest():
    root = Path('/etc/paopaogw')
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--phase', choices=('fresh', 'manual-boot', 'auto-boot'), required=True)
    parser.add_argument('--report', type=Path, default=Path('/var/tmp/installer-validation.json'))
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path('/var/tmp/installer-validation-only').is_file() or not Path('/run/systemd/container').exists():
        parser.error('requires a marked disposable systemd container')
    report = json.loads(args.report.read_text()) if args.report.exists() else {'kind': 'isolated-debian-installer-validation', 'passed': []}
    report.update(phase=args.phase, complete=False, status='running')
    def save(): args.report.write_text(json.dumps(report, indent=2) + '\n')
    def passed(name):
        report['passed'].append(name); save(); print('PASS ' + name, flush=True)
    def install(*values): return run('sh', str(args.package / 'install.sh'), *values)
    save()
    try:
        if args.phase == 'fresh':
            assert not Path('/etc/paopaogw').exists(), 'requires an empty installation root'
            help_text = run('sh', str(args.package / 'install.sh'), '--help').stdout
            assert '开机自启' in help_text and '用法:' in help_text
            with socket.socket() as occupied:
                occupied.bind(('0.0.0.0', 18081)); occupied.listen()
                result = run('sh', str(args.package / 'install.sh'), '--interface', 'eth0', '--web-listen', '0.0.0.0:18081',
                             '--dns-listen', '0.0.0.0:1053', '--startup', 'auto', '--install-deps', '--yes', check=False)
                assert result.returncode != 0 and 'LISTENER_UNAVAILABLE' in result.stderr and '监听地址' in result.stderr, result.stderr
                assert not Path('/etc/paopaogw').exists()
            passed('dependency_installation_chinese_help_and_occupied_port_rejected_before_configuration_write')
            result = install('--interface', 'eth0', '--web-listen', '0.0.0.0:18081', '--dns-listen', '0.0.0.0:1053', '--startup', 'disabled', '--yes')
            assert '全新安装完成' in result.stdout and '网关尚未启动' in result.stdout
            assert_states(False, False)
            sys.path.insert(0, '/usr/local/lib/paopaogw')
            from manage_common import Layout
            from manage_model import load_settings
            settings = load_settings(Layout())
            for key in ('openport', 'udp_enable', 'net_rec'): assert not settings['ini'][key]
            assert not settings['service']['PPGW_IPV6']
            assert not settings['maintenance']['history_enabled'] and not settings['maintenance']['auto_update']
            assert not settings['api']['enabled'] and settings['api']['listen'] == '127.0.0.1'
            assert len(settings['ini']['clash_web_password']) == 8
            assert settings['ini']['clash_web_password'] not in result.stdout + result.stderr
            passed('fresh_disabled_installs_minimal_defaults_without_starting_or_enabling_services')
            report['configuration'] = config_digest()
            result = install('--startup', 'manual', '--yes')
            assert '升级完成' in result.stdout and 'http://' in result.stdout
            assert_states(True, False)
            assert config_digest() == report['configuration']
            passed('manual_starts_three_primary_services_without_boot_links_optional_api_off')
        elif args.phase == 'manual-boot':
            assert_states(False, False)
            passed('manual_services_stay_stopped_after_systemd_container_restart')
            install('--startup', 'auto', '--yes')
            assert_states(True, True)
            assert config_digest() == report['configuration']
            passed('auto_starts_services_and_creates_boot_links_without_changing_configuration')
        else:
            sys.path.insert(0, '/usr/local/lib/paopaogw')
            from manage_common import Layout
            from manage_model import load_settings
            from manage_runtime import System
            system = System(Layout()); system.wait(load_settings(Layout())); system.wait_api(load_settings(Layout()))
            assert_states(True, True)
            passed('auto_primary_services_start_and_pass_core_readiness_optional_api_off')
            run('systemctl', 'stop', 'paopaogw-settings')
            before = states()
            result = install('--yes')
            assert '保留原有运行及开机自启状态' in result.stdout
            assert states() == before and config_digest() == report['configuration']
            passed('default_upgrade_preserves_mixed_running_and_autostart_states_and_credentials')
            run('systemctl', 'start', 'paopaogw-settings')
            report['complete'] = True
        report.update(status='passed', services=states()); save()
    except BaseException as error:
        report.update(status='failed', error=str(error)); save(); raise


if __name__ == '__main__': main()
