"""Public preview contracts must explain edits without copying private values."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage_common import Layout
from manage_model import settings_revision
from manage_preview import (native_summary, clash_summary, source_details,
                            complete_preview, named_changes)
from manage_web import SettingsAdmin
from clash_ppsub import source_identity
from test_manage_config import FakeSystem


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.layout = Layout(self.temporary.name)
        self.files = self.layout.path('etc/paopaogw/files'); self.files.mkdir(parents=True)
        (self.files / 'ppgw.ini').write_text('#paopao-gateway\nmode=suburl\nsuburl=https://example.com/?token=old-private\nclash_web_password=long-private-password\n')
        self.system = FakeSystem()

    def test_native_no_change_ignores_export_time_and_bounds_names(self):
        old = {'exported_at': 'old', 'subs': [{'name': 'S', 'url': 'https://example.com/secret'}],
               'node-groups': [], 'rules': []}
        new = deepcopy(old); new['exported_at'] = 'new'
        self.assertFalse(native_summary(old, new)['changed'])
        new['node-groups'] = [{'name': 'G' + str(index)} for index in range(55)]
        summary = native_summary(old, new)
        self.assertEqual(summary['node_groups']['counts']['added'], 55)
        self.assertEqual(len(summary['node_groups']['names']['added']), 20)
        self.assertTrue(summary['node_groups']['names_truncated'])

    def test_native_url_password_and_rules_never_copy_content(self):
        old = {'subs': [{'name': 'Provider', 'url': 'https://example.com/?key=old-secret'}],
               'node-groups': [], 'rules': [{'fixrule': ['DOMAIN,https://private.example/?token=raw-old,DIRECT']}],
               'shadowsocks_in': {'enable': True, 'password': 'old-password', 'cipher': 'aes-128-gcm'}}
        new = deepcopy(old)
        new['subs'][0]['url'] = 'https://example.com/?key=new-secret'
        new['shadowsocks_in']['password'] = 'new-password'
        new['rules'][0]['fixrule'] = ['DOMAIN,https://private.example/?token=raw-new,DIRECT']
        summary = native_summary(old, new)
        self.assertEqual(summary['subscriptions']['counts']['modified'], 1)
        self.assertEqual(summary['features']['changed_keys'], ['shadowsocks_in'])
        self.assertEqual(summary['rules']['inline_rules']['counts']['added'], 1)
        self.assertEqual(summary['rules']['inline_rules']['counts']['removed'], 1)
        self.assertEqual(summary['rules']['inline_rules']['types']['after'], {'DOMAIN': 1})
        public = json.dumps(summary)
        for secret in ('old-secret', 'new-secret', 'old-password', 'new-password', 'raw-old', 'raw-new', 'private.example'):
            self.assertNotIn(secret, public)

    def test_renames_require_identity_and_relative_order_is_explicit(self):
        old = [{'name': 'A'}, {'name': 'B'}]
        changed = named_changes(old, [{'name': 'B'}, {'name': 'C'}])
        self.assertEqual(changed['counts']['added'], 1)
        self.assertEqual(changed['counts']['removed'], 1)
        self.assertEqual(changed['renamed'], [])
        identified = named_changes(old, [{'name': 'B'}, {'name': 'C'}], {'A': 'C'})
        self.assertTrue(identified['order_changed'])
        self.assertEqual(identified['counts']['modified'], 1)
        self.assertEqual(identified['renamed'], [{'before': 'A', 'after': 'C'}])
        self.assertFalse(named_changes(old, [{'name': 'X'}, *old])['order_changed'])
        old_rules = {'rules': [{'priority': 10, 'fixrule': ['MATCH,DIRECT']},
                               {'priority': 20, 'fixrule': ['DOMAIN,example.com,DIRECT']}]}
        new_rules = deepcopy(old_rules); new_rules['rules'][0]['priority'] = 30
        rules = native_summary(old_rules, new_rules)['rules']
        self.assertTrue(rules['priority_changed']); self.assertTrue(rules['order_changed'])

    def test_clash_rules_reorder_replacement_and_hidden_fields(self):
        before = {'proxy-groups': [{'name': 'Main', 'hidden': True, 'icon': 'https://private.example/icon',
                                   'proxies': ['N', 'DIRECT']}],
                  'proxies': [{'name': 'N', 'password': 'hidden-node-secret'}],
                  'rules': ['DOMAIN,private.example,Main', 'MATCH,DIRECT']}
        after = deepcopy(before); after['rules'].reverse()
        summary = clash_summary(before, after, {}, {})
        self.assertTrue(summary['rules']['order_changed'])
        self.assertEqual(summary['rules']['counts']['added'], 0)
        self.assertFalse(summary['node_groups']['changed'])
        after['rules'][0] = 'https://raw-secret.example/?token=unknown'
        summary = clash_summary(before, after, {}, {})
        self.assertEqual(summary['rules']['types']['after'], {'DOMAIN': 1, 'OTHER': 1})
        self.assertEqual(summary['rules']['counts']['added'], 1)
        public = json.dumps(summary)
        for secret in ('hidden-node-secret', 'raw-secret', 'private.example'):
            self.assertNotIn(secret, public)

    def test_source_kinds_and_section_override_are_safe_and_identity_bound(self):
        values = {'mode': 'suburl', 'suburl': 'https://example.com/?token=private'}
        policy = {'schema': 1, 'source_identity': source_identity(values),
                  'sections': {'rules': ['MATCH,DIRECT'], 'proxy-groups': []}, 'features': {}}
        details = source_details(self.layout, values, policy=policy)
        self.assertEqual(details['kind'], 'remote_yaml')
        self.assertTrue(details['local_policy']['applies'])
        self.assertEqual(details['local_policy']['sections'], ['proxy-groups', 'rules'])
        self.assertNotIn('private', json.dumps(details))
        local = {**values, 'mode': 'yaml', 'yamlfile': 'my.yaml'}
        details = source_details(self.layout, local, policy=policy)
        self.assertEqual(details['kind'], 'local_yaml')
        self.assertFalse(details['local_policy']['applies'])
        self.assertEqual(details['file'], 'my.yaml')
        self.assertEqual(source_details(self.layout, {'mode': 'suburl', 'suburl': 'ppsub@https://example.com/private'})['kind'], 'native_ppsub')
        details = source_details(self.layout, values, policy={**policy, 'features': ['invalid']})
        self.assertEqual(details['local_policy']['state'], 'invalid')
        self.assertFalse(details['local_policy']['applies'])
        native = {'mode': 'suburl', 'suburl': 'ppsub@https://example.com/native-private'}
        native_policy = {**policy, 'source_identity': source_identity(native)}
        details = source_details(self.layout, native, policy=native_policy)
        self.assertEqual(details['kind'], 'native_ppsub')
        self.assertTrue(details['local_policy']['applies'])
        self.assertEqual(details['local_policy']['sections'], ['proxy-groups', 'rules'])
        details = source_details(self.layout, {**native, 'suburl': 'ppsub@https://example.com/changed'}, policy=native_policy)
        self.assertFalse(details['local_policy']['applies'])
        self.assertEqual(details['local_policy']['state'], 'source_mismatch')

    def test_impact_follows_existing_action_and_auxiliary_effects(self):
        with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True):
            preview = complete_preview({'action': 'reload', 'changes': {'ini': {'clash_web_password': 'never-show'}}}, self.layout, self.system)
            self.assertEqual(preview['application']['action'], 'reload')
            self.assertFalse(preview['application']['pending_load'])
            self.assertTrue(preview['application']['reload_fallback_possible'])
            self.system.states['paopaogw']['active'] = False
            preview = complete_preview({'action': 'restart', 'changes': {}}, self.layout, self.system)
            self.assertTrue(preview['application']['pending_load'])
            self.assertFalse(preview['application']['core_active'])
            preview = complete_preview({'action': 'none', 'changes': {'maintenance': {'auto_update': False}}}, self.layout, self.system)
            self.assertEqual(preview['application']['action'], 'auxiliary')
            self.assertEqual(preview['application']['services'], ['maintenance_timer'])
        self.assertNotIn('never-show', json.dumps(preview))

    def test_settings_password_url_only_and_noop_have_safe_summary(self):
        admin = SettingsAdmin(self.layout, self.system, start_job=lambda callback: None)
        def preview(patch):
            return admin.preview({'base_revision': settings_revision(self.layout), 'patch': patch})
        value = preview({'ini': {'clash_web_password': 'changed-private-password'}})
        self.assertEqual(value['change_summary']['settings']['changed_keys'], ['ini.clash_web_password'])
        self.assertEqual(value['action'], 'reload')
        self.assertNotIn('changed-private-password', json.dumps(value))
        value = preview({'ini': {'test_node_url': 'https://example.com/?token=url-private'}})
        self.assertTrue(value['change_summary']['changed'])
        self.assertNotIn('url-private', json.dumps(value))
        value = preview({})
        self.assertFalse(value['change_summary']['changed'])
        self.assertEqual(value['application']['action'], 'none')
        self.assertFalse(self.layout.path('var/backups/paopaogw').exists())

    def test_auxiliary_impact_matches_disabled_active_and_unknown_service_branches(self):
        admin = SettingsAdmin(self.layout, self.system, start_job=lambda callback: None)
        self.system.states.update({name: {'active': False} for name in (
            'paopaogw-api', 'paopaogw-observe', 'paopaogw-subscription')})
        def preview(patch):
            return admin.preview({'base_revision': settings_revision(self.layout), 'patch': patch})
        with patch.object(Layout, 'live', new_callable=PropertyMock, return_value=True):
            retention = preview({'maintenance': {'history_retention_days': 60}})
            self.assertEqual(retention['application']['action'], 'none')
            self.assertEqual(retention['application']['services'], [])
            # Legacy coarse effects remain compatible; the new application
            # contract reflects the runtime branch used for this request.
            self.assertEqual(retention['effects']['services'], ['history'])
            disabled_api = preview({'api': {'name': 'Edited'}})
            self.assertEqual(disabled_api['application']['action'], 'none')
            self.assertEqual(disabled_api['application']['services'], [])
            self.system.states['paopaogw-api']['active'] = True
            active_api = preview({'api': {'name': 'Edited'}})
            self.assertEqual(active_api['application']['services'], ['api'])
            self.assertEqual(active_api['application']['action'], 'auxiliary')
            enabled = preview({'api': {'enabled': True}, 'maintenance': {'history_retention_days': 60}})
            self.assertEqual(enabled['application']['services'], ['api', 'history'])
            self.layout.path('etc/paopaogw/api.json').write_text(json.dumps({'enabled': True}))
            self.system.states['paopaogw-api']['active'] = False
            enabled_inactive = preview({'api': {'name': 'Edited'}})
            self.assertEqual(enabled_inactive['application']['services'], [])
            self.system.states['paopaogw-observe']['active'] = True
            enabled_retention = preview({'maintenance': {'history_retention_days': 60}})
            self.assertEqual(enabled_retention['application']['services'], ['history'])
            disabled_history = preview({'maintenance': {'history_enabled': False}})
            self.assertEqual(disabled_history['application']['services'], ['history'])
            self.system.states['paopaogw-subscription']['active'] = True
            self.system.states['paopaogw-observe']['active'] = False
            timezone = preview({'service': {'TZ': 'UTC'}})
            self.assertEqual(timezone['application']['services'], ['subscription'])
            with patch.object(self.system, 'status', side_effect=OSError('unavailable')):
                unknown = preview({'api': {'name': 'Edited'}})['application']
            self.assertIsNone(unknown['core_active'])
            self.assertEqual(unknown['runtime_state'], 'unknown')
            self.assertTrue(unknown['services_uncertain'])
            self.assertEqual(unknown['services'], [])
            self.assertEqual(unknown['possible_services'], ['api'])
            self.assertFalse(unknown['pending_load'])


if __name__ == '__main__': unittest.main()
