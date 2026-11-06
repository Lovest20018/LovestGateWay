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
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import mihomo_update as update


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        cpu = patch.object(update, 'cpu_capabilities', return_value={
            'cpu_level': 'v1', 'cpu_detection': 'baseline-fallback', 'supported_variants': ['v1']})
        cpu.start(); self.addCleanup(cpu.stop)

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

    def test_gateway_network_failure_falls_back_without_inheriting_environment_proxy(self):
        gateway, direct = Mock(), Mock()
        gateway.open.side_effect = URLError(ConnectionRefusedError())
        direct.open.return_value.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b'official')))
        direct.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(update, 'build_opener', side_effect=[gateway, direct]) as build:
            self.assertEqual(update.fetch(update.API, 128), b'official')
        self.assertEqual(build.call_count, 2)
        self.assertTrue(any(isinstance(h, update.GatewayHTTPSHandler) for h in build.call_args_list[0].args))
        self.assertFalse(any(isinstance(h, update.GatewayHTTPSHandler) for h in build.call_args_list[1].args))
        self.assertTrue(all(call.args[0].proxies == {} for call in build.call_args_list))

    def test_gateway_http_and_tls_errors_do_not_switch_network_routes(self):
        headers = Message(); headers['X-RateLimit-Remaining'] = '0'
        for error in (HTTPError(update.API, 403, 'limited', headers, None), URLError(ssl.SSLCertVerificationError())):
            with self.subTest(kind=type(error).__name__), patch.object(update, 'build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(update.ReleaseFetchError): update.fetch(update.API, 128)
                self.assertEqual(build.call_count, 1)

    def test_gateway_socks_keeps_domain_and_handles_fragmented_replies(self):
        stream = Mock()
        data = bytearray(b'\x05\x00\x05\x00\x00\x01' + bytes(6))
        def receive(size):
            value = bytes(data[:1]); del data[:1]; return value
        stream.recv.side_effect = receive
        with patch.object(update.socket, 'create_connection', return_value=stream) as connect:
            self.assertIs(update.gateway_socket('api.github.com', 443, 5), stream)
        connect.assert_called_once_with(('127.0.0.1', 1079), 1)
        self.assertEqual(stream.sendall.call_args_list[1].args[0], b'\x05\x01\x00\x03\x0eapi.github.com\x01\xbb')
        stream.close.assert_not_called()
        connection = update.GatewayHTTPSConnection('api.github.com', timeout=5)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)

    def test_gateway_socks_rejection_closes_connection(self):
        stream = Mock(); stream.recv.return_value = b'\x05\xff'
        with patch.object(update.socket, 'create_connection', return_value=stream):
            with self.assertRaises(ConnectionError): update.gateway_socket('api.github.com', 443, 5)
        stream.close.assert_called_once()

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
                from manage_common import Layout
                with self.assertRaises(SystemExit) as exit_info: maintenance.main(Layout(temporary))
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


class CpuSelectionTests(unittest.TestCase):
    def capabilities(self, level='v3'):
        return {'cpu_level': level, 'cpu_detection': 'glibc-hwcaps',
                'supported_variants': ['v1', 'v2', 'v3'][:int(level[1])]}

    def release(self, raw=b'asset'):
        assets = []
        for level in ('v1', 'v2', 'v3', 'compatible'):
            name = f'mihomo-linux-amd64-{level}-v1.2.3.gz'
            assets.append({'name': name, 'browser_download_url':
                f'https://github.com/MetaCubeX/mihomo/releases/download/v1.2.3/{name}',
                'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)})
        return {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False, 'assets': assets}

    def elf(self, level):
        return UpdaterTests().elf() + b'\nbuild\tGOAMD64=' + level.encode() + b'\n'

    def updater(self, temporary):
        root = Path(temporary)
        return update.Updater(root / 'code', root / 'state', root / 'config.json')

    def test_cpu_probe_requires_positive_os_and_hypervisor_aware_loader_result(self):
        for output, level in [('x86-64-v3 (supported, searched)\n', 'v3'),
                              (' x86-64-v3\n x86-64-v2 (supported, searched)\n', 'v2'),
                              (' x86-64-v3\n x86-64-v2\n', 'v1')]:
            with self.subTest(level=level), patch.object(update.platform, 'system', return_value='Linux'), \
                 patch.object(Path, 'is_file', return_value=True), patch.object(update.subprocess, 'run') as run:
                run.return_value.stdout = output
                self.assertEqual(update.cpu_capabilities('x86_64')['cpu_level'], level)
                self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_probe_failure_is_baseline_and_other_architectures_are_rejected(self):
        with patch.object(update.platform, 'system', return_value='Linux'), patch.object(Path, 'is_file', return_value=True), \
             patch.object(update.subprocess, 'run', side_effect=TimeoutError()):
            self.assertEqual(update.cpu_capabilities('x86_64')['cpu_level'], 'v1')
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ARCHITECTURE'): update.cpu_capabilities('aarch64')

    def test_highest_confirmed_level_selects_exact_modern_asset(self):
        for level in ('v1', 'v2', 'v3'):
            with self.subTest(level=level), patch.object(update, 'cpu_capabilities', return_value=self.capabilities(level)):
                asset = update.release_asset(self.release())
                self.assertEqual(asset['architecture'], 'amd64-' + level)
                self.assertEqual(asset['asset'], f'mihomo-linux-amd64-{level}-v1.2.3.gz')

    def test_v1_alias_is_used_only_when_modern_v1_is_absent(self):
        release = self.release()
        release['assets'] = [row for row in release['assets'] if '-v1-v' not in row['name']]
        with patch.object(update, 'cpu_capabilities', return_value=self.capabilities('v1')):
            self.assertIn('-compatible-', update.release_asset(release)['asset'])
        release['assets'].append(self.release()['assets'][0])
        release['assets'][-1]['digest'] = None
        with patch.object(update, 'cpu_capabilities', return_value=self.capabilities('v1')):
            with self.assertRaisesRegex(ValueError, 'DIGEST'): update.release_asset(release)

    def test_no_silent_substitution_for_missing_or_untrusted_selected_asset(self):
        for kind in ('missing', 'duplicate', 'url', 'digest'):
            release = self.release(); selected = release['assets'][2]
            if kind == 'missing': release['assets'].remove(selected)
            elif kind == 'duplicate': release['assets'].append(selected)
            elif kind == 'url': selected['browser_download_url'] = 'https://evil.example/core.gz'
            else: selected['digest'] = None
            with self.subTest(kind=kind), patch.object(update, 'cpu_capabilities', return_value=self.capabilities()):
                with self.assertRaises(ValueError): update.release_asset(release)

    def test_manual_lower_level_is_allowed_but_unsupported_level_is_rejected(self):
        with patch.object(update, 'cpu_capabilities', return_value=self.capabilities('v2')):
            self.assertEqual(update.release_asset(self.release(), variant='v1')['architecture'], 'amd64-v1')
            with self.assertRaisesRegex(ValueError, 'CORE_VARIANT_UNSUPPORTED'):
                update.release_asset(self.release(), variant='v3')

    def test_settings_default_to_auto_and_do_not_mislabel_current_binary(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
             patch.object(update, 'version_of', return_value='v1.2.3'):
            updater = self.updater(temporary)
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(self.elf('v1'))
            status = updater.status()
            self.assertEqual(status['selection'], 'auto')
            self.assertEqual(status['architecture'], 'amd64-v3')
            self.assertEqual(status['installed_architecture'], 'amd64-v1')
            updater.config.write_text('{"core_variant":"v2"}')
            self.assertEqual(updater.status()['architecture'], 'amd64-v2')
            updater.config.write_text('{"core_variant":"evil"}')
            with self.assertRaisesRegex(ValueError, 'INVALID_CORE_VARIANT'): updater.status()

    def test_version_comparison_handles_numeric_order_and_never_auto_downgrades(self):
        for current, available, newer in [('v1.2.2', True, False), ('v1.2.3', False, False),
                                           ('v1.2.10', False, True), ('alpha-test', False, False)]:
            with self.subTest(current=current), tempfile.TemporaryDirectory() as temporary, \
                 patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
                 patch.object(update, 'version_of', return_value=current), \
                 patch.object(update, 'fetch', return_value=json.dumps(self.release()).encode()):
                updater = self.updater(temporary)
                updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(self.elf('v3'))
                updater._check(); status = updater.status()
                self.assertEqual(status['update_available'], available)
                self.assertEqual(status['installed_newer'], newer)
                if newer:
                    with patch.object(update, 'operation_lock'), patch.object(update, 'fetch') as network:
                        self.assertEqual(updater.update(force=True)['reason'], 'installed_newer')
                        network.assert_not_called()

    def test_same_version_variant_change_is_separate_from_scheduled_upgrade(self):
        compressed = gzip.compress(self.elf('v3'))
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
             patch.object(update, 'version_of', return_value='v1.2.3'), patch.object(update, 'operation_lock'):
            updater = self.updater(temporary)
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(self.elf('v1'))
            with patch.object(update, 'fetch', return_value=json.dumps(self.release(compressed)).encode()): updater._check()
            self.assertFalse(updater.status()['update_available'])
            self.assertTrue(updater.status()['variant_change_available'])
            with patch.object(update, 'fetch', return_value=compressed), patch.object(updater, 'validate_config'), \
                 patch.object(updater, 'restart_and_check') as health:
                self.assertEqual(updater.update()['status'], 'updated')
                health.assert_called_once_with('v1.2.3')
            self.assertEqual(updater.status()['installed_architecture'], 'amd64-v3')
            self.assertFalse(updater.status()['variant_change_available'])

    def test_cache_is_reselected_after_cpu_or_preference_change_without_new_request(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()):
            updater = self.updater(temporary)
            with patch.object(update, 'fetch', return_value=json.dumps(self.release()).encode()):
                self.assertEqual(updater._check()['architecture'], 'amd64-v3')
            updater.config.write_text('{"core_variant":"v1"}')
            with patch.object(update, 'fetch') as network:
                self.assertEqual(updater._check()['architecture'], 'amd64-v1')
                network.assert_not_called()

    def test_unrecognized_existing_version_is_not_overwritten_by_online_update(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
             patch.object(update, 'operation_lock'), patch.object(update, 'version_of', return_value='alpha-lab'):
            updater = self.updater(temporary)
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(b'existing')
            with patch.object(update, 'fetch', return_value=json.dumps(self.release()).encode()): updater._check()
            with patch.object(update, 'fetch') as network:
                with self.assertRaisesRegex(ValueError, 'INSTALLED_VERSION_UNKNOWN'): updater.update()
                network.assert_not_called()
            self.assertEqual(updater.binary.read_bytes(), b'existing')

    def test_same_version_offline_switches_variant_but_identical_data_never_restarts(self):
        data = self.elf('v3'); compressed = gzip.compress(data)
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
             patch.object(update, 'operation_lock'), patch.object(update, 'version_of', return_value='v1.2.3'):
            updater = self.updater(temporary); archive = Path(temporary) / 'asset.gz'; archive.write_bytes(compressed)
            updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(self.elf('v1'))
            with patch.object(updater, 'validate_config'), patch.object(updater, 'restart_and_check') as health:
                self.assertEqual(updater.install_local(archive, hashlib.sha256(compressed).hexdigest(), 'v1.2.3')['status'], 'updated')
                health.assert_called_once()
            self.assertEqual(updater.previous.read_bytes(), self.elf('v1'))
            with patch.object(updater, 'validate_config') as config, patch.object(updater, 'restart_and_check') as health:
                self.assertEqual(updater.install_local(archive, hashlib.sha256(compressed).hexdigest(), 'v1.2.3')['reason'], 'identical_binary')
                health.assert_not_called(); config.assert_not_called()

    def test_candidate_that_cannot_run_never_replaces_current_binary(self):
        data = self.elf('v3'); compressed = gzip.compress(data)
        with tempfile.TemporaryDirectory() as temporary, patch.object(update, 'cpu_capabilities', return_value=self.capabilities()), \
             patch.object(update, 'operation_lock'):
            updater = self.updater(temporary); updater.binary.parent.mkdir(parents=True); updater.binary.write_bytes(b'old')
            release = update.release_asset(self.release(compressed))
            with patch.object(update, 'version_of', return_value=None), patch.object(updater, 'restart_and_check') as health:
                with self.assertRaisesRegex(ValueError, 'VERSION_MISMATCH'): updater._install(release, compressed)
                health.assert_not_called()
            self.assertEqual(updater.binary.read_bytes(), b'old')

    def test_prerelease_version_is_not_truncated_into_a_comparable_stable_tag(self):
        with patch.object(update.subprocess, 'run') as run:
            run.return_value.stdout = 'Mihomo Meta v1.2.3-alpha-lab linux amd64 with go1.26.10\n'
            version = update.version_of(Path('/fixture/core'))
            self.assertEqual(version, 'v1.2.3-alpha-lab')
            self.assertIsNone(update.stable_version(version))

    def test_scheduled_maintenance_does_not_switch_same_version_variant(self):
        import maintenance
        from manage_common import Layout
        with tempfile.TemporaryDirectory() as temporary, patch.object(maintenance, 'STATE', Path(temporary) / 'state'), \
             patch.object(maintenance, 'Updater') as updater, patch.object(maintenance, 'Path') as config_path, \
             patch.object(sys, 'argv', ['maintenance.py', 'scheduled']):
            config_path.return_value.read_bytes.return_value = b'{"auto_update":true}'
            updater.return_value.status.return_value = {'update_available': False, 'variant_change_available': True}
            maintenance.main(Layout(temporary))
            updater.return_value.check.assert_called_once()
            updater.return_value.update.assert_not_called()
