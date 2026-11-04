from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from manage_common import Layout, atomic_write, operation_lock, ManagementError
from manage_deploy import Deployer, verify_package
from manage_common import error_text
from manage_model import load_settings
from types import SimpleNamespace


class InstallWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.base = Path(cls.fixture.name)
        binary = cls.base / 'core'
        binary.write_bytes(b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00' + bytes(12))
        subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'scripts/package-debian.py'),
                        '--binary', str(binary), '--version', 'wizard-test', '--commit', 'a'*40,
                        '--source-sha256', 'b'*64, '--arch', 'amd64', '--output', str(cls.base)],
                       capture_output=True, check=True)
        archive = cls.base / 'lovestgateway-wizard-test-debian-amd64.tar.gz'
        import hashlib
        with verify_package(archive, hashlib.sha256(archive.read_bytes()).hexdigest()) as package:
            cls.package = cls.base / 'package'
            shutil.copytree(package.path, cls.package)

    @classmethod
    def tearDownClass(cls): cls.fixture.cleanup()

    def setUp(self):
        # The installed CLI uses -B; in-process imports must also leave the
        # verified package unchanged when the test runner enables bytecode.
        bytecode = patch.object(sys, 'dont_write_bytecode', True)
        bytecode.start(); self.addCleanup(bytecode.stop)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'

    def run_install(self, *args):
        return subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.package / 'deploy.py'),
                               '--root', str(self.root), *args], input='', capture_output=True, encoding='utf-8')

    def test_fresh_headless_install_custom_listeners_and_minimal_defaults(self):
        result = self.run_install('--interface', 'ens18', '--web-listen', '0.0.0.0:18080',
                                  '--dns-listen', '192.0.2.1:53', '--startup', 'disabled', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('只安装', result.stdout); self.assertIn('全新安装完成', result.stdout)
        settings = load_settings(Layout(self.root))
        self.assertEqual(settings['service']['PPGW_INTERFACE'], 'ens18')
        self.assertEqual(settings['service']['PPGW_WEB_LISTEN'], '0.0.0.0:18080')
        self.assertEqual(settings['service']['PPGW_DNS_LISTEN'], '192.0.2.1:53')
        self.assertEqual(settings['ini']['mode'], 'free')
        for key in ('openport', 'net_rec', 'udp_enable'): self.assertFalse(settings['ini'][key])
        self.assertFalse(settings['maintenance']['history_enabled']); self.assertFalse(settings['maintenance']['auto_update'])
        self.assertFalse(settings['service']['PPGW_IPV6'])
        self.assertEqual(len(settings['ini']['clash_web_password']), 8)
        self.assertNotIn(settings['ini']['clash_web_password'], result.stdout + result.stderr)

    def test_missing_confirmation_interface_and_invalid_startup_never_install(self):
        for args, expected in [(('--interface', 'ens18'), '显式提供 --yes'),
                               (('--yes',), '全新安装必须指定'),
                               (('--interface', 'ens18', '--startup', 'preserve', '--yes'), '全新安装不能沿用')]:
            result = self.run_install(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
            self.assertFalse((self.root / 'etc/paopaogw').exists())

    def test_upgrade_preserves_credentials_and_rejects_initial_options(self):
        first = self.run_install('--interface', 'ens18', '--startup', 'disabled', '--yes')
        self.assertEqual(first.returncode, 0, first.stderr)
        config = self.root / 'etc/paopaogw'
        before = {name: (config / name).read_bytes() for name in ('instance-id', 'read-token', 'files/ppgw.ini', 'service.env')}
        failed = self.run_install('--web-listen', '0.0.0.0:9999', '--yes')
        self.assertNotEqual(failed.returncode, 0); self.assertIn('升级保留', failed.stderr)
        result = self.run_install('--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('保留原有运行及开机自启状态', result.stdout)
        for name, raw in before.items(): self.assertEqual((config / name).read_bytes(), raw)

    def test_fresh_interactive_network_list_plan_and_chinese_confirmation(self):
        spec = importlib.util.spec_from_file_location('wizard_under_test', self.package / 'deploy.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        output = io.StringIO()
        answers = iter(['2', '127.0.0.1:18080', '127.0.0.1:1053', '3', '是'])
        with patch.object(module, 'interfaces', return_value=[('ens18', '192.0.2.1'), ('ens19', '192.0.2.2')]), \
             patch('sys.stdin.isatty', return_value=True), patch.object(module.getpass, 'getpass', return_value=''), patch('builtins.input', side_effect=lambda _: next(answers)), redirect_stdout(output):
            self.assertEqual(module.main(['--root', str(self.root)]), 0)
        self.assertIn('本机网卡', output.getvalue()); self.assertIn('安装计划', output.getvalue())
        self.assertIn('暂不启动', output.getvalue())
        settings = load_settings(Layout(self.root))
        self.assertEqual(settings['service']['PPGW_INTERFACE'], 'ens19')
        self.assertEqual(settings['service']['PPGW_DNS_LISTEN'], '127.0.0.1:1053')

    def test_interactive_password_retries_confirms_and_does_not_print_secret(self):
        spec = importlib.util.spec_from_file_location('password_wizard_under_test', self.package / 'deploy.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        output = io.StringIO(); password = 'Fx8_pass'
        answers = ['seven77', password, 'different-private-password', password, password]
        with patch('sys.stdin.isatty', return_value=True), patch.object(module.getpass, 'getpass', side_effect=answers), \
             patch('builtins.input', return_value='是'), redirect_stdout(output):
            result = module.main(['--root', str(self.root), '--interface', 'ens18', '--web-listen', '127.0.0.1:18080',
                                  '--dns-listen', '127.0.0.1:1053', '--startup', 'disabled'])
        self.assertEqual(result, 0)
        self.assertEqual(load_settings(Layout(self.root))['ini']['clash_web_password'], password)
        self.assertIn('密码需至少 8 个字符', output.getvalue()); self.assertIn('两次输入的密码不一致', output.getvalue())
        self.assertNotIn(password, output.getvalue())
        with patch.object(module.getpass, 'getpass') as prompt, redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(['--root', str(self.root), '--yes']), 0)
        prompt.assert_not_called()
        self.assertEqual(load_settings(Layout(self.root))['ini']['clash_web_password'], password)

    def test_deployer_rejects_short_initial_password_before_writing(self):
        import hashlib
        archive = self.base / 'lovestgateway-wizard-test-debian-amd64.tar.gz'
        with self.assertRaisesRegex(ManagementError, 'DASHBOARD_PASSWORD_TOO_SHORT'):
            Deployer(Layout(self.root)).deploy(archive, hashlib.sha256(archive.read_bytes()).hexdigest(),
                                              interface='ens18', startup='disabled', initial_password='seven77')
        self.assertFalse((self.root / 'etc/paopaogw').exists())

    def test_chinese_help_argument_errors_and_error_codes(self):
        help_result = self.run_install('--help')
        self.assertIn('用法:', help_result.stdout); self.assertIn('开机自启', help_result.stdout)
        invalid = self.run_install('--startup', 'invalid', '--yes')
        self.assertIn('无效选项', invalid.stderr)
        self.assertNotIn('invalid choice', invalid.stderr)
        self.assertIn('运行依赖', error_text(ManagementError('DEPENDENCY_REQUIRED:private_value')))
        self.assertNotIn('private_value', error_text(ManagementError('DEPENDENCY_REQUIRED:private_value')))


class StartupPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.original = {'paopaogw': {'active': False, 'enabled': True},
                         'paopaogw-api': {'active': True, 'enabled': False}}
        self.states = []; self.waits = []
        self.system = SimpleNamespace(status=lambda: self.original, stop_all=lambda: None,
                        restore_services=lambda value: self.states.append(value),
                        wait=lambda settings: self.waits.append('core'), wait_api=lambda settings: self.waits.append('api'))
        self.candidates = {'etc/paopaogw/maintenance.json': b'{"history_enabled":false,"auto_update":false}',
                           'etc/systemd/system/paopaogw-settings.service': b'fixture'}
        def install(_):
            for path, raw in self.candidates.items(): atomic_write(self.layout, path, raw)
        self.deployer = Deployer(self.layout, self.system, install)
        self.package = SimpleNamespace(build={'version': 'fixture'})

    def apply(self, startup=None, fresh=True):
        with operation_lock(self.layout): return self.deployer._apply(self.package, self.candidates, fresh, startup)

    def test_fresh_all_startup_modes_and_optional_features_stay_off(self):
        for mode, active, enabled in [('auto', True, True), ('manual', True, False), ('disabled', False, False)]:
            with self.subTest(mode=mode):
                self.states.clear(); self.waits.clear()
                result = self.apply(mode)
                for unit in ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-settings'):
                    self.assertEqual(result['services'][unit], {'active': active, 'enabled': enabled})
                for unit in ('paopaogw-observe', 'paopaogw-maintenance.timer'):
                    self.assertEqual(result['services'][unit], {'active': False, 'enabled': False})
                self.assertEqual(self.waits, ['core', 'api'] if active else [])
                self.assertEqual(self.original['paopaogw'], {'active': False, 'enabled': True})

    def test_upgrade_preserves_stopped_but_enabled_core_and_adds_worker(self):
        result = self.apply(fresh=False)
        self.assertEqual(result['startup'], 'preserve')
        self.assertEqual(result['services']['paopaogw'], self.original['paopaogw'])
        self.assertEqual(result['services']['paopaogw-api'], self.original['paopaogw-api'])
        self.assertEqual(result['services']['paopaogw-settings'], {'active': False, 'enabled': True})

    def test_failed_startup_verification_restores_original_service_states(self):
        self.system.verify_services = lambda _: (_ for _ in ()).throw(ManagementError('SERVICE_STATE_MISMATCH'))
        with self.assertRaisesRegex(ManagementError, 'DEPLOY_FAILED_ROLLED_BACK'): self.apply('auto')
        self.assertEqual(self.states[-1], self.original)
        self.assertFalse(self.layout.path('etc/systemd/system/paopaogw-settings.service').exists())
        journal = json.loads(self.layout.path('var/lib/paopaogw/admin/transaction.json').read_bytes())
        self.assertEqual(journal['phase'], 'rolled_back')


if __name__ == '__main__': unittest.main()
