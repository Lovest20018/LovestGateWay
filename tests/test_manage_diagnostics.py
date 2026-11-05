import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_backup import BackupStore
from manage_common import Layout, ManagementError, digest, write_json
from manage_config import JOURNAL
from manage_diagnostics import Diagnostics


class SyntheticSystem:
    preserve_units = {'paopaogw-settings'}

    def __init__(self):
        self.states = {'paopaogw': {'active': False, 'enabled': False},
                       'paopaogw-api': {'active': False, 'enabled': False},
                       'paopaogw-settings': {'active': True, 'enabled': True}}
        self.calls = []

    def status(self): return {name: dict(state) for name, state in self.states.items()}
    def stop_all(self): self.calls.append('stop')
    def restore_services(self, states): self.calls.append('restore'); self.states.update(states)
    def verify_services(self, states): self.calls.append('verify')
    def wait(self, settings): self.calls.append('wait'); return {'status': 'running'}
    def wait_api(self, settings): self.calls.append('wait_api')


class DiagnosticsTests(unittest.TestCase):
    def test_packaged_worker_sandbox_allows_atomic_restore_without_broad_mounts(self):
        unit = (Path(__file__).resolve().parents[1] / 'deploy/debian/paopaogw-settings.service').read_text()
        self.assertIn('ProtectSystem=strict', unit.splitlines())
        writable = set(next(line for line in unit.splitlines() if line.startswith('ReadWritePaths=')).split('=', 1)[1].split())
        # An atomic file replacement needs the parent directory writable in the
        # service namespace, including the launcher and managed systemd units.
        for parent in ('/etc/paopaogw', '/var/lib/paopaogw', '/var/backups/paopaogw',
                       '/usr/local/lib/paopaogw', '/usr/local/bin', '/etc/systemd/system'):
            self.assertIn(parent, writable)
        self.assertFalse(writable & {'/', '/etc', '/usr', '/usr/local', '/var'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        self.system = SyntheticSystem()
        self.now = 1000
        self.diagnostics = Diagnostics(self.layout, self.system, clock=lambda: self.now)
        self.ini = self.layout.path('etc/paopaogw/files/ppgw.ini')
        self.ini.parent.mkdir(parents=True)
        self.ini.write_text('#paopao-gateway\nmode=free\nclash_web_password=historical-private-password\n')
        self.layout.path('etc/paopaogw/revision-key').write_text('diagnostic-private-key')
        self.program = self.layout.path('usr/local/lib/paopaogw/build.json')
        self.program.parent.mkdir(parents=True)
        self.program.write_text('{"version":"private-build-label"}')
        self.backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': self.system.status()})

    def preview(self): return self.diagnostics.preview_restore({'backup': self.backup})
    def payload(self, preview=None): return {'preview_token': (preview or self.preview())['preview_token']}
    def snapshot(self):
        return {path.relative_to(self.layout.root).as_posix(): path.read_bytes()
                for path in self.layout.root.rglob('*') if path.is_file()}

    def journal(self, **changes):
        value = {'kind': 'restore', 'phase': 'restoring', 'backup': self.backup,
                 'services': self.system.status(), 'expected': {}}
        value.update(changes); write_json(self.layout, JOURNAL, value)
        return value

    def test_report_backup_listing_and_previews_are_read_only_and_redacted(self):
        write_json(self.layout, 'var/lib/paopaogw/admin/operations/settings-fixture.json',
                   {'source': 'settings', 'kind': 'secret-object-name', 'status': 'failed', 'phase': 'complete',
                    'updated_at': 1, 'error': 'SECRET_ERROR_https://private.test/token',
                    'message': 'private-exception-password', 'result': {'config': 'secret'},
                    'execution': {'service': 'private-service-name'}})
        before = self.snapshot()
        report = self.diagnostics.report()
        listing = self.diagnostics.backups({})
        preview = self.preview()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.system.calls, [])
        self.assertEqual(listing['backups'][0]['integrity'], 'verified')
        self.assertEqual(report['recent'][0]['kind'], 'unknown')
        self.assertEqual(report['recent'][0]['error'], 'OPERATION_FAILED')
        raw = json.dumps([report, listing, preview])
        for text in ('historical-private-password', 'private-exception', 'secret-object', 'private-build-label',
                     'https://', 'private-service', 'diagnostic-private-key', 'ppgw.ini', 'sha256', str(self.layout.root)):
            self.assertNotIn(text, raw)

    def test_scope_counts_full_deletion_program_changes_and_preserved_worker(self):
        self.ini.write_text('new persistent settings')
        self.program.write_text('new program')
        extra = self.layout.path('etc/paopaogw/files/private-name.yaml'); extra.write_text('url: secret')
        preview = self.preview()
        self.assertEqual(preview['scope']['configuration']['modified'], 1)
        self.assertEqual(preview['scope']['program']['modified'], 1)
        self.assertEqual(preview['scope']['assets']['removed'], 1)
        self.assertTrue(preview['impact']['version_may_change'])
        self.assertTrue(preview['impact']['settings_worker_restart_required'])
        self.assertEqual(preview['impact']['service_restart'], [])
        self.assertTrue(preview['impact']['historical_credentials_may_return'])

    def test_partial_backup_keeps_current_services_and_unrelated_files(self):
        self.backup = BackupStore(self.layout).create(['etc/paopaogw/files/ppgw.ini'],
                      {'kind': 'config', 'services': {'paopaogw': {'active': True, 'enabled': True}}})
        extra = self.layout.path('etc/paopaogw/files/extra.yaml'); extra.write_text('keep')
        preview = self.preview()
        self.assertFalse(preview['full'])
        self.assertEqual(preview['scope']['assets']['removed'], 0)
        self.assertEqual(preview['target_services'], self.system.status())

    def test_legacy_bytecode_snapshot_is_verified_but_never_restored(self):
        cache = self.layout.path('usr/local/lib/paopaogw/__pycache__/synthetic.cpython-314.pyc')
        cache.parent.mkdir(); cache.write_bytes(b'historical-bytecode')
        name = cache.relative_to(self.layout.root).as_posix()
        self.backup = BackupStore(self.layout).create([name], {'kind': 'config'})
        cache.write_bytes(b'current-bytecode')
        preview = self.preview()
        self.assertTrue(all(not any(counts.values()) for counts in preview['scope'].values()))
        self.assertFalse(preview['impact']['program_files_changed'])
        self.assertFalse(preview['impact']['settings_worker_restart_required'])
        result = self.diagnostics.execute_restore(self.payload(preview))
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(cache.read_bytes(), b'current-bytecode')
        cache.unlink()
        result = self.diagnostics.execute_restore(self.payload())
        self.assertEqual(result['status'], 'restored')
        self.assertFalse(cache.exists())

    def test_settings_unit_changes_require_worker_restart_but_other_units_do_not(self):
        for name in ('paopaogw-settings', 'paopaogw-api'):
            unit = self.layout.path('etc/systemd/system/' + name + '.service')
            unit.parent.mkdir(parents=True, exist_ok=True); unit.write_text('historical unit')
            self.backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': self.system.status()})
            unit.write_text('current unit')
            preview = self.preview()
            self.assertEqual(preview['scope']['services']['modified'], 1)
            self.assertFalse(preview['impact']['program_files_changed'])
            self.assertEqual(preview['impact']['settings_worker_restart_required'], name == 'paopaogw-settings')
            unit.write_text('historical unit')

    def test_preserved_settings_service_state_difference_is_explicit(self):
        original = self.system.status()
        original['paopaogw-settings'] = {'active': False, 'enabled': False}
        self.backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': original})
        preview = self.preview()
        self.assertEqual(preview['target_services']['paopaogw-settings'], original['paopaogw-settings'])
        self.assertEqual(preview['effective_services']['paopaogw-settings'], self.system.states['paopaogw-settings'])
        self.assertTrue(preview['impact']['settings_worker_state_preserved'])
        self.assertFalse(preview['impact']['settings_worker_restart_required'])
        result = self.diagnostics.execute_restore(self.payload(preview))
        self.assertTrue(result['settings_worker_state_preserved'])

    def test_corrupt_target_is_listed_without_content_and_never_executed(self):
        index = BackupStore(self.layout).index(self.backup)
        record = next(record for record in index['files'].values() if record)
        data = self.layout.path(f'var/backups/paopaogw/{self.backup}/data/{record["data"]}')
        data.write_bytes(b'corrupt-private')
        listing = self.diagnostics.backups({})
        self.assertEqual(listing['backups'][0]['integrity'], 'invalid')
        self.assertIsNone(listing['backups'][0]['file_count'])
        with self.assertRaisesRegex(ManagementError, 'BACKUP_CHECKSUM_MISMATCH'): self.preview()
        self.assertEqual(self.system.calls, [])

    def test_corrupt_target_after_preview_fails_before_stop(self):
        payload = self.payload()
        index = BackupStore(self.layout).index(self.backup)
        record = next(record for record in index['files'].values() if record)
        self.layout.path(f'var/backups/paopaogw/{self.backup}/data/{record["data"]}').write_bytes(b'changed')
        with self.assertRaisesRegex(ManagementError, 'BACKUP_CHECKSUM_MISMATCH'):
            self.diagnostics.execute_restore(payload)
        self.assertEqual(self.system.calls, [])

    def test_external_settings_program_asset_add_and_service_change_expire_preview(self):
        changes = [lambda: self.ini.write_text('externally changed'),
                   lambda: self.program.write_text('externally changed'),
                   lambda: self.layout.path('etc/paopaogw/files/new.yaml').write_text('new'),
                   lambda: self.system.states['paopaogw'].update(enabled=True)]
        for change in changes:
            payload = self.payload()
            change()
            with self.assertRaisesRegex(ManagementError, 'STALE_RECOVERY_PREVIEW'):
                self.diagnostics.execute_restore(payload)
        self.assertEqual(self.system.calls, [])

    def test_token_expires_and_tampering_cannot_call_transaction(self):
        payload = self.payload()
        self.now += 300
        with self.assertRaisesRegex(ManagementError, 'PREVIEW_EXPIRED'):
            self.diagnostics.execute_restore(payload)
        self.now = 1000
        encoded, signature = payload['preview_token'].split('.')
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        claims['backup'] = '20000101000000-' + 'a' * 12
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        with self.assertRaisesRegex(ManagementError, 'INVALID_RECOVERY_PREVIEW'):
            self.diagnostics.execute_restore({'preview_token': encoded + '.' + signature})
        self.assertEqual(self.system.calls, [])

    def test_preview_token_does_not_contain_offline_testable_file_digests(self):
        preview = self.preview()
        encoded = preview['preview_token'].split('.')[0]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        self.assertEqual(set(claims), {'schema', 'action', 'backup', 'current', 'target', 'expires'})
        self.assertNotIn(digest(self.ini.read_bytes()), json.dumps(claims))

    def test_target_metadata_changes_invalidate_token_even_when_data_unchanged(self):
        payload = self.payload()
        index = BackupStore(self.layout).index(self.backup)
        index['metadata']['kind'] = 'deploy'
        write_json(self.layout, f'var/backups/paopaogw/{self.backup}/index.json', index)
        with self.assertRaisesRegex(ManagementError, 'STALE_RECOVERY_PREVIEW'):
            self.diagnostics.execute_restore(payload)
        self.assertEqual(self.system.calls, [])

    def test_volatile_state_changes_do_not_invalidate_persistent_revision(self):
        payload = self.payload()
        for name in ('var/lib/paopaogw/history/observations.sqlite3',
                     'var/lib/paopaogw/logs/controller.log',
                     'var/lib/paopaogw/subscriptions/schedule.json',
                     'var/lib/paopaogw/subscriptions/status.json',
                     'var/lib/paopaogw/clash/cache.db'):
            write_json(self.layout, name, {'counter': 1})
        with patch('manage.restore_backup', return_value={'status': 'restored'}) as restore:
            self.assertEqual(self.diagnostics.execute_restore(payload)['status'], 'restored')
        restore.assert_called_once_with(self.layout, self.system, self.backup, progress=self.diagnostics.progress)

    def test_persistent_var_payloads_and_unknown_files_invalidate_confirmation(self):
        names = ['var/lib/paopaogw/subscriptions/' + 'a' * 64 + '.json',
                 'var/lib/paopaogw/core-token', 'var/lib/paopaogw/hosts',
                 'var/lib/paopaogw/unknown-state.json',
                 'var/lib/paopaogw/clash/clash-dashboard/ui/index.html']
        for name in names:
            payload = self.payload()
            write_json(self.layout, name, {'changed': True})
            with self.assertRaisesRegex(ManagementError, '^STALE_RECOVERY_PREVIEW$'):
                self.diagnostics.execute_restore(payload)
        self.assertEqual(self.system.calls, [])

    def test_dashboard_assets_are_program_scope_and_require_worker_notice(self):
        write_json(self.layout, 'var/lib/paopaogw/clash/clash-dashboard/ui/index.html', {'changed': True})
        preview = self.preview()
        self.assertEqual(preview['scope']['program']['removed'], 1)
        self.assertTrue(preview['impact']['program_files_changed'])
        self.assertTrue(preview['impact']['runtime_data_in_revision'])
        self.assertTrue(preview['impact']['volatile_runtime_excluded'])

    def test_prepared_configuration_journal_can_be_previewed_and_recovered(self):
        self.journal(kind='config', phase='prepared')
        preview = self.diagnostics.preview_recover({})
        self.assertEqual(self.diagnostics.report()['recovery'], {'required': True, 'phase': 'prepared'})
        self.assertEqual(self.diagnostics.execute_recover(self.payload(preview))['status'], 'rolled_back')

    def test_restore_uses_existing_rescue_and_transaction_workflow(self):
        self.ini.write_text('#paopao-gateway\nmode=free\nclash_web_password=changed-private-password\n')
        value = self.diagnostics.execute_restore(self.payload())
        self.assertEqual(value['status'], 'restored')
        self.assertIn('historical-private-password', self.ini.read_text())
        self.assertNotEqual(value['rescue_backup'], self.backup)
        self.assertEqual(json.loads(self.layout.path(JOURNAL).read_bytes())['phase'], 'complete')

    def test_recover_checks_external_conflicts_without_stopping_services(self):
        self.journal(expected={'etc/paopaogw/files/ppgw.ini': ['0' * 64]})
        before = self.snapshot()
        with self.assertRaisesRegex(ManagementError, 'EXTERNAL_CONFIG_CONFLICT'):
            self.diagnostics.preview_recover({})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.system.calls, [])

    def test_recover_preview_journal_change_and_action_confusion_are_rejected(self):
        self.journal()
        preview = self.diagnostics.preview_recover({})
        with self.assertRaisesRegex(ManagementError, 'INVALID_RECOVERY_PREVIEW'):
            self.diagnostics.execute_restore(self.payload(preview))
        self.journal(phase='rollback_unconfirmed')
        with self.assertRaisesRegex(ManagementError, 'STALE_RECOVERY_PREVIEW'):
            self.diagnostics.execute_recover(self.payload(preview))
        self.assertEqual(self.system.calls, [])

    def test_recover_calls_existing_manager_under_management_lock(self):
        self.journal()
        preview = self.diagnostics.preview_recover({})
        from manage_common import operation_lock_descriptor
        def recovered():
            self.assertIsInstance(operation_lock_descriptor(self.layout), int)
            return {'status': 'rolled_back'}
        with patch.object(self.diagnostics.manager, 'recover', side_effect=recovered) as recover:
            self.assertEqual(self.diagnostics.execute_recover(self.payload(preview))['status'], 'rolled_back')
        recover.assert_called_once_with()

    def test_recovery_rejects_arbitrary_unit_names_and_invalid_record_types(self):
        index = BackupStore(self.layout).index(self.backup)
        index['metadata']['services']['--private-command'] = {'active': True, 'enabled': True}
        write_json(self.layout, f'var/backups/paopaogw/{self.backup}/index.json', index)
        with self.assertRaisesRegex(ManagementError, 'INVALID_BACKUP'): self.preview()
        self.assertEqual(self.diagnostics.backups({})['backups'][0]['integrity'], 'invalid')
        index['files']['etc/paopaogw/network'] = ['invalid-private-record']
        write_json(self.layout, f'var/backups/paopaogw/{self.backup}/index.json', index)
        with self.assertRaisesRegex(ManagementError, 'INVALID_BACKUP'): self.preview()

    def test_report_projects_known_errors_and_interrupted_operations_without_rewriting(self):
        write_json(self.layout, 'var/lib/paopaogw/admin/operations/settings-fixture.json',
                   {'source': 'settings', 'kind': 'restore', 'status': 'running', 'phase': 'verifying',
                    'updated_at': 15, 'execution': {'boot_id': 'dead', 'start_ticks': '0', 'pid': 99999999}})
        before = self.snapshot()
        with patch('manage_operations.alive', return_value=False): report = self.diagnostics.report()
        self.assertEqual(report['recent'][0]['status'], 'interrupted')
        self.assertTrue(report['recent'][0]['recovery_required'])
        self.assertIn('INTERRUPTED_CHECK_RECOVERY', report['causes'])
        self.assertEqual(self.snapshot(), before)

    def test_backup_list_is_paginated_and_unknown_metadata_is_not_returned(self):
        second = BackupStore(self.layout).create(['etc/paopaogw/files/ppgw.ini'], {'kind': 'private-kind', 'secret': 'hide'})
        rows = []
        for offset in (0, 1): rows += self.diagnostics.backups({'offset': offset, 'limit': 1})['backups']
        self.assertEqual(len(rows), 2)
        row = next(row for row in rows if row['id'] == second)
        self.assertEqual(row['kind'], 'unknown')
        for body in ({'offset': -1}, {'limit': 21}, {'offset': True}, {'path': '/etc/shadow'}):
            with self.assertRaisesRegex(ManagementError, 'INVALID_RECOVERY_REQUEST'): self.diagnostics.backups(body)

    def test_invalid_report_journal_and_failed_runtime_are_safe(self):
        self.layout.path(JOURNAL).parent.mkdir(parents=True, exist_ok=True)
        self.layout.path(JOURNAL).write_text('{private-invalid-journal')
        self.system.runtime = Mock(side_effect=RuntimeError('https://private/password'))
        with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True): report = self.diagnostics.report()
        self.assertTrue(report['recovery']['required'])
        self.assertEqual(report['runtime']['state'], 'unknown')
        self.assertIn('INVALID_TRANSACTION_JOURNAL', report['causes'])
        self.assertNotIn('private', json.dumps(report))

    def test_stopping_recovery_without_snapshot_has_zero_scope(self):
        self.journal(backup=None, phase='stopping')
        preview = self.diagnostics.preview_recover({})
        self.assertFalse(preview['impact']['historical_credentials_may_return'])
        self.assertTrue(all(not any(counts.values()) for counts in preview['scope'].values()))
        self.assertEqual(self.diagnostics.execute_recover(self.payload(preview))['status'], 'rolled_back')

    def test_live_recovery_failure_retains_unconfirmed_marker_and_supports_explicit_retry(self):
        self.system.states['paopaogw']['active'] = True
        self.journal(phase='rollback_unconfirmed')
        preview = self.diagnostics.preview_recover({})
        self.system.wait = Mock(side_effect=ManagementError('READINESS_TIMEOUT:private-url'))
        with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True), \
             patch.object(Layout, 'require_root'):
            with self.assertRaisesRegex(ManagementError, '^ROLLBACK_UNCONFIRMED$'):
                self.diagnostics.execute_recover(self.payload(preview))
            self.assertEqual(json.loads(self.layout.path(JOURNAL).read_bytes())['phase'], 'rollback_unconfirmed')
            self.system.wait = Mock(return_value={'status': 'running'})
            retry = self.diagnostics.preview_recover({})
            self.assertEqual(self.diagnostics.execute_recover(self.payload(retry))['status'], 'rolled_back')
        self.assertIn('verify', self.system.calls)

    def test_recovery_retains_worker_verifier_when_restored_program_is_older(self):
        import manage
        self.journal(backup=None, phase='stopping')
        preview = self.diagnostics.preview_recover({})
        acceptance = {'services': 'staged', 'core': 'staged', 'api': 'staged'}
        with patch.object(manage, 'verify_service_restore', Mock(return_value=acceptance)) as verifier:
            def historical_restore():
                # An older restored module may not define this new verifier.
                del manage.verify_service_restore
                return {'status': 'rolled_back'}
            with patch.object(self.diagnostics.manager, 'recover', side_effect=historical_restore):
                result = self.diagnostics.execute_recover(self.payload(preview))
        verifier.assert_called_once()
        self.assertEqual(result['acceptance'], acceptance)

    def test_recovery_expected_paths_cannot_expand_the_managed_boundary(self):
        self.journal(expected={'etc/passwd': [None]})
        with self.assertRaisesRegex(ManagementError, 'INVALID_TRANSACTION_JOURNAL'):
            self.diagnostics.preview_recover({})
        self.assertEqual(self.system.calls, [])

    def test_no_snapshot_configuration_recovery_and_invalid_preview_shape_are_rejected(self):
        self.journal(kind='config', backup=None)
        with self.assertRaisesRegex(ManagementError, 'INVALID_TRANSACTION_JOURNAL'):
            self.diagnostics.preview_recover({})
        for body in ({}, {'preview_token': None}, {'preview_token': 'broken'},
                     {'preview_token': 'a' * 2049}, {'preview_token': '', 'backup': self.backup}):
            with self.assertRaises(ManagementError): self.diagnostics.execute_restore(body)
        self.assertEqual(self.system.calls, [])

    def test_live_backup_and_restore_require_service_core_and_api_acceptance(self):
        from manage import create_backup, restore_backup
        self.system.states['paopaogw']['active'] = True
        self.system.states['paopaogw-api']['active'] = True
        write_json(self.layout, 'etc/paopaogw/api.json', {'enabled': True})
        self.backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': self.system.status()})
        self.system.wait = Mock(return_value={'boot_id': 'fixture', 'config_revision': 'fixture'})
        self.system.wait_api = Mock()
        with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True), patch.object(Layout, 'require_root'):
            backup = create_backup(self.layout, self.system)
            restored = restore_backup(self.layout, self.system, self.backup)
        for value in (backup, restored):
            self.assertEqual(value['acceptance'], {'services': 'verified', 'core': 'verified', 'api': 'verified'})
        self.assertEqual(self.system.wait.call_count, 2)
        self.assertEqual(self.system.wait_api.call_count, 2)
        self.assertTrue(self.layout.path('var/lib/paopaogw/admin/loaded-settings.json').is_file())

    def test_live_backup_and_restore_never_claim_success_after_unconfirmed_rollback(self):
        from manage import create_backup, restore_backup
        self.system.states['paopaogw']['active'] = True
        self.backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': self.system.status()})
        self.system.wait = Mock(side_effect=ManagementError('READINESS_TIMEOUT'))
        for operation in (lambda: create_backup(self.layout, self.system),
                          lambda: restore_backup(self.layout, self.system, self.backup)):
            with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True), patch.object(Layout, 'require_root'):
                with self.assertRaisesRegex(ManagementError, '^ROLLBACK_UNCONFIRMED$'): operation()
            self.assertEqual(json.loads(self.layout.path(JOURNAL).read_bytes())['phase'], 'rollback_unconfirmed')
            self.layout.path(JOURNAL).unlink()


class DiagnosticsJobTests(unittest.TestCase):
    payload = DiagnosticsTests.payload
    journal = DiagnosticsTests.journal

    def setUp(self):
        DiagnosticsTests.setUp(self)
        from manage_web import SettingsAdmin
        self.callbacks = []
        self.admin = SettingsAdmin(self.layout, self.system, start_job=self.callbacks.append)
        self.admin.diagnostics.clock = lambda: self.now

    def test_restore_job_is_idempotent_after_revision_key_restore_and_worker_restart(self):
        from manage_web import SettingsAdmin
        self.layout.path('etc/paopaogw/revision-key').write_text('new-key-to-be-restored')
        preview = self.admin.diagnostics.preview_restore({'backup': self.backup})
        body = {'id': 'a' * 32, 'kind': 'restore', 'payload': self.payload(preview)}
        self.assertEqual(self.admin.submit(body)['status'], 'queued')
        self.assertEqual(self.admin.submit(body)['status'], 'queued')
        with self.assertRaisesRegex(ManagementError, 'JOB_ID_REUSED'):
            self.admin.submit({**body, 'kind': 'backup', 'payload': {}})
        self.assertEqual(len(self.callbacks), 1)
        for path in self.layout.path('var/lib/paopaogw/admin/web-jobs').glob('*.json'):
            self.assertNotIn(preview['preview_token'], path.read_text())
        self.callbacks.pop()()
        self.assertEqual(self.admin.job(body['id'])['status'], 'succeeded')
        self.assertEqual(self.layout.path('etc/paopaogw/revision-key').read_text(), 'diagnostic-private-key')
        self.assertEqual(self.admin.submit(body)['status'], 'succeeded')
        restarted = SettingsAdmin(self.layout, self.system, start_job=self.callbacks.append)
        self.assertEqual(restarted.submit(body)['status'], 'succeeded')
        self.assertEqual(self.callbacks, [])
        self.assertNotIn('request_digest', json.dumps(restarted.job(body['id'])))

    def test_backup_job_reuses_existing_workflow_and_reports_acceptance(self):
        body = {'id': 'b' * 32, 'kind': 'backup', 'payload': {}}
        self.admin.submit(body); self.callbacks.pop()()
        value = self.admin.job(body['id'])
        self.assertEqual(value['status'], 'succeeded')
        self.assertEqual(value['result']['status'], 'backed_up')
        self.assertEqual(value['result']['acceptance']['services'], 'staged')
        self.assertEqual(self.system.calls, ['stop', 'restore', 'verify'])

    def test_queued_restore_revalidates_expiry_without_stopping_services(self):
        preview = self.admin.diagnostics.preview_restore({'backup': self.backup})
        body = {'id': 'c' * 32, 'kind': 'restore', 'payload': self.payload(preview)}
        self.admin.submit(body)
        self.now += 301
        self.callbacks.pop()()
        self.assertEqual(self.admin.job(body['id'])['error'], 'PREVIEW_EXPIRED')
        self.assertEqual(self.system.calls, [])

    def test_interrupted_restore_job_is_never_replayed_on_restart(self):
        from manage_web import SettingsAdmin
        preview = self.admin.diagnostics.preview_restore({'backup': self.backup})
        body = {'id': 'd' * 32, 'kind': 'restore', 'payload': self.payload(preview)}
        self.admin.submit(body)
        self.callbacks.clear()
        restarted = SettingsAdmin(self.layout, self.system, start_job=self.callbacks.append)
        value = restarted.submit(body)
        self.assertEqual(value['error'], 'INTERRUPTED_CHECK_RECOVERY')
        self.assertEqual(value['operation_result'], 'unknown')
        self.assertEqual(value['recovery'], 'check_required')
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.system.calls, [])

    def test_recover_job_uses_fixed_payload_and_private_error_projection(self):
        self.journal()
        preview = self.admin.diagnostics.preview_recover({})
        body = {'id': 'e' * 32, 'kind': 'recover', 'payload': self.payload(preview)}
        self.admin.submit(body)
        with patch.object(self.admin.diagnostics.manager, 'recover', side_effect=ManagementError('PRIVATE_EXCEPTION:https://secret')):
            self.callbacks.pop()()
        self.assertEqual(self.admin.job(body['id'])['error'], 'OPERATION_FAILED')
        self.assertNotIn('secret', json.dumps(self.admin.job(body['id'])))

    def test_duplicate_job_retry_works_while_original_transaction_holds_management_lock(self):
        from manage_common import operation_lock
        body = {'id': 'f' * 32, 'kind': 'backup', 'payload': {}}
        self.admin.submit(body)
        held, release = threading.Event(), threading.Event()
        def hold_lock():
            with operation_lock(self.layout):
                held.set()
                release.wait(5)
        thread = threading.Thread(target=hold_lock)
        thread.start()
        self.assertTrue(held.wait(3))
        try:
            self.assertEqual(self.admin.submit(body)['status'], 'queued')
            with self.assertRaisesRegex(ManagementError, 'JOB_ID_REUSED'):
                self.admin.submit({**body, 'payload': {'unexpected': 'private'}})
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())




if __name__ == '__main__': unittest.main()
