from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError
from manage_config import ConfigManager
from manage_model import settings_revision


class FakeSystem:
    def __init__(self):
        self.calls = []; self.fail = False; self.external = None
        self.states = {'paopaogw': {'active': True, 'enabled': False}}
    def status(self): return self.states.copy()
    def preflight(self, settings): pass
    def apply(self, action, settings, changes, before):
        self.calls.append(action)
        if self.external: self.external()
        if self.fail: raise ManagementError('HEALTH_FAILED')
    def restore_services(self, states): self.calls.append('restore')


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name); self.system = FakeSystem()
        self.ini = self.layout.path('etc/paopaogw/files/ppgw.ini'); self.ini.parent.mkdir(parents=True)
        self.original = b'#paopao-gateway\nmode=free\nnet_rec=yes\nclash_web_password=long-enough-password\n'
        self.ini.write_bytes(self.original)
        self.manager = ConfigManager(self.layout, self.system)
    def apply(self, patch, **kw): return self.manager.apply(patch, settings_revision(self.layout), **kw)
    def test_noop_and_dryrun_have_no_file_or_service_effect(self):
        self.assertEqual(self.apply({'ini': {'mode': 'free'}})['status'], 'unchanged')
        self.apply({'ini': {'mode': 'yaml'}}, dry_run=True)
        self.assertEqual(self.ini.read_bytes(), self.original); self.assertEqual(self.system.calls, [])
    def test_revision_conflict_does_not_write(self):
        with self.assertRaises(ManagementError): self.manager.apply({'ini': {'max_rec': 9}}, 'stale')
        self.assertEqual(self.ini.read_bytes(), self.original)
    def test_reload_and_restart_are_classified(self):
        self.apply({'ini': {'max_rec': 9}})
        self.assertEqual(self.system.calls, ['reload'])
        self.apply({'ini': {'udp_enable': True}})
        self.assertEqual(self.system.calls[-1], 'restart')
    def test_failed_health_restores_original_and_records_journal(self):
        self.system.fail = True
        with self.assertRaises(ManagementError): self.apply({'ini': {'max_rec': 9}})
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertEqual(self.manager.journal()['phase'], 'rolled_back')
    def test_external_edit_is_not_clobbered(self):
        self.system.fail = True; self.system.external = lambda: self.ini.write_bytes(b'external edit')
        with self.assertRaises(ManagementError): self.apply({'ini': {'max_rec': 9}})
        self.assertEqual(self.ini.read_bytes(), b'external edit')
        self.assertEqual(self.manager.journal()['phase'], 'conflict')
    def test_killed_after_one_write_recovers_from_persistent_journal(self):
        class Killed(BaseException): pass
        def kill(*args): raise Killed()
        self.system.apply = kill
        with self.assertRaises(Killed): self.apply({'ini': {'max_rec': 9}})
        self.assertNotEqual(self.ini.read_bytes(), self.original)
        self.manager.recover()
        self.assertEqual(self.ini.read_bytes(), self.original)
    def test_stale_ppsub_requires_explicit_source_cleanup(self):
        old = self.layout.path('etc/paopaogw/files/ppsub.json'); old.write_text('{}')
        with self.assertRaises(ManagementError): self.apply({'ini': {'suburl': 'https://example.com/x'}})
        self.apply({'ini': {'suburl': 'https://example.com/x'}}, reset_source=True)
        self.assertFalse(old.exists())

    def test_imported_native_ppsub_is_kept_when_switching_to_its_source(self):
        local = self.layout.path('etc/paopaogw/files/ppsub.json'); local.write_text('{"subs":[]}')
        self.apply({'ini': {'mode': 'suburl', 'suburl': 'ppsub@http://127.0.0.1/ppsub.json'}})
        self.assertTrue(local.exists())

    def test_explicit_restart_of_unchanged_config_is_a_verified_operation(self):
        self.apply({}, restart=True)
        self.assertEqual(self.system.calls, ['restart'])
    def test_backup_restore_and_tamper_refusal(self):
        from manage_backup import BackupStore
        store = BackupStore(self.layout)
        identifier = store.create(['etc/paopaogw/files/ppgw.ini'])
        self.ini.write_bytes(b'changed'); store.restore(identifier)
        self.assertEqual(self.ini.read_bytes(), self.original)
        data = store.directory(identifier) / 'data/000000'
        data.write_bytes(b'tampered')
        with self.assertRaises(ManagementError): store.restore(identifier)

    def test_interrupted_manual_restore_is_recoverable(self):
        from manage_backup import BackupStore
        from manage import restore_backup
        class Killed(BaseException): pass
        store = BackupStore(self.layout); target = store.create(['etc/paopaogw/files/ppgw.ini'])
        current = self.original + b'max_rec=11\n'; self.ini.write_bytes(current)
        self.system.stop_all = lambda: None
        self.system.restore_services = lambda states: (_ for _ in ()).throw(Killed())
        with self.assertRaises(Killed): restore_backup(self.layout, self.system, target)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.system.restore_services = lambda states: None
        self.manager.recover()
        self.assertEqual(self.ini.read_bytes(), current)

    def test_full_restore_removes_new_ppsub_but_partial_restore_keeps_it(self):
        from manage import restore_backup
        from manage_backup import BackupStore
        self.system.stop_all = lambda: None
        self.system.wait = lambda *args: None
        store = BackupStore(self.layout)
        partial = store.create(['etc/paopaogw/files/ppgw.ini'])
        full = store.create()
        local = self.layout.path('etc/paopaogw/files/ppsub.json'); local.write_text('{"new":true}')
        restore_backup(self.layout, self.system, partial)
        self.assertTrue(local.exists())
        restore_backup(self.layout, self.system, full)
        self.assertFalse(local.exists())

    def test_generated_bytecode_is_not_a_program_edit_or_snapshot_input(self):
        from manage_backup import BackupStore
        store = BackupStore(self.layout)
        cache = self.layout.path('usr/local/lib/paopaogw/__pycache__/manage_common.cpython-311.pyc')
        cache.parent.mkdir(parents=True); cache.write_bytes(b'generated cache')
        self.assertNotIn(cache.relative_to(self.layout.root).as_posix(), store.paths())
        identifier = store.create(['etc/paopaogw/files/ppgw.ini'])
        store.restore(identifier, expected={cache.relative_to(self.layout.root).as_posix(): ['old-cache-digest']})
        self.assertEqual(self.ini.read_bytes(), self.original)


if __name__ == '__main__': unittest.main()
