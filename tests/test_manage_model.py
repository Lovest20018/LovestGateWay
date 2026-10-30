from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError, operation_lock
from manage_model import load_settings, render_updates, settings_revision, public_settings


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.ini = self.layout.path('etc/paopaogw/files/ppgw.ini')
        self.ini.parent.mkdir(parents=True)
        self.ini.write_bytes(b'#paopao-gateway\r\n# retained comment\r\nmode="free"\r\nclash_web_password="old-password-long-enough"\r\ncustom_extra="kept"\r\n')

    def test_roundtrip_preserves_comments_unknown_fields_and_secret_quotes(self):
        updates, changes = render_updates(self.layout, {'ini': {'dns_port': 5353, 'clash_web_password': "new'p#ass$word-long"}})
        raw = updates['etc/paopaogw/files/ppgw.ini']
        self.assertIn(b'# retained comment', raw)
        self.assertIn(b'custom_extra="kept"', raw)
        self.ini.write_bytes(raw)
        self.assertEqual(load_settings(self.layout)['ini']['clash_web_password'], "new'p#ass$word-long")
        self.assertEqual(load_settings(self.layout)['ini']['dns_port'], 5353)
        self.assertEqual(changes['ini']['dns_port'], 5353)

    def test_defaults_and_equal_patch_are_noop(self):
        updates, changes = render_updates(self.layout, {'ini': {'mode': 'free', 'dns_port': 53}})
        self.assertEqual(updates, {})
        self.assertEqual(changes, {})

    def test_native_ppsub_source_and_escaped_quotes_match_core_parser(self):
        self.ini.write_bytes(b'#paopao-gateway\nsuburl="ppsub@http://127.0.0.1/ppsub.json"\nclash_web_password="one\\$two\\`three\\\"four" # retain inline\n')
        self.assertEqual(load_settings(self.layout)['ini']['clash_web_password'], 'one$two`three"four')
        updates, _ = render_updates(self.layout, {'ini': {'clash_web_password': 'another-long-password'}})
        self.assertIn(b'# retain inline', updates['etc/paopaogw/files/ppgw.ini'])

    def test_redaction_and_revision_cover_credentials(self):
        value = public_settings(load_settings(self.layout))
        self.assertEqual(value['ini']['clash_web_password'], '********')
        before = settings_revision(self.layout)
        self.ini.write_text(self.ini.read_text().replace('old-password', 'new-password'))
        self.assertNotEqual(before, settings_revision(self.layout))

    def test_invalid_or_disabled_settings_are_rejected(self):
        bad = [
            {'ini': {'dns_port': True}}, {'ini': {'udp_max_sessions': -1}},
            {'ini': {'admin_cidr': 'all'}}, {'ini': {'cloudflared_token': 'x'}},
            {'ini': {'tune_rps': True}}, {'service': {'PPGW_INTERFACE': 'eth0;reboot'}},
            {'ini': {'yamlfile': '../secret'}}, {'ini': {'box': 'unknown=true'}},
            {'ini': {'clash_web_password': '********'}}, {'ini': {'dns_ip': 'host.invalid'}},
            {'maintenance': {'history_retention_days': 500}}, {'other': {}},
            {'ini': {'openport_auth': 'missing-colon'}}, {'ini': {'box': 'dns.shards=4'}},
            {'ini': {'subcron': '0 3 * * *'}}, {'ini': {'pplog_uuid': 'not-a-uuid'}},
        ]
        for patch in bad:
            with self.subTest(patch=patch), self.assertRaises(ManagementError):
                render_updates(self.layout, patch)

    def test_ipv6_marker_and_service_settings_move_together(self):
        env = self.layout.path('etc/paopaogw/service.env')
        env.write_text('# keep env\nPPGW_INTERFACE=eth0\nPPGW_IPV6=no\n')
        network = self.layout.path('etc/paopaogw/network')
        network.write_text('# unchanged host networking\n')
        updates, _ = render_updates(self.layout, {'service': {'PPGW_IPV6': True}})
        self.assertIn(b'eth06', updates['etc/paopaogw/network'])
        self.assertIn(b'# keep env', updates['etc/paopaogw/service.env'])
        for path, raw in updates.items(): self.layout.path(path).write_bytes(raw)
        updates, _ = render_updates(self.layout, {'service': {'PPGW_IPV6': False}})
        self.assertNotIn(b'eth06', updates['etc/paopaogw/network'])

    def test_json_unknown_fields_and_explicit_secret_clear(self):
        path = self.layout.path('etc/paopaogw/maintenance.json')
        path.write_text(json.dumps({'history_enabled': False, 'extra': 'keep'}))
        updates, _ = render_updates(self.layout, {'maintenance': {'history_retention_days': 7}, 'ini': {'socks5_password': None}})
        self.assertEqual(json.loads(updates['etc/paopaogw/maintenance.json'])['extra'], 'keep')
        self.assertNotIn('etc/paopaogw/files/ppgw.ini', updates)

    def test_symlink_targets_and_busy_lock_rejected(self):
        # Two independent opens in the same process must still exclude one another.
        with operation_lock(self.layout, reentrant=False):
            with self.assertRaises(ManagementError):
                with operation_lock(self.layout, reentrant=False): pass
        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + '-outside')
        try:
            self.ini.unlink()
            self.ini.symlink_to(outside)
        except OSError:
            self.skipTest('symlinks not permitted')
        with self.assertRaises(ManagementError): load_settings(self.layout)


if __name__ == '__main__': unittest.main()
