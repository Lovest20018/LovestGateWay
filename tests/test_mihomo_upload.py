import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from mihomo_upload import UploadStore, CHUNK_BYTES, MAX_COMPRESSED
from manage_common import ManagementError, operation_lock
import maintenance
import subscription_admin


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UploadStore(Path(self.temp.name) / 'var/lib/paopaogw/maintenance')
        self.identifier = 'a' * 32
        self.raw = b'uploaded archive bytes'

    def begin(self, identifier=None, raw=None, **changes):
        value = self.raw if raw is None else raw
        return self.store.begin(dict(operation='begin', id=identifier or self.identifier, version='v1.2.3',
                                     sha256=hashlib.sha256(value).hexdigest(), size=len(value), **changes))

    def chunk(self, value=None, offset=0):
        return self.store.chunk(dict(operation='chunk', id=self.identifier, offset=offset,
                                    data=base64.b64encode(self.raw if value is None else value).decode()))

    def test_replayed_chunks_resume_and_commit_exact_content(self):
        self.assertLess(len(json.dumps(dict(operation='chunk',id=self.identifier,offset=MAX_COMPRESSED,data=base64.b64encode(b'x'*CHUNK_BYTES).decode())).encode()),32*1024)
        self.begin()
        self.chunk(self.raw[:5])
        self.assertEqual(self.chunk(self.raw[:5])['received'], 5)
        self.assertEqual(self.begin()['received'], 5)
        self.chunk(self.raw[5:], 5)
        self.store.commit(self.identifier)
        metadata, asset = self.store.pending()
        self.assertEqual(asset.read_bytes(), self.raw)
        self.assertEqual(metadata['version'], 'v1.2.3')
        with self.assertRaisesRegex(ValueError, 'INVALID_CORE_UPLOAD'):
            self.chunk()
        self.store.cancel(self.identifier)
        self.assertFalse(asset.exists())
        self.assertFalse(self.store.path('local-install.json').exists())

    def test_incomplete_and_corrupt_upload_cannot_be_scheduled(self):
        self.begin()
        self.chunk(self.raw[:-1])
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE'): self.store.commit(self.identifier)
        self.chunk(b'x', len(self.raw)-1)
        with self.assertRaisesRegex(ValueError, 'CHECKSUM_MISMATCH'): self.store.commit(self.identifier)
        self.assertFalse(self.store.path('local-install.json').exists())
        self.assertFalse(self.store.metadata(self.identifier)['ready'])

    def test_offset_replay_content_and_chunk_limits_are_enforced(self):
        self.begin(); self.chunk(self.raw[:3])
        for value, offset in [(b'x', 0), (b'x', 4)]:
            with self.assertRaisesRegex(ValueError, 'OFFSET'): self.chunk(value, offset)
        for data in ['not base64!', base64.b64encode(b'x'*(CHUNK_BYTES+1)).decode()]:
            with self.assertRaises(ValueError):
                self.store.chunk(dict(operation='chunk', id=self.identifier, offset=0, data=data))
        self.assertEqual(self.store.asset(self.identifier).read_bytes(), self.raw[:3])

    def test_bounds_invalid_ids_and_expired_upload_cleanup(self):
        for identifier in ['../escape', 'z'*32, None]:
            with self.assertRaises(ValueError): self.store.directory(identifier)
        with self.assertRaises(ValueError):
            self.store.begin(dict(operation='begin', id=self.identifier, version='v1.2.3', sha256='a'*64, size=MAX_COMPRESSED+1))
        self.begin(); self.begin(identifier='b'*32)
        with self.assertRaisesRegex(ValueError, 'BUSY'): self.begin(identifier='c'*32)
        with patch('mihomo_upload.time.time', return_value=99999999999): self.begin(identifier='c'*32)
        self.assertFalse(self.store.directory(self.identifier).exists())

    def test_management_transaction_and_exclusive_writer_prevent_upload(self):
        journal = self.store.layout.path('var/lib/paopaogw/admin/transaction.json')
        journal.parent.mkdir(parents=True); journal.write_text('{"phase":"installing"}')
        with self.assertRaisesRegex(ManagementError, 'RECOVERY_REQUIRED'): self.begin()
        journal.write_text('{"phase":"complete"}')
        with operation_lock(self.store.layout):
            # Another thread cannot reenter the current thread's filesystem lock.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as executor:
                with self.assertRaisesRegex(ManagementError, 'MANAGEMENT_BUSY'): executor.submit(self.begin).result()

    def test_admin_commit_schedules_only_verified_local_job(self):
        manager = subscription_admin.Admin()
        self.begin(); self.chunk()
        with patch('mihomo_upload.UploadStore', return_value=self.store), \
             patch.object(manager, 'core_idle'), patch.object(manager, 'start_core_action', return_value={'status':'running'}) as start:
            result = manager.core_upload({'operation':'commit','id':self.identifier})
        self.assertEqual(result['status'], 'running')
        start.assert_called_once_with('install-local')

    def test_http_upload_reuses_authenticated_same_origin_update_route(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        import threading
        manager = subscription_admin.Admin()
        server = ThreadingHTTPServer(('127.0.0.1', 0), subscription_admin.Handler)
        server.admin = manager
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        port = server.server_port
        token = Path(self.temp.name) / 'core-token'; token.write_text('a'*64)
        body = dict(operation='begin', id=self.identifier, version='v1.2.3', sha256=hashlib.sha256(self.raw).hexdigest(), size=len(self.raw))
        def send(path, value, authorization=None, origin=None):
            connection = HTTPConnection('127.0.0.1', port, timeout=5)
            headers = {'Content-Type':'application/json','Origin':origin or f'http://127.0.0.1:{port}'}
            if authorization: headers['Authorization'] = authorization
            connection.request('POST', path, None if origin else json.dumps(value), headers)
            response = connection.getresponse(); result = response.status, json.loads(response.read()); connection.close(); return result
        try:
            with patch.object(subscription_admin, 'CORE_TOKEN', token), patch('mihomo_upload.UploadStore', return_value=self.store), patch.object(manager, 'core_idle'):
                self.assertEqual(send('/api/core/update', body)[0], 401)
                self.assertFalse(self.store.directory(self.identifier).exists())
                self.assertEqual(send('/api/core/update', body, 'Bearer '+'a'*64, 'https://other.example')[0], 403)
                status, response = send('/api/core/update', body, 'Bearer '+'a'*64)
                self.assertEqual(status, 200); self.assertEqual(response['received'], 0)
                self.assertEqual(send('/api/core/check', body, 'Bearer '+'a'*64)[0], 400)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_maintenance_local_install_never_uses_online_update_and_cleans_staging(self):
        self.begin(); self.chunk(); self.store.commit(self.identifier)
        with patch.object(sys, 'argv', ['maintenance.py', 'install-local']), \
             patch('mihomo_upload.UploadStore', return_value=self.store), \
             patch.object(maintenance, 'STATE', self.store.state), patch.object(maintenance, 'Updater') as updater:
            maintenance.main()
        updater.return_value.install_local.assert_called_once_with(
            self.store.directory(self.identifier) / 'asset.gz', hashlib.sha256(self.raw).hexdigest(), 'v1.2.3')
        updater.return_value.check.assert_not_called(); updater.return_value.update.assert_not_called()
        self.assertEqual(json.loads(self.store.path('operation.json').read_bytes())['status'], 'complete')
        self.assertFalse(self.store.directory(self.identifier).exists())

    def test_failed_local_install_reports_error_and_cleans_staging(self):
        self.begin(); self.chunk(); self.store.commit(self.identifier)
        with patch.object(sys, 'argv', ['maintenance.py', 'install-local']), \
             patch('mihomo_upload.UploadStore', return_value=self.store), \
             patch.object(maintenance, 'STATE', self.store.state), patch.object(maintenance, 'Updater') as updater:
            updater.return_value.install_local.side_effect = ValueError('MIHOMO_VERSION_MISMATCH')
            with self.assertRaises(SystemExit): maintenance.main()
        self.assertEqual(json.loads(self.store.path('operation.json').read_bytes())['error'], 'MIHOMO_VERSION_MISMATCH')
        self.assertFalse(self.store.directory(self.identifier).exists())

if __name__ == '__main__': unittest.main()
