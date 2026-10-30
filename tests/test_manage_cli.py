from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError, operation_lock
from manage_assets import import_asset
from gateway_api import core_base_url

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        ini = self.layout.path('etc/paopaogw/files/ppgw.ini'); ini.parent.mkdir(parents=True)
        ini.write_text('#paopao-gateway\nmode=free\nclash_web_password=old-password-long\n')
    def run_cli(self, *args, stdin=''):
        return subprocess.run([sys.executable, str(ROOT / 'integration/manage.py'), '--root', str(self.layout.root), *args], input=stdin, text=True, capture_output=True)
    def test_show_redacts_and_batch_apply_requires_revision_and_confirmation(self):
        result = self.run_cli('config', 'show'); self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout); self.assertNotIn('old-password-long', result.stdout)
        payload = json.dumps({'base_revision': value['revision'], 'patch': {'ini': {'max_rec': 42}}})
        result = self.run_cli('config', 'apply', '--file', '-', stdin=payload)
        self.assertNotEqual(result.returncode, 0)
        result = self.run_cli('config', 'apply', '--file', '-', '--yes', stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'applied')
        self.assertNotEqual(self.run_cli('config', 'apply', '--file', '-', '--yes', stdin=payload).returncode, 0)
    def test_headless_menu_does_not_hang_and_no_secret_args(self):
        self.assertNotEqual(self.run_cli().returncode, 0)
        self.assertNotEqual(self.run_cli('config', 'apply', '--password', 'secret').returncode, 0)
    def test_alternate_loopback_listener(self):
        env = self.layout.path('etc/paopaogw/service.env'); env.write_text('PPGW_WEB_LISTEN="0.0.0.0:8080"\n')
        self.assertEqual(core_base_url(env), 'http://127.0.0.1:8080')
        env.write_text('PPGW_WEB_LISTEN="[::]:8081"\n')
        self.assertEqual(core_base_url(env), 'http://[::1]:8081')
        env.write_text('PPGW_WEB_LISTEN="127.0.0.1:80@evil"\n')
        with self.assertRaises(ManagementError): core_base_url(env)

    def test_api_name_matches_runtime_contract_and_ipv6_readiness_url_shape(self):
        payload = {'api': {'name': 'x' * 101}}
        from manage_model import render_updates
        with self.assertRaises(ManagementError): render_updates(self.layout, payload)
    def test_assets_reject_traversal_unsafe_vpn_and_overwrite(self):
        source = self.layout.path('source.ovpn'); source.write_text('client\nremote 192.0.2.1 1194\nup /tmp/script\n')
        with self.assertRaises(ManagementError): import_asset(self.layout, source, 'ovpn', 'custom.ovpn')

    def test_invalid_yaml_returns_managed_error_and_menu_remains_open(self):
        import io
        from contextlib import redirect_stdout
        from manage import menu
        from manage_runtime import System
        source = self.layout.path('broken.yaml'); source.write_text('proxies: [')
        with self.assertRaisesRegex(ManagementError, 'INVALID_ASSET_CONTENT'):
            import_asset(self.layout, source, 'yaml', 'custom.yaml')
        answers = iter(['4', 'yaml', str(source), 'custom.yaml', 'no', '0'])
        output = io.StringIO()
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=lambda _: next(answers)), redirect_stdout(output):
            self.assertEqual(menu(self.layout, System(self.layout)), 0)
        self.assertIn('操作失败', output.getvalue())
        self.assertFalse(self.layout.path('etc/paopaogw/files/custom.yaml').exists())

    def test_vpn_quoted_hook_and_external_include_are_rejected(self):
        source = self.layout.path('source.ovpn')
        for directive in ('"up" "/bin/sh"', '"script-security" 2', '"config" "/etc/secret"', 'm\\anagement 127.0.0.1 9999', 'tls-crypt-v2 /etc/key', 'redirect-gateway def1', 'route 0.0.0.0 0.0.0.0'):
            source.write_text('client\nremote 192.0.2.1 1194\n' + directive + '\n')
            with self.subTest(directive=directive), self.assertRaises(ManagementError): import_asset(self.layout, source, 'ovpn', 'safe.ovpn')
        source.write_text('client\nremote 192.0.2.1 1194\n')
        with self.assertRaises(ManagementError): import_asset(self.layout, source, 'ovpn', '../bad')
        import_asset(self.layout, source, 'ovpn', 'custom.ovpn')
        with self.assertRaises(ManagementError): import_asset(self.layout, source, 'ovpn', 'custom.ovpn')


if __name__ == '__main__': unittest.main()
