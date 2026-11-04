import json
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integration'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import subscription_admin as source
import unified_editor as editor
from manage_common import Layout, ManagementError
from manage_model import settings_revision, load_settings
from manage_config import ConfigManager
from manage_web import SettingsAdmin
from clash_policy import merge_policy
from clash_ppsub import source_identity, logical_document
from test_manage_config import FakeSystem
import test_clash_ppsub


class UnifiedEditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.layout=Layout(self.temporary.name)
        self.files=self.layout.path('etc/paopaogw/files'); self.files.mkdir(parents=True)
        self.ini=self.files/'ppgw.ini'; self.ini.write_text('#paopao-gateway\nmode=suburl\nsuburl=https://example.com/config\nsubtime=1d\nfast_node=no\nclash_web_password=private-password-long\n')
        self.original=self.ini.read_bytes()
        self.doc,self.baseline,self.submitted=test_clash_ppsub.ClashPpsubTests().editing()
        self.system=FakeSystem(); self.system.validate_policy=lambda document: None
        self.admin=source.Admin()
        self.patches=[patch.object(source,'CONFIG',self.ini),patch.object(source,'FILES',self.files),
                      patch.object(source,'STATE',self.layout.path('var/lib/paopaogw/admin')),
                      patch.object(source,'running_document',side_effect=lambda:deepcopy(self.doc)),
                      patch.object(source,'config_values',side_effect=lambda:load_settings(self.layout)['ini']),
                      patch.object(editor,'System',return_value=self.system)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
    def request(self):
        result=self.admin.current_config()
        value=deepcopy(result['config']); value['node-groups'][0]['name']='Renamed'
        return {'kind':'editor','edit_kind':'clash_patch','value':json.dumps(value),
                'base_revision':result['base_revision'],'settings_revision':result['settings_revision'],
                'snapshot_id':result['snapshot_id'],'settings_patch':{'ini':{'subtime':'9h'}},'operation_id':'test-editor-001'}
    def test_preview_has_no_writes_or_backup_and_combines_both_drafts(self):
        body=self.request(); value=editor.handle(self.admin,{**body,'dry_run':True})
        self.assertEqual(value['action'],'restart');self.assertEqual(value['changes']['ini']['subtime'],'9h')
        self.assertTrue(value['editor_changed']);self.assertEqual(value['save_target'],'local_policy')
        self.assertEqual(self.ini.read_bytes(),self.original);self.assertFalse((self.files/'clash-policy.json').exists())
        self.assertFalse(self.layout.path('var/backups/paopaogw').exists())
    def test_combined_failure_restores_ini_policy_and_records_one_backup(self):
        body=self.request();self.system.fail=True
        operation={'id':body['operation_id'],'status':'applying'};self.admin.operation=operation
        editor.worker(self.admin,body,operation)
        self.assertEqual(operation['status'],'failed');self.assertIn('恢复',operation['message'])
        self.assertEqual(self.ini.read_bytes(),self.original);self.assertFalse((self.files/'clash-policy.json').exists())
        manager=ConfigManager(self.layout,self.system)
        self.assertEqual(manager.journal()['phase'],'rolled_back')
        self.assertEqual(set(manager.backups.index(manager.journal()['backup'])['files']),
                         {'etc/paopaogw/files/ppgw.ini','etc/paopaogw/files/clash-policy.json'})
    def test_success_saves_schedule_and_policy_and_verifies_loaded_document(self):
        body=self.request()
        def apply(*args):
            policy=json.loads((self.files/'clash-policy.json').read_bytes())
            self.doc=logical_document(merge_policy(self.doc,policy,source_identity(load_settings(self.layout)['ini'])))
        self.system.apply=apply
        operation={'id':body['operation_id'],'status':'applying'};self.admin.operation=operation
        editor.worker(self.admin,body,operation)
        self.assertEqual(operation['status'],'applied');self.assertEqual(load_settings(self.layout)['ini']['subtime'],'9h')
        self.assertEqual(self.doc['proxy-groups'][0]['name'],'Renamed')
        self.assertEqual(self.doc['proxies'][0]['password'],'private-node')
        self.assertEqual(ConfigManager(self.layout,self.system).journal()['phase'],'complete')
    def test_stale_settings_and_main_source_change_are_rejected_before_write(self):
        body=self.request();body['settings_revision']='0'*64
        with self.assertRaisesRegex(ManagementError,'CONFIG_REVISION_CONFLICT'):editor.handle(self.admin,body)
        body=self.request();body['settings_patch']={'ini':{'mode':'yaml'}}
        with self.assertRaisesRegex(ManagementError,'INVALID_EDITOR_REQUEST'):editor.handle(self.admin,body)
        body=self.request();value=json.loads(body['value']);value['subs'].append({'name':'Unsupported','url':'https://example.com/changed'});body['value']=json.dumps(value)
        with self.assertRaisesRegex(ValueError,'CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED'):editor.handle(self.admin,body)
    def test_ppsub_and_schedule_rollback_together(self):
        self.ini.write_text(self.original.decode().replace('https://example.com/config','ppsub@http://127.0.0.1/ppsub.json'))
        old={'exported_at':'old','subs':[{'name':'Original','url':'https://example.com/old'}],'node-groups':[],'rules':[]}
        local=self.files/'ppsub.json';local.write_text(json.dumps(old));previous=local.read_bytes();original=self.ini.read_bytes()
        result=self.admin.current_config();new=deepcopy(old);new['subs'][0]['name']='Edited'
        body={'kind':'editor','edit_kind':'ppsub','value':json.dumps(new),'base_revision':result['base_revision'],
              'settings_revision':result['settings_revision'],'settings_patch':{'ini':{'subtime':'6h'}},'operation_id':'native-editor-001'}
        self.system.fail=True;operation={'status':'applying'}
        editor.worker(self.admin,body,operation)
        self.assertEqual(operation['status'],'failed');self.assertEqual(self.ini.read_bytes(),original);self.assertEqual(local.read_bytes(),previous)
    def test_ppsub_source_import_uses_existing_config_transaction(self):
        (self.files/'clash-policy.json').write_text('{}')
        admin=SettingsAdmin(self.layout,self.system,start_job=lambda callback:None)
        value={'exported_at':'now','subs':[{'name':'S','url':'https://example.com/sub'}]}
        body={'base_revision':settings_revision(self.layout),'patch':{'ini':{'mode':'suburl','suburl':'ppsub@http://127.0.0.1/ppsub.json'}},'source_ppsub':json.dumps(value)}
        with self.assertRaisesRegex(ManagementError,'SOURCE_CONFLICT'):admin.preview(body)
        body['reset_source']=True
        self.assertTrue(admin.preview(body)['effects']['source_reset'])
        self.assertFalse((self.files/'ppsub.json').exists())
    def test_export_yaml_is_complete_and_snapshot_checked(self):
        result=self.admin.current_config()
        value=editor.handle(self.admin,{'kind':'export_yaml','snapshot_id':result['snapshot_id'],'base_revision':result['base_revision']})
        import yaml
        doc=yaml.safe_load(value['content']);self.assertEqual(doc['dns'],self.doc['dns']);self.assertEqual(doc['proxies'][0]['password'],'private-node')
        self.doc['rules'].append('DOMAIN,changed.example,DIRECT')
        with self.assertRaisesRegex(ManagementError,'CONFIG_CHANGED'):
            editor.handle(self.admin,{'kind':'export_yaml','snapshot_id':result['snapshot_id'],'base_revision':result['base_revision']})

if __name__=='__main__':unittest.main()
