from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError
from manage_model import settings_revision, load_settings
from manage_policy import PolicyManager, merge_native
from clash_policy import merge_policy
from clash_ppsub import source_identity


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name)
        ini = self.layout.path('etc/paopaogw/files/ppgw.ini'); ini.parent.mkdir(parents=True)
        ini.write_text('#paopao-gateway\nmode=suburl\nsuburl=https://example.com/config\nclash_web_password=private-password-long\n')
        self.document = {
            'proxies': [{'name': 'N', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9, 'password': 'node-secret'}],
            'secret': 'core-secret', 'dns': {'nameserver': ['1.1.1.1']},
            'proxy-groups': [{'name': 'Main', 'type': 'select', 'proxies': ['Backup', 'DIRECT'], 'hidden': True},
                             {'name': 'Backup', 'type': 'fallback', 'proxies': ['N', 'N'], 'url': 'https://example.com/check', 'interval': 120, 'lazy': True}],
            'rule-providers': {'rules': {'type': 'http', 'url': 'https://example.com/rules.yaml', 'behavior': 'classical', 'format': 'yaml', 'path': './r.yaml', 'header': {'X-Synthetic': ['retained']}}},
            'rules': ['RULE-SET,rules,Main', 'DOMAIN-KEYWORD,foo#bar,DIRECT', 'MATCH,Main'],
        }
        self.loaded = deepcopy(self.document); self.before = deepcopy(self.document)
        owner = self
        class System:
            def __init__(self): self.actions = []; self.fail = False; self.validations = []
            def status(self): return {'paopaogw': {'active': True, 'enabled': True}}
            def preflight(self, settings): pass
            def validate_policy(self, document): self.validations.append(deepcopy(document))
            def apply(self, action, settings, changes, before):
                self.actions.append(action)
                path = owner.layout.path('etc/paopaogw/files/clash-policy.json')
                policy = json.loads(path.read_bytes()) if path.exists() else None
                owner.loaded = merge_policy(owner.document, policy, source_identity(load_settings(owner.layout)['ini'])) if policy else deepcopy(owner.document)
                if self.fail: raise ManagementError('HEALTH_FAILED')
                return {'status': 'running'}
            def restore_services(self, states): owner.loaded = deepcopy(owner.before)
        self.system = System()
        self.manager = PolicyManager(self.layout, self.system, lambda: deepcopy(self.loaded))

    def request(self):
        value = self.manager.show()
        self.assertNotIn('node-secret', json.dumps(value)); self.assertNotIn('core-secret', json.dumps(value))
        return {'snapshot_id': value['snapshot_id'], 'base_revision': value['base_revision'],
                'policy': {key: value[key] for key in ('groups', 'providers', 'rules')}}

    def test_noop_and_preview_preserve_hidden_fields_and_credentials(self):
        request = self.request()
        result, _ = merge_native(self.document, request['policy'])
        self.assertEqual(result, self.document)
        self.assertEqual(self.manager.apply(request)['status'], 'unchanged')
        request['policy']['groups'][0]['name'] = 'Renamed'
        preview = self.manager.preview(request)
        self.assertEqual(preview['action'], 'restart')
        self.assertFalse(self.layout.path('etc/paopaogw/files/clash-policy.json').exists())
        self.assertEqual(self.system.actions, [])

    def test_native_group_and_provider_renames_preserve_full_source(self):
        request = self.request()
        request['policy']['groups'][0]['name'] = 'Renamed'
        request['policy']['groups'][1]['proxies'].append('Main')
        request['policy']['providers'][0]['name'] = 'new-rules'
        self.manager.apply(request)
        self.assertEqual(self.system.actions, ['restart'])
        self.assertEqual(self.loaded['proxy-groups'][0]['name'], 'Renamed')
        self.assertTrue(self.loaded['proxy-groups'][0]['hidden'])
        self.assertEqual(self.loaded['proxy-groups'][1]['proxies'], ['N', 'N', 'Renamed'])
        self.assertEqual(self.loaded['rules'][0], 'RULE-SET,new-rules,Renamed')
        self.assertEqual(self.loaded['rules'][-1], 'MATCH,Renamed')
        self.assertEqual(self.loaded['proxies'], self.document['proxies'])
        self.assertEqual(self.loaded['dns'], self.document['dns'])
        self.assertEqual(self.loaded['rule-providers']['new-rules']['header'], {'X-Synthetic': ['retained']})
        self.assertEqual(self.loaded['rule-providers']['new-rules']['path'], './r.yaml')

    def test_source_change_and_runtime_refresh_reject_stale_edits(self):
        request = self.request()
        self.loaded['rules'].append('DOMAIN,another.example,DIRECT')
        with self.assertRaisesRegex(ManagementError, 'STALE_POLICY_SNAPSHOT'): self.manager.preview(request)
        request = self.request()
        ini = self.layout.path('etc/paopaogw/files/ppgw.ini'); ini.write_text(ini.read_text() + 'dns_port=5353\n')
        with self.assertRaisesRegex(ManagementError, 'CONFIG_REVISION_CONFLICT'): self.manager.apply(request)

    def test_failed_runtime_restores_policy_and_durable_journal(self):
        request = self.request(); request['policy']['rules'].insert(0, 'DOMAIN,test.example,DIRECT')
        self.system.fail = True
        with self.assertRaisesRegex(ManagementError, 'APPLY_FAILED_ROLLED_BACK'): self.manager.apply(request)
        self.assertFalse(self.layout.path('etc/paopaogw/files/clash-policy.json').exists())
        self.assertEqual(self.loaded, self.document)
        journal = json.loads(self.layout.path('var/lib/paopaogw/admin/transaction.json').read_bytes())
        self.assertEqual(journal['phase'], 'rolled_back')

    def test_reset_is_revision_checked_and_transactional(self):
        request = self.request(); request['policy']['rules'].insert(0, 'DOMAIN,test.example,DIRECT')
        self.manager.apply(request)
        with self.assertRaisesRegex(ManagementError, 'CONFIG_REVISION_CONFLICT'):
            self.manager.reset({'base_revision': request['base_revision']})
        self.manager.reset({'base_revision': settings_revision(self.layout)})
        self.assertFalse(self.layout.path('etc/paopaogw/files/clash-policy.json').exists())
        self.assertEqual(self.loaded, self.document)

    def test_duplicate_ids_and_arbitrary_fields_are_rejected(self):
        request = self.request(); request['policy']['groups'].append(deepcopy(request['policy']['groups'][0]))
        with self.assertRaises(ManagementError): self.manager.preview(request)
        request = self.request(); request['policy']['providers'][0]['path'] = '/etc/secret'
        with self.assertRaises(ManagementError): self.manager.preview(request)


if __name__ == '__main__': unittest.main()
