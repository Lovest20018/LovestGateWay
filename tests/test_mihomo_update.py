from pathlib import Path
import hashlib
import json
import socket
import ssl
from email.message import Message
from urllib.error import HTTPError, URLError
import gzip
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import mihomo_update as update


class UpdaterTests(unittest.TestCase):
    def release(self, raw):
        return {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False,
                'assets': [{'name': 'mihomo-linux-amd64-compatible-v1.2.3.gz',
                'browser_download_url': 'https://github.com/MetaCubeX/mihomo/releases/download/v1.2.3/mihomo-linux-amd64-compatible-v1.2.3.gz',
                'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)}]}

    def elf(self):
        return b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00' + bytes(1024 * 1024)

    def test_only_official_stable_digest_matched_asset_is_selected(self):
        release = self.release(b'asset')
        self.assertEqual(update.release_asset(release, 'x86_64')['version'], 'v1.2.3')
        release['assets'][0]['browser_download_url'] = 'https://example.com/malicious'
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')
        self.assertFalse(update.official_url('https://github.com.evil.example/a'))
        self.assertFalse(update.official_url('http://github.com/MetaCubeX/mihomo'))
        self.assertTrue(update.official_url('https://release-assets.githubusercontent.com/github-production-release-asset/a'))

    def test_hash_failure_does_not_change_installed_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'code/bin/clash';binary.parent.mkdir(parents=True);binary.write_bytes(b'old')
            updater = update.Updater(root / 'code', root / 'state')
            with patch.object(update, 'operation_lock'), patch.object(updater, '_check', return_value=update.release_asset(self.release(b'good'), 'x86_64')), \
                 patch.object(update, 'version_of', return_value='v0.0.1'), patch.object(update, 'fetch', return_value=b'bad'):
                with self.assertRaisesRegex(ValueError, 'CHECKSUM'):updater.update()
            self.assertEqual(binary.read_bytes(), b'old')

    def test_failed_health_restores_previous_core(self):
        compressed = gzip.compress(self.elf())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'code/bin/clash';binary.parent.mkdir(parents=True);binary.write_bytes(b'old')
            updater = update.Updater(root / 'code', root / 'state')
            def version(path):return 'v0.0.1' if Path(path).read_bytes() == b'old' else 'v1.2.3'
            with patch.object(update, 'operation_lock'), patch.object(updater, '_check', return_value=update.release_asset(self.release(compressed), 'x86_64')), \
                 patch.object(update, 'fetch', return_value=compressed), patch.object(update, 'version_of', side_effect=version), \
                 patch.object(updater, 'validate_config'), patch.object(updater, 'restart_and_check', side_effect=[ValueError('failed'), None]):
                with self.assertRaisesRegex(ValueError, 'ROLLED_BACK'):updater.update()
            self.assertEqual(binary.read_bytes(), b'old')
            self.assertEqual(updater.previous.read_bytes(), b'old')

    def test_prerelease_or_missing_digest_is_not_silently_trusted(self):
        release = self.release(b'asset');release['prerelease'] = True
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')
        release['prerelease'] = False;release['assets'][0]['digest'] = None
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')

    def test_network_failure_reports_stage_without_leaking_upstream_details(self):
        cases = [
            (URLError(socket.gaierror(-2, 'private-dns-detail')), 'RELEASE_DNS_FAILED'),
            (URLError(TimeoutError('private-timed-out-url')), 'RELEASE_NETWORK_TIMEOUT'),
            (URLError(ssl.SSLCertVerificationError('private-certificate-detail')), 'RELEASE_TLS_FAILED'),
            (URLError(ConnectionRefusedError('private-proxy-detail')), 'RELEASE_NETWORK_FAILED'),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected), patch.object(update, 'build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(update.ReleaseFetchError) as caught:
                    update.fetch(update.API, 2048)
                details = update.error_details(caught.exception)
                self.assertEqual(details['error'], expected)
                self.assertEqual(details['stage'], 'metadata')
                self.assertEqual(details['host'], 'api.github.com')
                self.assertNotIn('private-', json.dumps(details))

    def test_http_limit_is_distinguished_from_access_denied(self):
        for remaining, expected in [('0', 'RELEASE_RATE_LIMITED'), ('12', 'RELEASE_HTTP_FAILED')]:
            headers = Message(); headers['X-RateLimit-Remaining'] = remaining
            error = HTTPError(update.API, 403, 'private-body', headers, None)
            with self.subTest(remaining=remaining), patch.object(update, 'build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(update.ReleaseFetchError) as caught:
                    update.fetch(update.API, 2048)
                details = update.error_details(caught.exception)
                self.assertEqual(details['error'], expected)
                self.assertEqual(details['http_status'], 403)
                self.assertNotIn('private-body', json.dumps(details))

    def test_download_failure_is_labeled_as_download_and_unknown_errors_are_sanitized(self):
        url = 'https://github.com/MetaCubeX/mihomo/releases/download/v1.2.3/mihomo.gz'
        with patch.object(update, 'build_opener') as build:
            build.return_value.open.side_effect = TimeoutError('private-detail')
            with self.assertRaises(update.ReleaseFetchError) as caught:
                update.fetch(url, 2048)
        details = update.error_details(caught.exception)
        self.assertEqual(details['stage'], 'download')
        self.assertEqual(details['error'], 'RELEASE_NETWORK_TIMEOUT')
        self.assertNotIn('private-', json.dumps(update.error_details(ValueError('private-secret'))))

    def test_maintenance_persists_a_safe_actionable_error_for_the_web_page(self):
        import maintenance
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with patch.object(maintenance, 'STATE', state), patch.object(maintenance, 'Updater') as updater, \
                 patch.object(sys, 'argv', ['maintenance.py', 'check']), patch('builtins.print'):
                updater.return_value.check.side_effect = update.ReleaseFetchError('RELEASE_NETWORK_TIMEOUT', update.API)
                with self.assertRaises(SystemExit) as exit_info: maintenance.main()
            self.assertEqual(exit_info.exception.code, 1)
            result = json.loads((state / 'operation.json').read_bytes())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['action'], 'check')
            self.assertEqual(result['error'], 'RELEASE_NETWORK_TIMEOUT')
            self.assertEqual(result['stage'], 'metadata')
            self.assertIn('超时', result['message'])

    def test_offline_install_uses_pinned_asset_without_any_network_request(self):
        compressed = gzip.compress(self.elf())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root / 'mihomo.gz'; archive.write_bytes(compressed)
            updater = update.Updater(root / 'code', root / 'state')
            with patch.object(update, 'operation_lock'), patch.object(update, 'fetch') as network, \
                 patch.object(update, 'version_of', side_effect=lambda path: 'v1.2.3' if Path(path).is_file() else None), \
                 patch.object(updater, 'validate_config'), patch.object(updater, 'restart_and_check'):
                result = updater.install_local(archive, hashlib.sha256(compressed).hexdigest(), 'v1.2.3')
            network.assert_not_called()
            self.assertEqual(result['status'], 'updated')
            self.assertEqual(updater.binary.read_bytes(), self.elf())
            self.assertEqual(updater.status()['installed_asset_sha256'], hashlib.sha256(compressed).hexdigest())

    def test_offline_corruption_keeps_installed_core_and_does_not_contact_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root / 'mihomo.gz'; archive.write_bytes(b'corrupt')
            updater = update.Updater(root / 'code', root / 'state')
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(b'old')
            with patch.object(update, 'operation_lock'), patch.object(update, 'fetch') as network:
                with self.assertRaisesRegex(ValueError, 'CHECKSUM'):
                    updater.install_local(archive, hashlib.sha256(b'good').hexdigest(), 'v1.2.3')
            network.assert_not_called()
            self.assertEqual(updater.binary.read_bytes(), b'old')

    def test_offline_failed_health_check_restores_installed_core(self):
        compressed = gzip.compress(self.elf())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root / 'mihomo.gz'; archive.write_bytes(compressed)
            updater = update.Updater(root / 'code', root / 'state')
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(b'old')
            def version(path): return 'v0.0.1' if Path(path).read_bytes() == b'old' else 'v1.2.3'
            with patch.object(update, 'operation_lock'), patch.object(update, 'fetch') as network, \
                 patch.object(update, 'version_of', side_effect=version), patch.object(updater, 'validate_config'), \
                 patch.object(updater, 'restart_and_check', side_effect=[ValueError('failed'), None]):
                with self.assertRaisesRegex(ValueError, 'ROLLED_BACK'):
                    updater.install_local(archive, hashlib.sha256(compressed).hexdigest(), 'v1.2.3')
            network.assert_not_called()
            self.assertEqual(updater.binary.read_bytes(), b'old')

    def test_rate_limit_header_cooldown_is_honored_and_recorded(self):
        headers = Message()
        headers['X-RateLimit-Remaining'] = '0'; headers['X-RateLimit-Reset'] = '2000'
        headers['Retry-After'] = '120'
        with patch.object(update.time, 'time', return_value=1000):
            self.assertEqual(update.rate_limit_retry_at(headers), 2001)
            error = HTTPError(update.API, 403, 'private-upstream-body', headers, None)
            with patch.object(update, 'build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(update.ReleaseFetchError) as caught: update.fetch(update.API, 2048)
            details = update.error_details(caught.exception)
            self.assertEqual(details['retry_at'], 2001)
            self.assertIn('后重试', details['message'])
            self.assertNotIn('private-', json.dumps(details))

    def test_limited_api_is_not_retried_until_server_reset_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); updater = update.Updater(root / 'code', root / 'state')
            error = update.ReleaseFetchError('RELEASE_RATE_LIMITED', update.API, 403, retry_at=2001)
            with patch.object(update.time, 'time', return_value=1000), patch.object(update, 'fetch', side_effect=error) as network:
                for attempt in range(2):
                    with self.assertRaises(update.ReleaseFetchError): updater._check()
                self.assertEqual(network.call_count, 1)
                self.assertEqual(updater.status()['retry_at'], 2001)
            with patch.object(update.time, 'time', return_value=2002), \
                 patch.object(update, 'fetch', return_value=json.dumps(self.release(b'asset')).encode()) as network:
                self.assertEqual(updater._check()['version'], 'v1.2.3')
                network.assert_called_once()
                self.assertNotIn('retry_at', updater.status())
                self.assertEqual(updater.status()['last_check_result'], 'success')

    def test_check_then_update_reuses_recent_verified_metadata_without_another_api_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); updater = update.Updater(root / 'code', root / 'state')
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(b'installed')
            metadata = json.dumps(self.release(b'asset')).encode()
            with patch.object(update, 'operation_lock'), patch.object(update.time, 'time', return_value=1000), \
                 patch.object(update, 'version_of', return_value='v1.2.3'), patch.object(update, 'fetch', return_value=metadata) as network:
                updater.check()
                self.assertEqual(updater.update()['status'], 'unchanged')
                network.assert_called_once_with(update.API, 2 * 1024 * 1024)
                self.assertTrue(updater.status()['metadata_cached'])
            with patch.object(update.time, 'time', return_value=1301), patch.object(update, 'fetch', return_value=metadata) as network:
                updater._check()
                network.assert_called_once()
                self.assertFalse(updater.status()['metadata_cached'])

    def test_corrupt_or_untrusted_cache_requires_fresh_verified_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); updater = update.Updater(root / 'code', root / 'state')
            bad = self.release(b'asset');bad['assets'][0]['browser_download_url'] = 'https://evil.example/payload.gz'
            update.atomic_json(updater.state / 'release-cache.json', {'checked_at': 1000, 'release': bad})
            with patch.object(update.time, 'time', return_value=1001), \
                 patch.object(update, 'fetch', return_value=json.dumps(self.release(b'asset')).encode()) as network:
                selected = updater._check()
                self.assertTrue(selected['url'].startswith('https://github.com/MetaCubeX/mihomo/'))
                network.assert_called_once()
