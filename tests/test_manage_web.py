import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
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
        with self.assertRaises(HTTPError) as denied: call('/api/preview', self.request(), 'a' * 64, 'https://example.org')
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with call('/api/config', token='a' * 64) as response:
            self.assertEqual(response.status, 200); self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.assertRaises(HTTPError) as denied: call('/api/config?path=/etc/passwd', token='a' * 64)
        self.assertEqual(denied.exception.code, 404)
        denied.exception.close()
        with self.assertRaises(ManagementError): self.admin.job('../config')


if __name__ == '__main__': unittest.main()
