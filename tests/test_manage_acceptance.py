import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_acceptance import Acceptance, CHECK
from manage_common import Layout, ManagementError, operation_lock, write_json
from manage_model import settings_revision
from manage_operations import RECEIPT, remember_loaded, summary


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.live = patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True)
        self.live.start(); self.addCleanup(self.live.stop)
        root = patch.object(Layout, 'require_root'); root.start(); self.addCleanup(root.stop)
        self.runtime = {'boot_id': '12345678-1234-1234-1234-123456789abc', 'config_revision': 'a' * 64,
                        'configuration_state': 'loaded', 'last_load_result': 'success'}
        self.system = Mock(); self.system.runtime.return_value = self.runtime
        self.system.verify_current.return_value = ['core_ini', 'service_environment', 'management_services']
        self.acceptance = Acceptance(self.layout, self.system)

    def test_rechecks_new_boot_and_writes_only_safe_receipt(self):
        remember_loaded(self.layout, 'obsolete', {**self.runtime, 'boot_id': 'old'})
        result = self.acceptance.check()
        self.assertEqual(result['result'], 'verified')
        value = summary(self.layout, settings_revision(self.layout), self.runtime)
        self.assertEqual(value['state'], 'loaded')
        self.assertEqual(value['verification']['checked_at'], result['checked_at'])
        self.system.command.assert_not_called(); self.system.apply.assert_not_called()

    def test_mismatch_cannot_reuse_matching_old_success(self):
        revision = settings_revision(self.layout)
        remember_loaded(self.layout, revision, self.runtime)
        old = self.layout.path(RECEIPT).read_bytes()
        self.system.verify_current.side_effect = ManagementError('RUNNING_CONFIG_MISMATCH')
        result = self.acceptance.check()
        self.assertEqual(result['error'], 'RUNNING_CONFIG_MISMATCH')
        self.assertEqual(self.layout.path(RECEIPT).read_bytes(), old)
        self.assertEqual(summary(self.layout, revision, self.runtime)['state'], 'saved_pending')

    def test_runtime_change_during_check_cannot_confirm(self):
        self.system.runtime.side_effect = [self.runtime, {**self.runtime, 'boot_id': 'b' * 36}]
        self.assertEqual(self.acceptance.check()['error'], 'STATE_CHANGED_DURING_CHECK')
        self.assertFalse(self.layout.path(RECEIPT).exists())

    def test_saved_change_during_check_cannot_confirm(self):
        self.system.verify_current.side_effect = lambda _: write_json(self.layout, 'etc/paopaogw/maintenance.json', {'auto_update': False}) or []
        self.assertEqual(self.acceptance.check()['error'], 'STATE_CHANGED_DURING_CHECK')
        self.assertFalse(self.layout.path(RECEIPT).exists())

    def test_mihomo_source_change_during_check_cannot_confirm_even_with_same_core_boot(self):
        self.system.runtime.side_effect = [self.runtime, {**self.runtime, 'mihomo_configuration': {'status':'loaded','source_revision':'new'}}]
        self.assertEqual(self.acceptance.check()['error'], 'STATE_CHANGED_DURING_CHECK')
        self.assertFalse(self.layout.path(RECEIPT).exists())

    def test_pending_recovery_is_never_overwritten(self):
        write_json(self.layout, 'var/lib/paopaogw/admin/transaction.json', {'phase': 'conflict'})
        with self.assertRaisesRegex(ManagementError, 'RECOVERY_REQUIRED'): self.acceptance.check()
        self.system.verify_current.assert_not_called()
        self.assertFalse(self.layout.path(CHECK).exists())

    def test_slow_check_does_not_block_a_writer_and_rejects_its_changed_snapshot(self):
        probing, release = threading.Event(), threading.Event()
        def slow(_):
            probing.set(); self.assertTrue(release.wait(3)); return ['core_ini']
        self.system.verify_current.side_effect = slow
        results=[]
        worker=threading.Thread(target=lambda: results.append(self.acceptance.check()))
        worker.start()
        try:
            self.assertTrue(probing.wait(3))
            with operation_lock(self.layout):
                write_json(self.layout, 'etc/paopaogw/maintenance.json', {'auto_update':False})
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['error'], 'STATE_CHANGED_DURING_CHECK')
        self.assertFalse(self.layout.path(RECEIPT).exists())

    def test_unexpected_errors_are_redacted(self):
        self.system.verify_current.side_effect = ValueError('private-password-in-error')
        result = self.acceptance.check()
        self.assertEqual(result['error'], 'STATE_SAMPLE_UNAVAILABLE')
        self.assertNotIn('private', self.layout.path(CHECK).read_text())

    def test_missing_runtime_identity_cannot_confirm(self):
        self.system.runtime.return_value = {}
        self.assertEqual(self.acceptance.check()['error'], 'CORE_NOT_RUNNING')
        self.system.verify_current.assert_not_called()

    def test_unfinished_transaction_keeps_recovery_status_even_with_successful_receipt(self):
        self.acceptance.check()
        self.assertEqual(summary(self.layout, settings_revision(self.layout), self.runtime, True)['state'], 'recovery_required')

    def test_changed_identity_marks_old_check_as_historical(self):
        self.acceptance.check()
        value = summary(self.layout, settings_revision(self.layout), {**self.runtime, 'boot_id': 'b'*36})
        self.assertEqual(value['state'], 'unknown')
        self.assertFalse(value['verification']['current'])
        value = summary(self.layout, settings_revision(self.layout), {**self.runtime, 'mihomo_configuration': {'status':'loaded','source_revision':'new'}})
        self.assertEqual(value['state'], 'unknown')
        self.assertFalse(value['verification']['current'])

    def test_triggers_coalesce_and_new_boot_can_recheck_without_waiting_a_minute(self):
        callbacks = []
        class Worker:
            def __init__(self, target, daemon): callbacks.append(target)
            def start(self): pass
        with patch('manage_acceptance.threading.Thread', Worker), patch.object(self.acceptance, 'check') as check:
            self.acceptance.trigger('saved', self.runtime)
            self.acceptance.trigger('saved', self.runtime)
            self.assertEqual(len(callbacks), 1)
            callbacks.pop()()
            self.acceptance.trigger('saved', self.runtime)
            self.assertEqual(len(callbacks), 0)
            self.acceptance.trigger('saved', {**self.runtime, 'boot_id': 'b'*36})
            self.assertEqual(len(callbacks), 1)
            callbacks.pop()(); self.assertEqual(check.call_count, 2)


if __name__ == '__main__': unittest.main()
