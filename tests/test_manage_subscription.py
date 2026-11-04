import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout
from manage_model import validate
from manage_subscription import refresh_nodes


class SubscriptionCacheTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.cache = self.layout.path('var/lib/paopaogw/subscriptions')
        self.cache.mkdir(parents=True)
        self.filename = 'a' * 64 + '.json'
        self.original = b'{"raw_yaml":"previous valid nodes"}\n'
        (self.cache / self.filename).write_bytes(self.original)
        source=self.layout.path('etc/paopaogw/files/ppsub.json'); source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'subs':[{'name':'Airport','url':'https://example.com/a'}]}))
        self.commands = []
        self.rows = [{'name': 'Airport', 'nodes': 3, 'source': 'fresh', 'update_failed': False}]
        self.validation_fails = False
        self.readiness_fails_once = False
        self.waits = 0
        self.system = SimpleNamespace(command=self.command, wait=self.wait)

    def command(self, *args):
        self.commands.append(args)
        return SimpleNamespace(stdout='123' if 'MainPID' in ' '.join(args) else '')

    def wait(self, settings):
        self.waits += 1
        if self.readiness_fails_once and self.waits == 1:
            raise RuntimeError('synthetic readiness failure')

    def execute(self, command, **kwargs):
        if '-ppsub' in command:
            stage = Path(kwargs['env']['PPGW_SUB_CACHE_DIR'])
            self.assertEqual(kwargs['env']['PPGW_SUB_CACHE_ONLY'], '0')
            self.assertEqual((stage / self.filename).read_bytes(), self.original)
            self.assertEqual((self.cache / self.filename).read_bytes(), self.original)
            (stage / self.filename).write_bytes(b'{"raw_yaml":"new valid nodes"}\n')
            (stage / 'status.json').write_text(json.dumps({'subscriptions': self.rows}))
            Path(command[command.index('-output') + 1]).write_text('proxies: []\n')
        elif '-input' in command:
            self.assertIn('/tmp/clash_base.yaml', command)
            Path(command[command.index('-output') + 1]).write_text('geodata-mode: true\nproxies: []\n')
        elif '-t' in command and '-f' in command and self.validation_fails:
            raise RuntimeError('synthetic invalid subscription')
        elif '-f' in command:
            self.assertIn('geodata-mode: true', Path(command[command.index('-f') + 1]).read_text())
        return SimpleNamespace(returncode=0)

    def test_manual_schedule_is_valid_and_other_interval_values_remain_bounded(self):
        self.assertEqual(validate('ini', 'subtime', 'manual'), 'manual')
        self.assertEqual(validate('ini', 'subtime', '365d'), '365d')
        for value in ('manual; command', '0d', '10000d'):
            with self.assertRaises(ValueError): validate('ini', 'subtime', value)

    def test_invalid_payload_preserves_live_cache_and_running_service(self):
        self.validation_fails = True
        with patch('manage_subscription.subprocess.run', side_effect=self.execute):
            with self.assertRaises(RuntimeError): refresh_nodes(self.layout, self.system)
        self.assertEqual((self.cache / self.filename).read_bytes(), self.original)
        self.assertFalse(any('restart' in cmd for cmd in self.commands))

    def test_total_download_failure_keeps_nodes_without_restarting(self):
        self.rows[0].update(source='cache', update_failed=True)
        with patch('manage_subscription.subprocess.run', side_effect=self.execute):
            rows = refresh_nodes(self.layout, self.system)
        self.assertTrue(rows[0]['update_failed'])
        self.assertEqual((self.cache / self.filename).read_bytes(), self.original)
        self.assertFalse(any('restart' in cmd for cmd in self.commands))

    def test_new_nodes_publish_only_after_validation_and_survive_readiness(self):
        with patch('manage_subscription.subprocess.run', side_effect=self.execute):
            rows = refresh_nodes(self.layout, self.system)
        self.assertEqual(rows, self.rows)
        self.assertIn(b'new valid nodes', (self.cache / self.filename).read_bytes())
        self.assertEqual(self.waits, 1)
        self.assertEqual(self.commands.count(('systemctl', 'restart', 'paopaogw')), 1)

    def test_selected_refresh_sends_only_chosen_names_and_rejects_unknown_provider(self):
        def execute(command, **kwargs):
            if '-ppsub' in command:
                self.assertEqual(json.loads(kwargs['env']['PPGW_SUB_REFRESH_NAMES']), ['Airport'])
            return self.execute(command, **kwargs)
        with patch('manage_subscription.subprocess.run', side_effect=execute):
            refresh_nodes(self.layout, self.system, ['Airport'])
            with self.assertRaisesRegex(ValueError, 'INVALID_EDITOR_REQUEST'):
                refresh_nodes(self.layout, self.system, ['Unknown'])

    def test_failed_load_restores_original_nodes_and_verifies_recovery(self):
        self.readiness_fails_once = True
        with patch('manage_subscription.subprocess.run', side_effect=self.execute):
            with self.assertRaises(RuntimeError): refresh_nodes(self.layout, self.system)
        self.assertEqual((self.cache / self.filename).read_bytes(), self.original)
        self.assertEqual(self.waits, 2)
        self.assertEqual(self.commands.count(('systemctl', 'restart', 'paopaogw')), 2)


if __name__ == '__main__': unittest.main()
