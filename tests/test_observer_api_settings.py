"""Observer credential settings stay private and use API-only transactions."""
import json
from pathlib import Path
import sys
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_manage_web as web_tests
from manage_common import ManagementError
from manage_model import load_settings, public_settings, render_updates, settings_revision
from manage_web import Handler


class ObserverApiSettingsTests(unittest.TestCase):
    request = web_tests.WebSettingsTests.request
    job = web_tests.WebSettingsTests.job

    def setUp(self):
        web_tests.WebSettingsTests.setUp(self)
        self.token = self.layout.path('etc/paopaogw/read-token')
        self.token.write_text('old-observer-' + 'a' * 40)
        self.api = self.layout.path('etc/paopaogw/api.json')
        self.api.write_text(json.dumps({'listen':'127.0.0.1','port':8788,'name':'Original'}))
        self.new_token = 'new-observer-' + 'b' * 40

    def test_default_off_and_switch_preview_include_only_auxiliary_services(self):
        self.assertFalse(load_settings(self.layout)['api']['enabled'])
        preview = self.admin.preview(self.request({'api': {'enabled': True}}))
        self.assertEqual(preview['effects']['core'], 'none')
        self.assertEqual(preview['effects']['services'], ['api', 'history'])
        self.assertEqual(preview['changes']['api']['enabled'], True)
        for invalid in (1, None, 'on'):
            with self.subTest(value=invalid), self.assertRaises(ManagementError):
                render_updates(self.layout, {'api': {'enabled': invalid}})

    def test_token_redaction_blank_preservation_and_separate_file(self):
        self.assertEqual(public_settings(load_settings(self.layout))['api']['read_token'], '********')
        self.assertEqual(render_updates(self.layout, {'api':{'read_token':''}}), ({}, {}))
        for value in ['short', 'a' * 31, 'a' * 257, 'a ' * 32, '秘密' * 32, None, '********']:
            with self.subTest(value=value), self.assertRaises(ManagementError):
                render_updates(self.layout, {'api':{'read_token':value}})
        updates, _ = render_updates(self.layout, {'api':{'read_token':self.new_token}})
        self.assertEqual(set(updates), {'etc/paopaogw/read-token'})

    def test_rotation_is_redacted_and_keeps_configuration_core_unchanged(self):
        payload = self.request({'api':{'read_token':self.new_token,'listen':'192.0.2.100','port':8788}})
        preview = self.admin.preview(payload)
        self.assertNotIn(self.new_token, json.dumps(preview))
        self.assertEqual(preview['effects']['core'], 'none')
        self.assertEqual(preview['effects']['services'], ['api'])
        body = self.job(payload=payload); self.admin.submit(body); self.callbacks.pop()()
        self.assertEqual(self.admin.job(body['id'])['status'], 'succeeded')
        self.assertEqual(self.token.read_text().strip(), self.new_token)
        self.assertNotIn('read_token', json.loads(self.api.read_bytes()))
        self.assertEqual(self.system.calls, ['none'])
        self.assertNotIn(self.new_token, json.dumps(self.admin.show()))
        for path in self.layout.path('var/lib/paopaogw/admin/web-jobs').glob('*.json'):
            self.assertNotIn(self.new_token, path.read_text())

    def test_failed_api_change_restores_token_without_restarting_core(self):
        old = self.token.read_bytes(); old_api = self.api.read_bytes()
        self.system.states['paopaogw-api'] = {'active':True,'enabled':True}
        restored=[];self.system.restore_services=lambda states:restored.append(states)
        self.system.fail=True
        with self.assertRaisesRegex(ManagementError, 'APPLY_FAILED_ROLLED_BACK'):
            self.admin.manager.apply({'api':{'read_token':self.new_token,'listen':'192.0.2.100'}}, settings_revision(self.layout))
        self.assertEqual(self.token.read_bytes(),old)
        self.assertEqual(self.api.read_bytes(),old_api)
        self.assertEqual(set(restored[0]),{'paopaogw-api'})

    def test_reveal_is_explicit_and_does_not_create_a_job(self):
        self.assertEqual(self.admin.observer_token({})['token'],self.token.read_text().strip())
        with self.assertRaises(ManagementError):self.admin.observer_token({'path':'/etc/passwd'})
        self.assertNotIn(self.token.read_text().strip(),json.dumps(self.admin.show()))

    def test_http_reveal_requires_admin_post_and_same_origin(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.admin = self.admin
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_address[1])
        opener = build_opener(ProxyHandler({}))

        def call(method='POST', token='a' * 64, source=origin, path='/api/observer-token', body=None):
            headers = {'Content-Type': 'application/json'}
            if source is not None: headers['Origin'] = source
            if token is not None: headers['Authorization'] = 'Bearer ' + token
            data = json.dumps({} if body is None else body).encode() if method == 'POST' else None
            try: response = opener.open(Request(origin + path, data, headers, method=method), timeout=3)
            except HTTPError as error: response = error
            with response:
                return response.status, response.headers, json.load(response)

        for token in (None, 'incorrect', self.token.read_text().strip()):
            self.assertEqual(call(token=token)[0], 401)
        for source in (None, 'https://other.example'):
            self.assertEqual(call(source=source)[0], 403)
        self.assertEqual(call(method='GET')[0], 404)
        self.assertEqual(call(path='/api/observer-token?reveal=true')[0], 404)
        self.assertEqual(call(body={'path': '/etc/passwd'})[0], 400)
        status, headers, value = call()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(value, {'token': self.token.read_text().strip()})
        self.assertEqual(self.callbacks, [])


if __name__ == '__main__': unittest.main()
