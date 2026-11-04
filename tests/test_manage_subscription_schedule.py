import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError, operation_lock
from manage_subscription_schedule import due_providers, record_attempt, identity
from unified_editor import validate_ppsub
import subscription_admin as admin
from manage_model import settings_revision


class ProviderScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.source = self.layout.path('etc/paopaogw/files/ppsub.json')
        self.source.parent.mkdir(parents=True)
        self.rows = [{'name': 'ProviderA', 'url': 'https://example.com/private', 'update_mode': 'manual'},
                     {'name': 'ProviderB', 'url': 'https://example.com/k', 'update_mode': 'auto', 'update_interval': '1d'},
                     {'name': 'ProviderC', 'url': 'https://example.com/d', 'update_mode': 'auto', 'update_interval': '6h'},
                     {'name': 'Inherited', 'url': 'https://example.com/i'}]
        self.save()
        self.settings = {'ini': {'subtime': 'manual', 'subcron': ''}}

    def save(self):
        self.source.write_text(json.dumps({'exported_at': 'fixture', 'subs': self.rows}))

    def test_independent_plans_survive_restart_and_manual_never_becomes_due(self):
        self.assertEqual(due_providers(self.layout, self.settings, 1000), [])
        self.assertEqual(due_providers(Layout(self.temp.name), self.settings, 1000 + 21600), ['ProviderC'])
        record_attempt(self.layout, self.rows, ['ProviderC'], 1000 + 21600)
        self.assertEqual(due_providers(self.layout, self.settings, 1000 + 86400), ['ProviderB', 'ProviderC'])
        record_attempt(self.layout, self.rows, ['ProviderB', 'ProviderC'], 1000 + 86400)
        self.assertEqual(due_providers(self.layout, self.settings, 1000 + 86430), [])

    def test_failed_attempt_waits_interval_and_source_or_plan_change_resets_clock(self):
        due_providers(self.layout, self.settings, 1000)
        record_attempt(self.layout, self.rows, ['ProviderB'], 1000 + 86400)
        self.assertNotIn('ProviderB', due_providers(self.layout, self.settings, 1000 + 86401))
        self.rows[1]['url'] += '/new'; self.save()
        self.assertNotIn('ProviderB', due_providers(self.layout, self.settings, 200000))
        self.assertIn('ProviderB', due_providers(self.layout, self.settings, 286400))
        self.rows[1]['update_mode'] = 'manual'; self.save()
        self.assertNotIn('ProviderB', due_providers(self.layout, self.settings, 999999))

    def test_inherited_interval_and_daily_hour_leave_explicit_modes_independent(self):
        self.settings['ini']['subtime'] = '2h'
        due_providers(self.layout, self.settings, 1000)
        self.assertEqual(due_providers(self.layout, self.settings, 8200), ['Inherited'])
        from datetime import datetime
        self.settings['ini']['subcron'] = 8
        start = datetime(2026, 11, 4, 7, 0).timestamp()
        due_providers(self.layout, self.settings, start)
        self.assertIn('Inherited', due_providers(self.layout, self.settings, start + 3600))
        record_attempt(self.layout, self.rows, ['Inherited'], start + 3600)
        self.assertNotIn('Inherited', due_providers(self.layout, self.settings, start + 7200))
        self.assertIn('Inherited', due_providers(self.layout, self.settings, start + 90000))

    def test_bad_plan_values_rejected_by_editor_and_source_import(self):
        for value in ({'update_mode': 'typo'}, {'update_mode': []}, {'update_interval': '0d'},
                      {'update_interval': 'manual'}, {'update_interval': '1d;bad'}, {'update_interval': 2}):
            with self.subTest(value=value), self.assertRaises(ManagementError):
                validate_ppsub({'exported_at': 'fixture', 'subs': [dict(self.rows[0], **value)]})

    def test_scheduled_worker_receives_only_due_names_and_respects_shared_lock(self):
        config = self.source.parent / 'ppgw.ini'
        config.write_text('#paopao-gateway\nmode=suburl\nsuburl=ppsub@http://127.0.0.1/ppsub.json\nsubtime=manual\n')
        manager = admin.Admin()
        with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', config.parent), \
             patch('manage_subscription_schedule.time.time', return_value=1000):
            manager.scheduled_tick()
        with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', config.parent), \
             patch('manage_subscription_schedule.time.time', return_value=1000 + 86400), \
             patch.object(manager, 'refresh_nodes_worker') as worker:
            manager.scheduled_tick()
            self.assertEqual(worker.call_args.args[0]['providers'], ['ProviderB', 'ProviderC'])
            manager.scheduled_tick()
            self.assertEqual(worker.call_count, 1)
            manager.operation = None
            with operation_lock(self.layout), patch('subscription_admin.management_lock',
                    side_effect=ManagementError('MANAGEMENT_BUSY')):
                with self.assertRaises(ManagementError): manager.scheduled_tick()
            self.assertEqual(worker.call_count, 1)


if __name__ == '__main__': unittest.main()
