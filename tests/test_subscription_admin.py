import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import subscription_admin as admin
from manage_common import Layout


class SubscriptionTests(unittest.TestCase):
    def test_maintenance_legacy_statuses_preserve_unknown_contract(self):
        from mihomo_update import atomic_json
        with tempfile.TemporaryDirectory() as temp:
            layout=Layout(temp); state=Path(temp)/'mihomo'; state.mkdir()
            manager=admin.Admin(layout)
            for status,expected in [('queued','running'),('interrupted','failed')]:
                value={'id':'compat_task','kind':'check','status':status,'created_at':__import__('time').time()}
                if status=='interrupted':value['stage']='interrupted'
                atomic_json(state/'operation.json',value)
                with patch('mihomo_update.STATE',state), patch('mihomo_update.Updater'):
                    data=manager.maintenance()['operation']
                self.assertEqual(data['status'],expected)
                if status=='interrupted':
                    self.assertEqual((data['phase'],data['result'],data['recovery']),('interrupted','unknown','check_required'))
                    self.assertIn('尚未确认',data['message'])

    def test_provider_status_http_requires_admin_and_does_not_fetch_full_configuration(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        import threading
        with tempfile.TemporaryDirectory() as temp:
            token=Path(temp)/'token';token.write_text('a'*64)
            manager=admin.Admin(Layout(temp))
            server=ThreadingHTTPServer(('127.0.0.1',0),admin.Handler);server.admin=manager
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch.object(admin,'CORE_TOKEN',token), \
                     patch.object(manager,'node_cache',return_value={'subscriptions':[]}) as node_cache, \
                     patch.object(manager,'current_config',side_effect=AssertionError('full config')), \
                     patch.object(manager.core,'request',side_effect=AssertionError('duplicate core request')):
                    for authorization,expected in [(None,401),('Bearer observer-token',401),('Bearer '+'a'*64,200)]:
                        connection=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                        connection.request('GET','/api/providers',headers={} if authorization is None else {'Authorization':authorization})
                        response=connection.getresponse();self.assertEqual(response.status,expected);response.read();connection.close()
                    self.assertEqual(node_cache.call_count,1)
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_manual_refresh_rejects_stale_request_before_scheduling(self):
        from manage_common import Layout, ManagementError
        from manage_model import settings_revision
        with tempfile.TemporaryDirectory() as temp:
            layout = Layout(temp)
            files = layout.path('etc/paopaogw/files'); files.mkdir(parents=True)
            config = files / 'ppgw.ini'; config.write_text('#paopao-gateway\nmode=suburl\nsubtime=manual\n')
            manager = admin.Admin(Layout(temp))
            body = {'kind': 'refresh_nodes', 'base_revision': admin.revision(config.read_bytes()),
                    'settings_revision': settings_revision(layout), 'operation_id': 'manual-test-001'}
            with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', files), \
                 patch.object(admin, 'config_values', return_value={'mode': 'suburl', 'suburl': 'ppsub@https://example.com/config'}), \
                 patch('subscription_admin.threading.Timer') as timer:
                stale = dict(body, settings_revision='0' * 64)
                with self.assertRaisesRegex(ManagementError, 'CONFIG_REVISION_CONFLICT'):
                    manager.refresh_nodes(stale)
                timer.assert_not_called()
                operation = manager.refresh_nodes(body)
                self.assertEqual(operation['kind'], 'refresh_nodes')
                self.assertEqual(operation['status'], 'applying')
                self.assertEqual(manager.refresh_nodes(body), operation)
                self.assertEqual(timer.call_count, 1)

    def test_native_panel_bearer_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / 'core-token'
            digest = hashlib.sha256(b'panel-password').hexdigest()
            token.write_text(digest)
            manager = admin.Admin(Layout(temp))
            with patch.object(admin, 'CORE_TOKEN', token):
                self.assertTrue(manager.authenticated('Bearer ' + digest))
                self.assertFalse(manager.authenticated(None))
                self.assertFalse(manager.authenticated('Bearer panel-password'))
                self.assertFalse(manager.authenticated('Bearer observer-token'))
                self.assertFalse(manager.authenticated('Bearer 非法字符'))

    def test_complete_clash_sources_show_status_and_lock_the_editor(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = Layout(temp)
            files = layout.path('etc/paopaogw/files')
            files.mkdir(parents=True)
            config = files / 'ppgw.ini'
            manager = admin.Admin(layout)
            for mode, suburl, source_kind in (
                ('yaml', '', 'local_yaml'),
                ('suburl', 'https://example.com/sub?token=fixture', 'remote_yaml'),
            ):
                with self.subTest(mode=mode):
                    original = f'#paopao-gateway\nmode="{mode}"\nsuburl="{suburl}"\nyamlfile="legacy.yaml"\n'.encode()
                    config.write_bytes(original)
                    exported = f"mode='{mode}'\nsuburl='{suburl}'\nyamlfile='legacy.yaml'\n"
                    with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', files), \
                         patch('subscription_admin.subprocess.run') as run:
                        run.return_value.stdout = exported
                        result = manager.current_config()
                    self.assertEqual(result['source'], 'empty')
                    self.assertEqual(result['edit_kind'], 'ppsub')
                    self.assertFalse(result['write_allowed'])
                    self.assertIn('不再支持在线编辑完整 Clash YAML', result['notice'])
                    self.assertEqual(result['source_details']['kind'], source_kind)
                    self.assertEqual(result['source_details']['url'], suburl)
                    if mode == 'yaml':
                        self.assertEqual(result['source_details']['file'], 'legacy.yaml')
                    for key in ('subs', 'node-groups', 'rules'):
                        self.assertEqual(result['config'][key], [])
                    self.assertNotIn('snapshot_id', result)
                    self.assertEqual(config.read_bytes(), original)
                    run.assert_called_once()
                    self.assertIsInstance(run.call_args.args[0], list)

    def test_status_requires_a_ready_core_and_saved_native_source_without_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = Layout(temp)
            files = layout.path('etc/paopaogw/files')
            files.mkdir(parents=True)
            config = files / 'ppgw.ini'
            native_url = 'ppsub@https://example.com/config'
            remote_url = 'https://example.com/config'
            manager = admin.Admin(layout)
            with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', files), \
                 patch.object(admin, 'config_values', side_effect=AssertionError('subprocess during status poll')):
                for mode, suburl, running, installed, allowed in (
                    ('suburl', native_url, 'suburl', True, True),
                    ('suburl', native_url, 'suburl', False, False),
                    ('suburl', remote_url, 'suburl', True, False),
                    ('yaml', '', 'yaml', True, False),
                    ('free', '', 'free', False, False),
                ):
                    with self.subTest(mode=mode, suburl=suburl, installed=installed):
                        config.write_text(f"#paopao-gateway\n  mode='{mode}'\nsuburl='{suburl}'\n")
                        with patch.object(manager.core, 'request', return_value={
                            'mode': running, 'clash_version': 'fixture' if installed else '',
                        }):
                            value = manager.status()
                        self.assertEqual(value['saved_mode'], mode)
                        self.assertEqual(value['running_mode'], running)
                        self.assertEqual(value['ready'], installed or mode == 'free')
                        self.assertEqual(value['edit_kind'], 'ppsub')
                        self.assertEqual(value['ppsub_write_allowed'], allowed)

    def test_other_outbound_modes_require_source_switch_before_editing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root / 'ppgw.ini'; config.write_text('#paopao-gateway\nmode=free\n')
            with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', root), \
                 patch.object(admin, 'config_values', return_value={'mode': 'free'}):
                value = admin.Admin(Layout(temp)).current_config()
            self.assertFalse(value['write_allowed'])
            self.assertEqual(value['source'], 'empty')
            self.assertEqual(value['edit_kind'], 'ppsub')
            self.assertIn('当前不是 LovestSub 模式', value['notice'])

    def test_legacy_yaml_editor_and_export_requests_get_explicit_http_rejection(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from threading import Thread

        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / 'token'
            token.write_text('a' * 64)
            manager = admin.Admin(Layout(temp))
            with ThreadingHTTPServer(('127.0.0.1', 0), admin.Handler) as server:
                server.admin = manager
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with patch.object(admin, 'CORE_TOKEN', token), patch.object(admin.threading, 'Timer') as timer:
                        for body in (
                            {'kind': 'clash_patch'},
                            {'kind': 'export_yaml'},
                            {'kind': 'editor', 'edit_kind': 'clash_patch'},
                        ):
                            with self.subTest(body=body):
                                with self.assertRaisesRegex(ValueError, '^CLASH_YAML_EDITING_UNSUPPORTED$'):
                                    manager.apply(body)
                                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                                try:
                                    conn.request('POST', '/api/subscription', json.dumps(body), {
                                        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + 'a' * 64,
                                        'Origin': f'http://127.0.0.1:{server.server_port}',
                                    })
                                    response = conn.getresponse()
                                    self.assertEqual(response.status, 400)
                                    self.assertEqual(json.loads(response.read()), {
                                        'error': 'CLASH_YAML_EDITING_UNSUPPORTED',
                                        'message': admin.CLASH_YAML_EDITING_MESSAGE,
                                    })
                                finally:
                                    conn.close()
                        timer.assert_not_called()
                    self.assertIsNone(manager.operation)
                finally:
                    server.shutdown()
                    thread.join(2)

    def test_full_clash_cannot_be_overwritten_by_ppsub_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(admin, 'FILES', root), \
                 patch.object(admin, 'config_values', return_value={'mode': 'suburl', 'suburl': 'https://example.com/sub'}):
                payload = {'kind': 'ppsub', 'value': json.dumps({'exported_at': 'snapshot', 'subs': [{'name': 'S', 'url': 'https://example.com/sub'}]})}
                with self.assertRaisesRegex(ValueError, 'CLASH_PPSUB_NOT_EQUIVALENT'):
                    admin.Admin(Layout(temp)).apply(payload)

    def test_update_preserves_password_and_business_config(self):
        raw = b'#paopao-gateway\nmode=free\nclash_web_password="private-secret"\nfake_cidr=7.0.0.0/8\n'
        updated = admin.update_config(raw, {'mode':'suburl', 'suburl':'https://example.com/sub?token=abc'})
        self.assertIn(b'clash_web_password="private-secret"', updated)
        self.assertIn(b'fake_cidr=7.0.0.0/8', updated)
        self.assertEqual(updated.count(b'mode='), 1)
        self.assertIn(b'mode="suburl"', updated)

    def test_payload_only_accepts_supported_inputs(self):
        changes, upload = admin.validate_subscription({'kind':'url', 'value':'https://example.com/sub?token=x'})
        self.assertEqual(changes['mode'], 'suburl')
        self.assertIsNone(upload)
        changes, upload = admin.validate_subscription({'kind':'yaml', 'value':'proxies:\n  - name: x\n'})
        self.assertEqual(upload[0], 'custom.yaml')
        for value in ('file:///etc/passwd', 'https://user:secret@example.com/', 'https://example.com/\nx=1', 'https://example.com/"'):
            with self.assertRaises(ValueError):
                admin.validate_subscription({'kind':'url', 'value':value})
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'yaml', 'value':'no nodes'})
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'url', 'value':'https://example.com/', 'path':'/etc/hosts'})

    def test_ppsub_file_requires_actual_export_shape(self):
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'ppsub', 'value':'{}'})
        _, upload = admin.validate_subscription({'kind':'ppsub', 'value':json.dumps({'exported_at':'test','subs':[{'name':'x','url':'https://example.com'}]})})
        self.assertEqual(upload[0], 'ppsub.json')

    def test_failed_apply_restores_all_prior_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = root / 'files'
            files.mkdir()
            config = files / 'ppgw.ini'
            original = b'#paopao-gateway\nmode=free\n'
            config.write_bytes(original)
            (files / 'ppsub.json').write_bytes(b'{"private":"old"}')
            candidate = admin.update_config(original, {'mode':'yaml'})
            manager = admin.Admin(Layout(temp))
            manager.operation = {'id':'test','status':'applying'}
            with patch.object(admin,'CONFIG',config), patch.object(admin,'FILES',files), patch.object(admin,'STATE',root/'state'), patch('subscription_admin.subprocess.run',side_effect=RuntimeError()):
                manager.worker(original,candidate,('custom.yaml',b'proxies: []'))
            self.assertEqual(config.read_bytes(),original)
            self.assertEqual((files/'ppsub.json').read_bytes(),b'{"private":"old"}')
            self.assertFalse((files/'custom.yaml').exists())
            self.assertEqual(manager.operation['status'],'failed')


if __name__=='__main__':
    unittest.main()
