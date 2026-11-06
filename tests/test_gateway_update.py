import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout, ManagementError, write_json
from manage_gateway_update import GatewayUpdate, BASE, BUILD, JOURNAL
import test_online_install


class GatewayUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.layout = Layout(temporary.name)
        write_json(self.layout, BUILD, {'version': '0.2.0-management.20261106.3', 'build_commit': 'b'*40, 'architecture': 'amd64'})
        bundle = test_online_install.OnlineInstallTests(); bundle.setUp(); self.addCleanup(bundle.doCleanups)
        self.raw, self.manifest = bundle.bundle()
        self.starts, self.downloads, self.installs = [], [], []
        self.updater = GatewayUpdate(self.layout, self.starts.append, self.fetch, self.install)

    def fetch(self, url, destination, limit):
        self.downloads.append(url)
        raw = json.dumps(self.manifest).encode() if url.endswith('.json') else self.raw
        destination.write_bytes(raw)
        return raw

    def install(self, package, archive, candidate):
        self.installs.append(package)
        self.assertTrue((package / 'manifest.json').is_file())
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), next(iter(candidate['manifest']['assets'].values()))['sha256'])
        write_json(self.layout, BUILD, {'version': candidate['version'], 'build_commit': candidate['commit'], 'architecture': 'amd64'})
        write_json(self.layout, JOURNAL, {'phase': 'complete', 'backup': '20261106120000-abcdef012345'})

    def check(self):
        self.updater.submit('check', {'id': '1'*32}); self.updater.run('1'*32)
        return self.updater.status()['candidate']['token']

    def test_check_only_downloads_metadata_and_returns_notes_and_pinned_identity(self):
        self.check(); data = self.updater.status()
        self.assertTrue(data['available']); self.assertEqual(len(self.downloads), 1); self.assertFalse(self.installs)
        self.assertNotIn('manifest', data['candidate'])
        self.assertEqual(data['candidate']['release_url'], 'https://github.com/Lovest20018/LovestGateWay/releases/tag/v0.2.1')
        self.assertNotIn('request', data['operation'])

    def test_update_pins_checked_release_survives_service_recreation_and_is_idempotent(self):
        token = self.check(); request = {'id': '2'*32, 'candidate': token}
        first = self.updater.submit('update', request)
        self.assertEqual(first, self.updater.submit('update', request))
        self.assertEqual(self.starts, ['1'*32, '2'*32])
        restored = GatewayUpdate(self.layout, self.starts.append, self.fetch, self.install)
        restored.run('2'*32); restored.run('2'*32)
        self.assertEqual(len(self.installs), 1)
        result = restored.status('2'*32)
        self.assertEqual(result['operation']['status'], 'succeeded')
        self.assertEqual(result['operation']['backup'], '20261106120000-abcdef012345')
        self.assertFalse(result['available'])
        self.assertIn('/download/v0.2.1/', self.downloads[-1])

    def test_rejects_arbitrary_urls_stale_tokens_and_reused_ids(self):
        token = self.check()
        for body in ({'id':'2'*32, 'candidate':'0'*64}, {'id':'../evil', 'candidate':token},
                     {'id':'2'*32, 'candidate':token, 'url':'https://evil.invalid'}):
            with self.assertRaises(ManagementError): self.updater.submit('update', body)
        with self.assertRaisesRegex(ManagementError, 'JOB_ID_REUSED'):
            self.updater.submit('update', {'id':'1'*32, 'candidate':token})
        self.assertEqual(len(self.starts), 1)

    def test_concurrent_jobs_and_recovery_block_new_updates(self):
        self.updater.submit('check', {'id':'1'*32})
        with self.assertRaisesRegex(ManagementError, 'MANAGEMENT_BUSY'): self.updater.submit('check', {'id':'2'*32})
        self.updater.run('1'*32)
        write_json(self.layout, JOURNAL, {'phase':'installing'})
        with self.assertRaisesRegex(ManagementError, 'RECOVERY_REQUIRED'): self.updater.submit('check', {'id':'2'*32})

    def test_expired_and_changed_base_never_install(self):
        token = self.check()
        self.updater.submit('update', {'id':'2'*32, 'candidate':token})
        write_json(self.layout, BUILD, {'version':'0.3.0', 'build_commit':'c'*40, 'architecture':'amd64'})
        self.updater.run('2'*32)
        self.assertEqual(self.updater.status()['operation']['error'], 'GATEWAY_UPDATE_CHECK_REQUIRED')
        self.assertFalse(self.installs)
        with patch('manage_gateway_update.time.time', return_value=time.time()+1900):
            self.assertFalse(self.updater.status()['available'])

    def test_downgrade_same_version_and_non_amd64_are_unavailable(self):
        self.check()
        for version, arch in [('0.2.1','amd64'), ('0.3.0','amd64'), ('dev-abc','amd64'), ('0.2.0','arm64')]:
            write_json(self.layout, BUILD, {'version':version, 'architecture':arch, 'build_commit':'b'*40})
            self.assertFalse(self.updater.status()['available'])

    def test_corrupt_download_does_not_execute_installer(self):
        token = self.check(); self.raw += b'tampered'
        self.updater.submit('update', {'id':'2'*32, 'candidate':token}); self.updater.run('2'*32)
        self.assertEqual(self.updater.status()['operation']['error'], 'GATEWAY_RELEASE_INVALID')
        self.assertFalse(self.installs)

    def test_download_and_install_failures_are_durable_and_redacted(self):
        token = self.check()
        self.updater.fetch = Mock(side_effect=OSError('private-network-details'))
        self.updater.submit('update', {'id':'2'*32, 'candidate':token}); self.updater.run('2'*32)
        self.assertEqual(self.updater.status()['operation']['error'], 'GATEWAY_DOWNLOAD_FAILED')
        self.updater.fetch = self.fetch
        self.updater.install = Mock(side_effect=ManagementError('DEPLOY_FAILED_ROLLED_BACK'))
        self.updater.submit('update', {'id':'3'*32, 'candidate':token}); self.updater.run('3'*32)
        result = self.updater.status()
        self.assertEqual(result['operation']['error'], 'DEPLOY_FAILED_ROLLED_BACK')
        self.assertNotIn('private', json.dumps(result))

    def test_failed_check_clears_old_candidate_and_never_installs(self):
        self.check(); self.updater.fetch = Mock(side_effect=OSError())
        self.updater.submit('check', {'id':'2'*32}); self.updater.run('2'*32)
        self.assertIsNone(self.updater.status()['candidate']); self.assertFalse(self.installs)

    def test_dead_worker_is_interrupted_without_replay(self):
        self.updater.submit('check', {'id':'1'*32})
        job = self.updater.read('1'*32 + '.json'); job.update(status='running', execution={'pid':123})
        self.updater.save(job)
        with patch.object(Layout, 'live', new_callable=lambda: property(lambda _: True)), patch('manage_gateway_update.alive', return_value=False):
            self.assertEqual(self.updater.status()['operation']['status'], 'interrupted')
        self.assertEqual(self.starts, ['1'*32]); self.assertFalse(self.downloads)

    def test_start_failure_is_not_silently_retried(self):
        self.updater.start = Mock(side_effect=OSError())
        self.assertEqual(self.updater.submit('check', {'id':'1'*32})['error'], 'GATEWAY_UPDATE_START_FAILED')
        self.updater.submit('check', {'id':'1'*32})
        self.updater.start.assert_called_once()

    def test_shell_installer_embeds_same_verified_downloader(self):
        root = Path(__file__).resolve().parents[1]
        embedded = (root/'install.sh').read_text(encoding='utf-8').split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
        self.assertEqual(embedded.strip(), (root/'integration/manage_release.py').read_text(encoding='utf-8').strip())

    def test_real_staged_install_transaction_preserves_config_and_rolls_back_failure(self):
        # Real subprocess/installer/backup path in an isolated root; no running services.
        from manage_release import unpack
        from manage_model import settings_revision
        root = Path(__file__).resolve().parents[1]
        work = self.layout.root / 'release'; work.mkdir()
        binary = work / 'sniffbox'
        binary.write_bytes(b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00' + bytes(12))
        subprocess.run([sys.executable, str(root/'scripts/package-debian.py'), '--binary', str(binary),
                        '--version','0.2.2','--commit','c'*40,'--source-sha256','d'*64,
                        '--arch','amd64','--output',str(work)], check=True, capture_output=True)
        archive = work/'lovestgateway-0.2.2-debian-amd64.tar.gz'
        self.raw = archive.read_bytes()
        self.manifest = {'schema':1,'version':'0.2.2','tag':'v0.2.2','commit':'c'*40,'architecture':'amd64',
                         'assets':{archive.name:{'bytes':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest()}}}
        package = work/'package'; unpack(self.raw, package, self.manifest)
        subprocess.run([sys.executable,'-B',str(package/'install.py'),'--root',str(self.layout.root)], check=True,capture_output=True)
        old = {'version':'0.2.1','build_commit':'b'*40,'architecture':'amd64'}
        write_json(self.layout, BUILD, old)
        before = settings_revision(self.layout)
        self.updater.install = self.updater.install_package
        token = self.check()
        self.updater.submit('update',{'id':'2'*32,'candidate':token}); self.updater.run('2'*32)
        self.assertEqual(self.updater.status()['operation']['status'],'succeeded')
        self.assertEqual(settings_revision(self.layout),before)
        self.assertTrue(self.updater.status()['operation']['backup'])
        self.assertTrue(self.layout.path('etc/systemd/system/paopaogw-gateway-update@.service').is_file())
        # A valid test release whose live installation fails after writing must restore the snapshot.
        write_json(self.layout, BUILD, old)
        installer = package/'install.py'
        installer.write_text(installer.read_text(encoding='utf-8') + '\nif "--root" in sys.argv and sys.argv[sys.argv.index("--root")+1] == '+repr(str(self.layout.root))+': raise SystemExit(1)\n',encoding='utf-8')
        checksums = {p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in package.rglob('*') if p.is_file() and p != package/'manifest.json'}
        (package/'manifest.json').write_text(json.dumps(checksums))
        with tarfile.open(archive,'w:gz') as tar: tar.add(package,arcname='lovestgateway-0.2.2-debian-amd64')
        self.raw=archive.read_bytes()
        self.manifest['assets'][archive.name]={'bytes':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest()}
        unpack(self.raw,work/'failure-check',self.manifest)
        self.updater.submit('check',{'id':'3'*32});self.updater.run('3'*32)
        self.updater.submit('update',{'id':'4'*32,'candidate':self.updater.status()['candidate']['token']});self.updater.run('4'*32)
        self.assertEqual(self.updater.status()['operation']['error'],'DEPLOY_FAILED_ROLLED_BACK')
        self.assertEqual(self.updater.installed(),old)
        self.assertEqual(settings_revision(self.layout),before)


if __name__ == '__main__': unittest.main()
