import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock, PropertyMock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage_common import Layout, ManagementError
from manage_config import ConfigManager
from manage_model import settings_revision
from manage_web import SettingsAdmin, Handler
from test_manage_config import FakeSystem


class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = Layout(self.temp.name); self.callbacks = []; self.system = FakeSystem()
        ini = self.layout.path('etc/paopaogw/files/ppgw.ini'); ini.parent.mkdir(parents=True)
        ini.write_text('#paopao-gateway\nmode=free\nnet_rec=yes\nclash_web_password=original-private-password\nsuburl=https://example.com/?token=private-subscription\n')
        self.layout.path('etc/paopaogw/revision-key').write_text('private-key-for-fixture')
        self.admin = SettingsAdmin(self.layout, self.system, start_job=self.callbacks.append)
        token = self.layout.path('var/lib/paopaogw/core-token'); token.parent.mkdir(parents=True, exist_ok=True); token.write_text('a' * 64)

    def request(self, patch=None): return {'base_revision': settings_revision(self.layout), 'patch': patch or {'ini': {'max_rec': 9}}}
    def job(self, identifier='a' * 32, payload=None): return {'id': identifier, 'kind': 'config', 'payload': payload or self.request()}

    def test_preview_is_redacted_side_effect_free_and_checks_revision(self):
        before = self.layout.path('etc/paopaogw/files/ppgw.ini').read_bytes()
        value = self.admin.preview(self.request({'ini': {'clash_web_password': 'new-secret-password-long'}}))
        self.assertEqual(value['action'], 'reload')
        self.assertNotIn('new-secret-password-long', json.dumps(value))
        self.assertEqual(self.layout.path('etc/paopaogw/files/ppgw.ini').read_bytes(), before)
        self.assertFalse(self.layout.path('var/backups/paopaogw').exists())
        bad = self.request(); bad['base_revision'] = '0' * 64
        with self.assertRaisesRegex(ManagementError, 'CONFIG_REVISION_CONFLICT'): self.admin.preview(bad)

    def test_jobs_are_idempotent_and_never_persist_plaintext_inputs(self):
        body = self.job(payload=self.request({'ini': {'clash_web_password': 'new-secret-password-long'}}))
        self.assertEqual(self.admin.submit(body)['status'], 'queued')
        self.assertEqual(self.admin.submit(body)['status'], 'queued')
        self.assertEqual(len(self.callbacks), 1)
        for path in self.layout.path('var/lib/paopaogw/admin/web-jobs').glob('*.json'):
            self.assertNotIn('new-secret-password-long', path.read_text())
        changed = self.job(payload=self.request({'ini': {'clash_web_password': 'different-secret-long'}}))
        with self.assertRaisesRegex(ManagementError, 'JOB_ID_REUSED'): self.admin.submit(changed)
        with self.assertRaisesRegex(ManagementError, 'MANAGEMENT_BUSY'): self.admin.submit(self.job('b' * 32))
        self.callbacks.pop()()
        self.assertEqual(self.admin.job(body['id'])['status'], 'succeeded')
        self.assertEqual(self.system.calls, ['reload'])
        public = json.dumps(self.admin.show())
        self.assertNotIn('private-subscription', public); self.assertNotIn('new-secret-password-long', public)

    def test_failed_job_retains_previous_configuration_and_public_error(self):
        before = self.layout.path('etc/paopaogw/files/ppgw.ini').read_bytes()
        self.system.fail = True
        body = self.job(); self.admin.submit(body); self.callbacks.pop()()
        value = self.admin.job(body['id'])
        self.assertEqual(value['status'], 'failed'); self.assertEqual(value['error'], 'APPLY_FAILED_ROLLED_BACK')
        self.assertEqual(self.layout.path('etc/paopaogw/files/ppgw.ini').read_bytes(), before)

    def test_interrupted_jobs_are_reported_after_service_restart(self):
        body = self.job(); self.admin.submit(body)
        restarted = SettingsAdmin(self.layout, self.system, start_job=self.callbacks.append)
        self.assertEqual(restarted.job(body['id'])['error'], 'INTERRUPTED_CHECK_RECOVERY')
        self.assertEqual(restarted.status()['management']['latest']['result'], 'unknown')
        self.assertEqual(restarted.status()['management']['latest']['recovery'], 'check_required')

    def test_status_avoids_full_config_and_rehash_until_external_metadata_changes(self):
        with patch.object(self.admin.manager,'show',side_effect=AssertionError('full config read')), \
             patch('manage_web.load_settings',side_effect=AssertionError('secret settings read')), \
             patch('manage_model.settings_revision', wraps=settings_revision) as revision:
            first = self.admin.status()
            self.admin.status()
            self.assertEqual(revision.call_count,1)
            asset = self.layout.path('etc/paopaogw/files/externally-added.yaml')
            asset.write_text('rules: []')
            self.admin.status()
            self.assertEqual(revision.call_count,2)
            asset.unlink()
            self.admin.status()
            self.assertEqual(revision.call_count,3)
            external=self.layout.path('etc/paopaogw/network')
            external.write_text('external network change')
            self.admin.status()
            self.assertEqual(revision.call_count,4)
            self.admin.revision_sample.checked-=61
            self.admin.status()
            self.assertEqual(revision.call_count,5)
        self.assertNotIn('settings',first)
        self.assertNotIn('private',json.dumps(first))

    def test_status_samples_core_once_and_never_attests_unreachable_runtime(self):
        client = Mock(); client.request.return_value = {'mode':'free','integration':{'boot_id':'boot','config_revision':'rev'}}
        # Pretending this temporary layout is live must not launch a worker
        # that can outlive the runtime-sampling test and its directory.
        with patch.object(Layout,'live',new_callable=PropertyMock,return_value=True), \
             patch.object(self.system,'client',return_value=client,create=True), \
             patch.object(self.admin.acceptance,'trigger'):
            value = self.admin.status()
            self.assertEqual(value['running_mode'],'free')
            client.request.assert_called_once_with('/sniffbox/info')
            client.request.side_effect = OSError('offline')
            value = self.admin.status()
            self.assertEqual(value['management']['state'],'unknown')

    def test_auxiliary_services_and_source_reset_are_in_preview(self):
        value = self.admin.preview(self.request({'maintenance': {'auto_update': False}, 'api': {'name': 'Fixture'}}))
        self.assertEqual(value['effects']['core'], 'none')
        self.assertEqual(value['effects']['services'], ['api', 'maintenance_timer'])
        self.layout.path('etc/paopaogw/files/clash-policy.json').write_text('{}')
        request = self.request({'ini': {'mode': 'suburl', 'suburl': 'https://example.net/config'}})
        with self.assertRaisesRegex(ManagementError, 'SOURCE_CONFLICT'): self.admin.preview(request)
        request['reset_source'] = True
        self.assertTrue(self.admin.preview(request)['effects']['source_reset'])

    def test_http_requires_admin_auth_and_same_origin_and_keeps_paths_fixed(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.admin = self.admin
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_address[1])
        opener = build_opener(ProxyHandler({}))
        def call(path, body=None, token=None, source=origin):
            headers = {'Origin': source, 'Content-Type': 'application/json'}
            if token: headers['Authorization'] = 'Bearer ' + token
            return opener.open(Request(origin + path, None if body is None else json.dumps(body).encode(), headers), timeout=3)
        with self.assertRaises(HTTPError) as denied: call('/api/config')
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with self.assertRaises(HTTPError) as denied: call('/api/status')
        self.assertEqual(denied.exception.code,401); denied.exception.close()
        with call('/api/status',token='a'*64) as response:
            self.assertEqual(response.status,200)
            self.assertNotIn('settings',json.load(response))
        self.admin.gateway_update.start = lambda identifier: None
        for path in ('/api/gateway-update/check', '/api/gateway-update/update'):
            with self.assertRaises(HTTPError) as denied: call(path, {'id':'9'*32})
            self.assertEqual(denied.exception.code, 401); denied.exception.close()
            with self.assertRaises(HTTPError) as denied: call(path, {'id':'9'*32}, 'a'*64, 'https://foreign.test')
            self.assertEqual(denied.exception.code, 403); denied.exception.close()
            with self.assertRaises(HTTPError) as denied: call(path, token='a'*64)
            self.assertEqual(denied.exception.code, 404); denied.exception.close()
        with call('/api/gateway-update/check', {'id':'9'*32}, 'a'*64) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(json.load(response)['status'], 'queued')
        with call('/api/gateway-update/jobs/'+'9'*32, token='a'*64) as response:
            self.assertEqual(json.load(response)['operation']['id'], '9'*32)
        with call('/api/gateway-update', token='a'*64) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertNotIn('request',json.load(response)['operation'])
        with self.assertRaises(HTTPError) as denied: call('/api/recheck', {})
        self.assertEqual(denied.exception.code, 401); denied.exception.close()
        with self.assertRaises(HTTPError) as denied: call('/api/recheck', {}, 'a'*64, 'https://foreign.test')
        self.assertEqual(denied.exception.code, 403); denied.exception.close()
        with call('/api/recheck', {}, 'a'*64) as response:
            self.assertEqual(json.load(response)['result'], 'staged')
        with self.assertRaises(HTTPError) as denied: call('/api/recheck', {'restart':True}, 'a'*64)
        self.assertEqual(denied.exception.code, 400); denied.exception.close()
        with self.assertRaises(HTTPError) as denied: call('/api/preview', self.request(), 'a' * 64, 'https://example.org')
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with call('/api/config', token='a' * 64) as response:
            self.assertEqual(response.status, 200); self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.assertRaises(HTTPError) as denied: call('/api/config?path=/etc/passwd', token='a' * 64)
        self.assertEqual(denied.exception.code, 404)
        denied.exception.close()
        with self.assertRaises(ManagementError): self.admin.job('../config')
        from manage_backup import BackupStore
        backup = BackupStore(self.layout).create(metadata={'kind': 'manual', 'services': self.system.status()})
        for path in ('/api/diagnostics', '/api/backups'):
            with self.assertRaises(HTTPError) as denied: call(path)
            self.assertEqual(denied.exception.code, 401); denied.exception.close()
            with call(path, token='a' * 64) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertNotIn('private', response.read().decode())
            with self.assertRaises(HTTPError) as denied: call(path + '?path=/etc/passwd', token='a' * 64)
            self.assertEqual(denied.exception.code, 404); denied.exception.close()
        for path, body in (('/api/restore/preview', {'backup': backup}),
                           ('/api/recover/preview', {}), ('/api/backups/list', {'offset': 0, 'limit': 1})):
            with self.assertRaises(HTTPError) as denied: call(path, body)
            self.assertEqual(denied.exception.code, 401); denied.exception.close()
            with self.assertRaises(HTTPError) as denied: call(path, body, 'a' * 64, 'https://foreign.test')
            self.assertEqual(denied.exception.code, 403); denied.exception.close()
        with call('/api/restore/preview', {'backup': backup}, 'a' * 64) as response:
            preview = json.load(response)
            self.assertIn('preview_token', preview)
        with call('/api/backups/list', {'offset': 0, 'limit': 1}, 'a' * 64) as response:
            self.assertEqual(len(json.load(response)['backups']), 1)
        with self.assertRaises(HTTPError) as denied: call('/api/backups/download', token='a' * 64)
        self.assertEqual(denied.exception.code, 404); denied.exception.close()


if __name__ == '__main__': unittest.main()
