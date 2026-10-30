from pathlib import Path
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manage_common import Layout, ManagementError
from manage_deploy import verify_package, Deployer
from test_manage_config import FakeSystem


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
    def package(self, extra=None, corrupt=False):
        files = {'install.py': b'print("installer")', 'lib/bin/sniffbox': b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00' + bytes(12),
                 'lib/build.json': json.dumps({'architecture': 'amd64', 'version': 'test'}).encode()}
        manifest = {k: hashlib.sha256(v).hexdigest() for k,v in files.items()}
        if corrupt: files['install.py'] += b'x'
        files['manifest.json'] = json.dumps(manifest).encode()
        path = self.base / 'release.tar.gz'
        with tarfile.open(path, 'w:gz') as tar:
            for name, raw in files.items():
                member = tarfile.TarInfo('release/' + name); member.size = len(raw); tar.addfile(member, io.BytesIO(raw))
            if extra: tar.addfile(extra)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()
    def test_verified_package_and_outer_digest(self):
        path, sha = self.package()
        with verify_package(path, sha) as package: self.assertEqual(package.build['version'], 'test')
        with self.assertRaises(ManagementError):
            with verify_package(path, '0'*64): pass
    def test_manifest_and_path_link_extra_rejections(self):
        path, sha = self.package(corrupt=True)
        with self.assertRaises(ManagementError):
            with verify_package(path, sha): pass
        for name, kind in [('release/../../escape', tarfile.REGTYPE), ('release/lib/evil', tarfile.SYMTYPE), ('release/extra', tarfile.REGTYPE)]:
            member = tarfile.TarInfo(name); member.type = kind; member.linkname = '/etc/passwd'
            path, sha = self.package(member)
            with self.assertRaises(ManagementError):
                with verify_package(path, sha): pass
    def test_failed_upgrade_removes_new_files_and_restores_old_identity(self):
        layout = Layout(self.base / 'root'); system = FakeSystem()
        system.stop_all = lambda: system.calls.append('stop')
        system.wait = lambda *args: None
        config = layout.path('etc/paopaogw/instance-id'); config.parent.mkdir(parents=True); config.write_text('old-id')
        path, sha = self.package()
        def install(package):
            new = layout.path('usr/local/lib/paopaogw/build.json'); new.parent.mkdir(parents=True); new.write_bytes((package.path / 'lib/build.json').read_bytes())
            raise ManagementError('INJECTED')
        deployer = Deployer(layout, system, installer=install)
        with self.assertRaises(ManagementError): deployer.deploy(path, sha)
        self.assertEqual(config.read_text(), 'old-id')
        self.assertFalse(layout.path('usr/local/lib/paopaogw/build.json').exists())
        self.assertIn('restore', system.calls)

    def test_failed_upgrade_preserves_external_edits_and_reports_conflict(self):
        layout = Layout(self.base / 'root'); system = FakeSystem()
        system.stop_all = lambda: None; system.wait = lambda *args: None
        config = layout.path('etc/paopaogw/instance-id'); config.parent.mkdir(parents=True); config.write_text('old-id')
        path, sha = self.package()
        def external(package):
            config.write_text('external-id'); raise ManagementError('INJECTED')
        with self.assertRaises(ManagementError): Deployer(layout, system, installer=external).deploy(path, sha)
        self.assertEqual(config.read_text(), 'external-id')
        journal = json.loads(layout.path('var/lib/paopaogw/admin/transaction.json').read_bytes())
        self.assertEqual(journal['phase'], 'conflict')


if __name__ == '__main__': unittest.main()
