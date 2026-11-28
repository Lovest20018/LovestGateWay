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
from test_manage_config import FakeSystem


class UnifiedEditorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.layout = Layout(temporary.name)
        self.files = self.layout.path('etc/paopaogw/files')
        self.files.mkdir(parents=True)
        self.ini = self.files / 'ppgw.ini'
        self.native_url = 'ppsub@http://127.0.0.1/ppsub.json'
        self.original = (f'#paopao-gateway\nmode=suburl\nsuburl={self.native_url}\nsubtime=1d\n'
                         'fast_node=no\nclash_web_password=private-password-long\n').encode()
        self.ini.write_bytes(self.original)
        self.local = self.files / 'ppsub.json'
        self.saved = {
            'exported_at': 'old', 'subs': [{'name': 'Original', 'url': 'https://example.com/old'}],
            'node-groups': [{'name': 'Main'}], 'custom': {'password': 'private-node-option'},
            'rules': [{'type': 'rule-set', 'name': 'ads', 'url': 'https://example.com/rules'},
                      {'fixrule': ['MATCH,DIRECT']}],
        }
        self.local.write_text(json.dumps(self.saved), encoding='utf-8')
        self.previous = self.local.read_bytes()
        self.system = FakeSystem()
        self.admin = source.Admin(self.layout)
        for item in (
            patch.object(source, 'CONFIG', self.ini),
            patch.object(source, 'FILES', self.files),
            patch.object(source, 'STATE', self.layout.path('var/lib/paopaogw/admin')),
            patch.object(source, 'config_values', side_effect=lambda: load_settings(self.layout)['ini']),
            patch.object(editor, 'System', return_value=self.system),
        ):
            item.start()
            self.addCleanup(item.stop)

    def request(self, config=None):
        result = self.admin.current_config()
        if config is None:
            config = deepcopy(result['config'])
            config['node-groups'][0]['name'] = 'Renamed'
        return {'kind': 'editor', 'edit_kind': 'ppsub', 'value': json.dumps(config),
                'base_revision': result['base_revision'], 'settings_revision': result['settings_revision'],
                'settings_patch': {'ini': {'subtime': '9h'}}, 'operation_id': 'test-editor-001'}

    def test_preview_has_no_writes_or_backup_and_combines_both_drafts(self):
        value = editor.handle(self.admin, {**self.request(), 'dry_run': True})
        self.assertEqual(value['action'], 'restart')
        self.assertEqual(value['changes']['ini']['subtime'], '9h')
        self.assertTrue(value['editor_changed'])
        self.assertEqual(value['save_target'], 'ppsub')
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertEqual(self.local.read_bytes(), self.previous)
        self.assertFalse(self.layout.path('var/backups/paopaogw').exists())

    def test_combined_failure_restores_ini_and_ppsub_and_records_one_backup(self):
        body = self.request()
        self.system.fail = True
        operation = {'id': body['operation_id'], 'status': 'applying'}
        editor.worker(self.admin, body, operation)
        self.assertEqual(operation['status'], 'failed')
        self.assertIn('恢复', operation['message'])
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertEqual(self.local.read_bytes(), self.previous)
        manager = ConfigManager(self.layout, self.system)
        self.assertEqual(manager.journal()['phase'], 'rolled_back')
        self.assertEqual(set(manager.backups.index(manager.journal()['backup'])['files']),
                         {'etc/paopaogw/files/ppgw.ini', 'etc/paopaogw/files/ppsub.json'})

    def test_success_saves_schedule_and_ppsub_and_verifies_core(self):
        body = self.request()
        operation = {'id': body['operation_id'], 'status': 'applying'}
        with patch.object(self.admin.core, 'request', return_value={'ppsub': True}) as runtime:
            editor.worker(self.admin, body, operation)
        runtime.assert_called_once_with('/sniffbox/info')
        self.assertEqual(operation['status'], 'applied')
        self.assertEqual(load_settings(self.layout)['ini']['subtime'], '9h')
        saved = json.loads(self.local.read_bytes())
        self.assertEqual(saved, json.loads(body['value']))
        self.assertEqual(saved['node-groups'][0]['name'], 'Renamed')
        self.assertEqual(saved['custom'], self.saved['custom'])
        self.assertEqual(ConfigManager(self.layout, self.system).journal()['phase'], 'complete')

    def test_stale_settings_and_source_change_are_rejected_before_write(self):
        body = self.request()
        with self.assertRaisesRegex(ManagementError, 'CONFIG_REVISION_CONFLICT'):
            editor.handle(self.admin, {**body, 'settings_revision': '0' * 64})
        with self.assertRaisesRegex(ManagementError, 'INVALID_EDITOR_REQUEST'):
            editor.handle(self.admin, {**body, 'settings_patch': {'ini': {'mode': 'yaml'}}})
        self.local.unlink()
        self.ini.write_bytes(self.original.replace(self.native_url.encode(), b'https://example.com/legacy'))
        legacy_ini = self.ini.read_bytes()
        legacy_body = self.request(self.saved)
        with self.assertRaisesRegex(ManagementError, 'SOURCE_CHANGE_REQUIRES_SWITCH'):
            editor.handle(self.admin, legacy_body)
        self.assertEqual(self.ini.read_bytes(), legacy_ini)
        self.assertFalse(self.local.exists())
        self.assertEqual(self.system.calls, [])

    def test_remote_ppsub_materializes_locally_and_rolls_back_if_core_does_not_load_it(self):
        self.ini.write_bytes(self.original.replace(self.native_url.encode(), b'ppsub@https://example.com/native'))
        remote_ini = self.ini.read_bytes()
        self.local.unlink()
        runtime = self.layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json')
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(self.previous)
        current = self.admin.current_config()
        self.assertEqual(current['config'], self.saved)
        self.assertEqual(current['edit_kind'], 'ppsub')
        self.assertTrue(current['write_allowed'])
        body = self.request()
        preview = editor.handle(self.admin, {**body, 'dry_run': True})
        self.assertTrue(preview['source_details']['materializes_native'])
        self.assertFalse(self.local.exists())
        self.assertEqual(self.ini.read_bytes(), remote_ini)
        operation = {'id': body['operation_id'], 'status': 'applying'}
        with patch.object(self.admin.core, 'request', return_value={'ppsub': False}):
            editor.worker(self.admin, body, operation)
        self.assertEqual(operation['status'], 'failed')
        self.assertEqual(self.ini.read_bytes(), remote_ini)
        self.assertFalse(self.local.exists())
        with patch.object(self.admin.core, 'request', return_value={'ppsub': True}):
            editor.worker(self.admin, body, operation)
        self.assertEqual(operation['status'], 'applied')
        self.assertEqual(load_settings(self.layout)['ini']['suburl'], self.native_url)
        self.assertEqual(json.loads(self.local.read_bytes()), json.loads(body['value']))

    def test_ppsub_source_import_requires_reset_and_clears_legacy_override(self):
        self.ini.write_bytes(self.original.replace(self.native_url.encode(), b'https://example.com/legacy'))
        self.local.unlink()
        policy = self.files / 'clash-policy.json'
        policy.write_text('{}', encoding='utf-8')
        self.layout.path('etc/paopaogw/revision-key').write_bytes(b'fixture-revision-key')
        jobs = []
        admin = SettingsAdmin(self.layout, self.system, start_job=jobs.append)
        value = {'exported_at': 'now', 'subs': [{'name': 'S', 'url': 'https://example.com/sub'}]}
        body = {'base_revision': settings_revision(self.layout),
                'patch': {'ini': {'mode': 'suburl', 'suburl': self.native_url}}, 'source_ppsub': json.dumps(value)}
        with self.assertRaisesRegex(ManagementError, 'SOURCE_CONFLICT'):
            admin.preview(body)
        body['reset_source'] = True
        self.assertTrue(admin.preview(body)['effects']['source_reset'])
        self.assertFalse(self.local.exists())
        self.assertTrue(policy.exists())
        job_id = 'a' * 32
        admin.submit({'id': job_id, 'kind': 'config', 'payload': body})
        jobs.pop()()
        self.assertEqual(admin.job(job_id)['status'], 'succeeded')
        self.assertFalse(policy.exists())
        self.assertEqual(json.loads(self.local.read_bytes()), value)
        self.assertEqual(load_settings(self.layout)['ini']['suburl'], self.native_url)

    def test_legacy_edit_kind_is_rejected_even_for_an_existing_operation_id(self):
        body = {**self.request(), 'edit_kind': 'clash_patch', 'snapshot_id': 'obsolete'}
        self.admin.operation = {'id': body['operation_id'], 'status': 'applied'}
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(ManagementError, '^CLASH_YAML_EDITING_UNSUPPORTED$'):
                    editor.handle(self.admin, {**body, 'dry_run': dry_run})
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertEqual(self.local.read_bytes(), self.previous)

    def test_ppsub_rule_validation_keeps_the_document_and_reports_the_exact_part(self):
        value = {
            'exported_at': 'fixture', 'subs': [{'name': 'S', 'url': 'https://example.org/sub'}],
            'node-groups': [{'name': 'PROXY'}], 'custom': {'untouched': True},
            'rules': [
                {'type': 'rule-set', 'name': 'ads', 'url': 'https://example.org/ads', 'format': 'auto'},
                {'fixrule': ['# note', 'AND,((RULE-SET,ads),(DOMAIN,example.org)),PROXY',
                             'MATCH,PASS-RULE', 'MATCH,GLOBAL']},
            ],
        }
        self.assertEqual(json.loads(editor.validate_ppsub(value)), value)
        for rule, expected in [('MATCH', 'INVALID_RULE_LINE'), ('DOMAIN,x,Missing', 'UNKNOWN_RULE_TARGET'),
                               ('NOT,((RULE-SET,missing)),DIRECT', 'UNKNOWN_RULE_PROVIDER')]:
            with self.subTest(rule=rule):
                candidate = deepcopy(value)
                candidate['rules'][1]['fixrule'][1] = rule
                with self.assertRaisesRegex(ManagementError, expected) as caught:
                    editor.validate_ppsub(candidate)
                self.assertEqual(editor.editor_error_details(caught.exception), {
                    'error': expected, 'issue': {'section': 'rules', 'index': 1, 'field': 'fixrule', 'line': 1},
                })

        for interval in (-10, 0, 12, 604800):
            with self.subTest(interval=interval):
                candidate = deepcopy(value)
                candidate['rules'][0]['interval'] = interval
                self.assertEqual(json.loads(editor.validate_ppsub(candidate)), candidate)
        for interval in (True, '60', 604801):
            with self.subTest(invalid_interval=interval):
                candidate = deepcopy(value)
                candidate['rules'][0]['interval'] = interval
                with self.assertRaisesRegex(ManagementError, 'INVALID_POLICY_INTERVAL'):
                    editor.validate_ppsub(candidate)

        duplicate = deepcopy(value)
        duplicate['rules'].append(deepcopy(value['rules'][0]))
        with self.assertRaisesRegex(ManagementError, 'DUPLICATE_RULE_PROVIDER'):
            editor.validate_ppsub(duplicate)
        invalid = deepcopy(value)
        invalid['rules'].append(42)
        with self.assertRaisesRegex(ManagementError, 'INVALID_PPSUB_ROW'):
            editor.validate_ppsub(invalid)

    def test_legacy_ppsub_optional_nulls_and_manual_rule_kind_round_trip(self):
        value = {
            'exported_at': 'fixture', 'subs': [{'name': 'S', 'url': 'https://example.org/sub'}],
            'node-groups': [{
                'name': 'Old', 'subs': None, 'include': None, 'keywords': None,
                'exclude_keywords': None, 'speedtest_url': None, 'monitor_url': None,
            }],
            'rules': [
                {'type': 'rules', 'url': 'https://example.org/old', 'fixrule': ['MATCH,Old']},
                {'type': 'rules', 'fixrule': None},
                {'type': 'rule-set', 'name': 'ads', 'url': 'https://example.org/ads',
                 'format': None, 'behavior': None, 'interval': None, 'proxy': None},
                {'type': None, 'priority': None, 'url': None},
            ],
        }
        self.assertEqual(json.loads(editor.validate_ppsub(value)), value)
        for field in ('format', 'behavior', 'interval', 'proxy'):
            with self.subTest(field=field):
                invalid = deepcopy(value)
                invalid['rules'][2][field] = False
                with self.assertRaises(ManagementError):
                    editor.validate_ppsub(invalid)
        invalid = deepcopy(value)
        invalid['rules'][0]['fixrule'] = ['MATCH,Missing']
        with self.assertRaisesRegex(ManagementError, 'UNKNOWN_RULE_TARGET'):
            editor.validate_ppsub(invalid)

    def test_failover_checks_only_active_monitor_fields_and_preserves_the_document(self):
        config = deepcopy(self.saved)
        group = config['node-groups'][0]
        group.update(failover=False, monitor_url='not a URL', retries=True, extra={'kept': True})
        self.assertEqual(json.loads(editor.validate_ppsub(config)), config)
        del group['failover']
        self.assertEqual(json.loads(editor.validate_ppsub(config)), config)
        group['failover'] = True
        for url in ('not a URL', 'ftp://example.com/204', 'https://user:pass@example.com/204'):
            with self.subTest(url=url):
                group['monitor_url'] = url
                with self.assertRaisesRegex(ManagementError, 'INVALID_URL') as error:
                    editor.validate_ppsub(config)
                self.assertEqual(editor.editor_error_details(error.exception)['issue'],
                                 {'section': 'node-groups', 'index': 0, 'field': 'monitor_url'})
        group['monitor_url'] = 'https://example.com/204'
        for retries in (-1, 101, 1.5, '3', True, False):
            with self.subTest(retries=retries):
                group['retries'] = retries
                with self.assertRaisesRegex(ManagementError, 'INVALID_EDITOR_GROUP') as error:
                    editor.validate_ppsub(config)
                self.assertEqual(editor.editor_error_details(error.exception), {
                    'error': 'INVALID_EDITOR_GROUP',
                    'issue': {'section': 'node-groups', 'index': 0, 'field': 'retries'},
                })
        for retries in (None, 0, 100):
            group['retries'] = retries
            self.assertEqual(json.loads(editor.validate_ppsub(config)), config)
        del group['monitor_url'], group['retries']
        self.assertEqual(json.loads(editor.validate_ppsub(config)), config)
        group.update(failover=False, speedtest_url='not a URL')
        with self.assertRaisesRegex(ManagementError, 'INVALID_URL') as error:
            editor.validate_ppsub(config)
        self.assertEqual(editor.editor_error_details(error.exception)['issue']['field'], 'speedtest_url')

    def test_preview_and_save_return_safe_field_errors_over_http(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from threading import Thread
        token = self.files / 'fixture-token'
        token.write_text('a' * 64)
        body = self.request()
        value = json.loads(body['value'])
        value['rules'][0]['format'] = 'zip'
        body['value'] = json.dumps(value)
        with ThreadingHTTPServer(('127.0.0.1', 0), source.Handler) as server:
            server.admin = self.admin
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(source, 'CORE_TOKEN', token):
                    def post(payload):
                        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                        try:
                            connection.request('POST', '/api/subscription', json.dumps(payload),
                                               {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + 'a' * 64,
                                                'Origin': f'http://127.0.0.1:{server.server_port}'})
                            response = connection.getresponse()
                            return response.status, json.loads(response.read())
                        finally:
                            connection.close()
                    for dry_run in (True, False):
                        self.assertEqual(post({**body, 'dry_run': dry_run}), (400, {
                            'error': 'INVALID_RULE_FORMAT',
                            'issue': {'section': 'rules', 'index': 0, 'field': 'format'},
                        }))
                    value['rules'][0]['format'] = 'auto'
                    value['node-groups'][0].update(failover=True, retries=101)
                    body['value'] = json.dumps(value)
                    for dry_run in (True, False):
                        self.assertEqual(post({**body, 'dry_run': dry_run}), (400, {
                            'error': 'INVALID_EDITOR_GROUP',
                            'issue': {'section': 'node-groups', 'index': 0, 'field': 'retries'},
                        }))
                    with patch.object(self.system, 'preflight',
                                      side_effect=ManagementError('SYSTEM_OPERATION_FAILED:fixture')):
                        self.assertEqual(post({**self.request(), 'dry_run': True}),
                                         (503, {'error': 'SYSTEM_OPERATION_FAILED'}))
            finally:
                server.shutdown()
                thread.join(2)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertEqual(self.local.read_bytes(), self.previous)
        self.assertIsNone(self.admin.operation)
        self.assertFalse((self.files / 'clash-policy.json').exists())

    def test_native_provider_url_and_rule_edit_have_counts_without_content(self):
        config = deepcopy(self.saved)
        config['rules'][0]['url'] = 'https://example.com/?token=changed-private-url'
        config['rules'][1]['fixrule'].insert(0, 'DOMAIN,https://private-rule.example/?token=raw-private,DIRECT')
        body = {**self.request(config), 'dry_run': True}
        value = editor.handle(self.admin, body)
        self.assertEqual(value['change_summary']['rule_providers']['counts']['modified'], 1)
        self.assertEqual(value['change_summary']['rules']['counts']['added'], 1)
        self.assertNotIn('changed-private-url', json.dumps(value))
        self.assertNotIn('raw-private', json.dumps(value))

    def test_native_preview_matches_old_ppsub_and_distinguishes_name_replacement(self):
        old={'exported_at':'old','subs':[{'name':'Original','url':'https://example.com/?token=old-private'}],
             'node-groups':[{'name':'First'},{'name':'Second'}], 'rules':[{'fixrule':['MATCH,DIRECT']}]}
        local=self.files/'ppsub.json';local.write_text(json.dumps(old));previous=local.read_bytes()
        result=self.admin.current_config();config=deepcopy(old);config['exported_at']='new'
        body={'kind':'editor','edit_kind':'ppsub','value':json.dumps(config),'base_revision':result['base_revision'],
              'settings_revision':result['settings_revision'],'operation_id':'native-preview-001','dry_run':True}
        noop=editor.handle(self.admin,body)
        self.assertFalse(noop['change_summary']['changed']);self.assertFalse(noop['editor_changed'])
        self.assertEqual(noop['application']['action'], 'none')
        config['subs'][0]['name']='Replacement';config['subs'][0]['url']='https://example.com/?token=new-private'
        config['node-groups'].reverse();body['value']=json.dumps(config)
        value=editor.handle(self.admin,body)
        self.assertEqual(value['change_summary']['subscriptions']['counts']['added'],1)
        self.assertEqual(value['change_summary']['subscriptions']['counts']['removed'],1)
        self.assertEqual(value['change_summary']['subscriptions']['renamed'],[])
        self.assertTrue(value['change_summary']['node_groups']['order_changed'])
        self.assertEqual(value['source_details']['kind'],'native_ppsub')
        self.assertNotIn('new-private',json.dumps(value));self.assertEqual(local.read_bytes(),previous)

    def test_native_password_only_preview_changes_feature_without_showing_password(self):
        config = deepcopy(self.saved)
        config['shadowsocks_in'] = {'enable': True, 'cipher': 'aes-128-gcm', 'password': 'original-feature-password'}
        self.local.write_text(json.dumps(config), encoding='utf-8')
        previous = self.local.read_bytes()
        config['shadowsocks_in']['password'] = 'replacement-feature-password'
        body = {**self.request(config), 'settings_patch': {}, 'dry_run': True}
        value = editor.handle(self.admin, body)
        self.assertTrue(value['editor_changed'])
        self.assertEqual(value['action'], 'restart')
        self.assertEqual(value['change_summary']['features']['changed_keys'], ['shadowsocks_in'])
        for section in ('subscriptions', 'node_groups', 'rule_providers', 'rules'):
            self.assertFalse(value['change_summary'][section]['changed'])
        self.assertNotIn('original-feature-password', json.dumps(value))
        self.assertNotIn('replacement-feature-password', json.dumps(value))
        self.assertEqual(self.local.read_bytes(), previous)

    def test_refresh_rollback_unconfirmed_requires_recovery_without_claiming_restore(self):
        body={'base_revision':source.revision(self.ini.read_bytes()),'settings_revision':settings_revision(self.layout)}
        self.admin.operation={'id':'refresh-recovery-001','status':'applying','kind':'refresh_nodes'}
        with patch('manage_subscription.refresh_nodes',side_effect=ManagementError('SUBSCRIPTION_ROLLBACK_UNCONFIRMED')):
            self.admin.refresh_nodes_worker(body)
        value=dict(self.admin.operation)
        self.assertEqual(value['status'],'failed');self.assertEqual(value['result'],'failed')
        self.assertEqual(value['phase'],'complete');self.assertEqual(value['recovery'],'check_required')
        self.assertEqual(value['error'],'SUBSCRIPTION_ROLLBACK_UNCONFIRMED')
        self.assertIn('已尝试还原',value['message']);self.assertIn('尚未确认',value['message'])
        self.assertNotIn('继续保留',value['message'])

if __name__=='__main__':unittest.main()
