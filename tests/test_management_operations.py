import json
from pathlib import Path
import sys
import tempfile
import unittest
import time
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout
from manage_operations import Operation, history, record, restore_operation, remember_loaded, summary
from manage_operations import begin_mihomo, mihomo_current, public, lifecycle_lock
from manage_common import ManagementError


class OperationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)

    def test_restart_retains_task_identity_without_claiming_failure_or_replaying(self):
        task = Operation(self.layout, {'id': 'refresh_fixture_001', 'kind': 'refresh_nodes', 'status': 'applying'})
        task.update(stage='verifying')
        restored = restore_operation(Layout(self.temp.name))
        self.assertEqual(restored['id'], task['id'])
        self.assertEqual(restored['status'], 'interrupted')
        self.assertEqual(history(self.layout)[0]['stage'], 'interrupted')
        restored.update(status='applied')
        previous = history(self.layout)[0]['updated_at']
        self.assertEqual(restore_operation(self.layout)['status'], 'applied')
        self.assertEqual(history(self.layout)[0]['updated_at'], previous)

    def test_records_drop_request_data_and_keep_bounded_history(self):
        for number in range(105):
            record(self.layout, 'settings', {'id': str(number), 'status': 'succeeded',
                   'patch': {'password': 'private-password'}, 'result': {'url': 'https://example.com/private'}})
        rows = history(self.layout)
        self.assertEqual(len(rows), 20)
        paths = list(self.layout.path('var/lib/paopaogw/admin/operations').glob('*.json'))
        self.assertEqual(len(paths), 100)
        self.assertTrue(all('private' not in p.read_text() for p in paths))

    def test_loaded_receipt_requires_saved_revision_and_same_runtime_identity(self):
        runtime = {'boot_id': 'fixture-boot', 'config_revision': 'core-hash'}
        remember_loaded(self.layout, 'settings-hash', runtime)
        self.assertEqual(summary(self.layout, 'settings-hash', runtime)['state'], 'loaded')
        self.assertEqual(summary(self.layout, 'new-settings', runtime)['state'], 'saved_pending')
        self.assertEqual(summary(self.layout, 'settings-hash', {**runtime, 'boot_id': 'new'})['state'], 'unknown')
        self.assertEqual(summary(self.layout, 'settings-hash', runtime, True)['state'], 'recovery_required')

    def test_dead_mihomo_keeps_old_identity_and_next_execution_uses_new_id(self):
        state = self.layout.path('var/lib/paopaogw/mihomo'); state.mkdir(parents=True)
        previous = {'id':'dead_task', 'kind':'update', 'status':'running', 'execution':{'pid':99999}}
        (state/'operation.json').write_text(json.dumps(previous))
        record(self.layout, 'mihomo', previous)
        with patch('manage_operations.alive', return_value=False):
            value = begin_mihomo(self.layout, state, 'check')
        self.assertNotEqual(value['id'], previous['id'])
        old = next(row for row in history(self.layout) if row['id']=='dead_task')
        self.assertEqual((old['status'], old['result'], old['recovery']), ('interrupted','unknown','check_required'))
        self.assertEqual(old['kind'], 'update')

    def test_queued_claim_requires_exact_action_unit_and_new_invocation(self):
        state = self.layout.path('var/lib/paopaogw/mihomo'); state.mkdir(parents=True)
        queued = {'id':'queued_task', 'kind':'check', 'status':'queued', 'created_at':time.time(),
                  'queued_invocation':'old', 'execution':{'service':'paopaogw-core-action@check.service'}}
        (state/'operation.json').write_text(json.dumps(queued))
        with patch.dict('os.environ', {'INVOCATION_ID':'new'}), \
             patch('manage_operations.service_state',return_value={'ActiveState':'activating','InvocationID':'new'}):
            with self.assertRaisesRegex(ManagementError, 'MANAGEMENT_BUSY'):
                begin_mihomo(self.layout, state, 'update')
            value = begin_mihomo(self.layout, state, 'check')
        self.assertEqual(value['id'], queued['id'])
        self.assertEqual(value['execution']['invocation'], 'new')

    def test_queued_window_not_abandoned_and_expired_inactive_queue_is_interrupted(self):
        state = self.layout.path('var/lib/paopaogw/mihomo'); state.mkdir(parents=True)
        value = {'id':'queued_task', 'status':'queued', 'created_at':time.time(),
                 'execution':{'service':'paopaogw-core-action@check.service'}}
        (state/'operation.json').write_text(json.dumps(value))
        with patch('manage_operations.service_state', return_value={'ActiveState':'inactive'}):
            with lifecycle_lock(self.layout): self.assertEqual(mihomo_current(self.layout,state)['status'],'queued')
            value['created_at'] -= 61
            (state/'operation.json').write_text(json.dumps(value))
            with lifecycle_lock(self.layout): self.assertEqual(mihomo_current(self.layout,state)['status'],'interrupted')

    def test_foreign_invocation_cannot_claim_queued_action(self):
        state = self.layout.path('var/lib/paopaogw/mihomo'); state.mkdir(parents=True)
        queued = {'id':'queued_task', 'kind':'check', 'status':'queued', 'created_at':time.time(),
                  'execution':{'service':'paopaogw-core-action@check.service'}}
        (state/'operation.json').write_text(json.dumps(queued))
        with patch.dict('os.environ', {'INVOCATION_ID':'foreign'}), \
             patch('manage_operations.service_state',return_value={'ActiveState':'activating','InvocationID':'expected'}):
            with self.assertRaisesRegex(ManagementError,'MANAGEMENT_BUSY'):
                begin_mihomo(self.layout,state,'check')
        self.assertEqual(json.loads((state/'operation.json').read_bytes())['status'],'queued')

    def test_stale_active_history_is_bounded_and_does_not_include_private_result(self):
        for number in range(105):
            record(self.layout,'mihomo',{'id':str(number),'status':'running','created_at':0})
        self.assertEqual(len(list(self.layout.path('var/lib/paopaogw/admin/operations').glob('*.json'))),100)
        self.assertEqual(public({'status':'failed','stage':'interrupted','result':{'secret':'value'}})['result'],'unknown')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux process identity')
    def test_real_terminated_execution_is_interrupted_and_following_process_does_not_reuse_id(self):
        from manage_operations import finish_mihomo
        import os
        state=self.layout.path('var/lib/paopaogw/mihomo');state.mkdir(parents=True)
        code='''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import maintenance
from manage_common import Layout
layout=Layout(sys.argv[2]);maintenance.STATE=Path(sys.argv[3])
class SyntheticUpdater:
 def check(self): time.sleep(30)
maintenance.Updater=SyntheticUpdater
sys.argv=["maintenance.py","check"]
maintenance.main(layout)
'''
        env=dict(os.environ);env.pop('INVOCATION_ID',None)
        previous=None
        for signal in ('terminate','kill'):
            process=subprocess.Popen([sys.executable,'-c',code,str(Path(__file__).resolve().parents[1]/'integration'),
                                      str(self.layout.root),str(state)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    try:
                        current=json.loads((state/'operation.json').read_bytes())
                        if current.get('status')=='running' and current['id']!=previous:break
                    except (OSError,ValueError):pass
                    if process.poll() is not None:raise AssertionError(process.communicate())
                    time.sleep(.02)
                else:raise AssertionError('child did not persist execution identity')
                getattr(process,signal)();process.communicate(timeout=5)
                with lifecycle_lock(self.layout):dead=mihomo_current(self.layout,state)
                self.assertEqual(dead['id'],current['id']);self.assertEqual(dead['status'],'interrupted')
                next_task=begin_mihomo(self.layout,state,'rollback')
                self.assertNotEqual(next_task['id'],dead['id'])
                finish_mihomo(self.layout,state,next_task,status='complete')
                preserved=next(row for row in history(self.layout) if row['id']==dead['id'])
                self.assertEqual((preserved['kind'],preserved['result']),('check','unknown'))
                previous=next_task['id']
            finally:
                if process.poll() is None:process.kill();process.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
