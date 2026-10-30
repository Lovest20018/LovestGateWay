from pathlib import Path
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
import time
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

    @unittest.skipUnless(os.name == 'posix', 'Debian installer FD inheritance requires POSIX')
    def test_orphan_installer_blocks_recovery_until_its_last_write(self):
        from manage_backup import BackupStore
        from manage_common import write_json
        from manage_config import JOURNAL
        layout = Layout(self.base / 'root'); system = FakeSystem()
        system.stop_all = lambda: None
        installed = layout.path('usr/local/lib/paopaogw/build.json')
        installed.parent.mkdir(parents=True); installed.write_bytes(b'old')
        backup = BackupStore(layout).create(['usr/local/lib/paopaogw/build.json'])
        write_json(layout, JOURNAL, {'phase': 'installing', 'backup': backup, 'services': system.states,
                   'expected': {'usr/local/lib/paopaogw/build.json': [hashlib.sha256(x).hexdigest() for x in (b'old', b'new')]}})
        package = self.base / 'package'; package.mkdir()
        (package / 'install.py').write_text("""import argparse,os,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root');root=Path(p.parse_args().root)
(root/'child.pid').write_text(str(os.getpid()))
deadline=time.monotonic()+15
while not (root/'release-child').exists():
 if time.monotonic()>deadline:raise SystemExit(2)
 time.sleep(.01)
(root/'usr/local/lib/paopaogw/build.json').write_bytes(b'new')
(root/'child-finished').touch()
""")
        script = """import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from manage_common import Layout,operation_lock
from manage_deploy import Deployer
layout=Layout(sys.argv[2])
with operation_lock(layout):Deployer(layout).install(SimpleNamespace(path=Path(sys.argv[3])))
"""
        parent = subprocess.Popen([sys.executable, '-B', '-c', script,
                str(Path(__file__).resolve().parents[1] / 'integration'), str(layout.root), str(package)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child = None
        def until(predicate):
            deadline = time.monotonic() + 10
            while not predicate():
                if time.monotonic() > deadline: self.fail('installer synchronization timed out')
                time.sleep(.01)
        try:
            until(lambda: (layout.root / 'child.pid').exists())
            child = int((layout.root / 'child.pid').read_text())
            parent.kill(); parent.wait(timeout=5)
            busy = False
            try: Deployer(layout, system).recover()
            except ManagementError as error:
                self.assertEqual(str(error), 'MANAGEMENT_BUSY'); busy = True
            (layout.root / 'release-child').touch()
            until(lambda: (layout.root / 'child-finished').exists())
            # Finished marker precedes process exit; retry only a still-held installer lock.
            deadline = time.monotonic() + 5
            while True:
                try: Deployer(layout, system).recover(); break
                except ManagementError as error:
                    if str(error) != 'MANAGEMENT_BUSY' or time.monotonic() > deadline: raise
                    time.sleep(.01)
            self.assertTrue(busy, 'recovery ran concurrently with the orphan installer')
            self.assertEqual(installed.read_bytes(), b'old')
        finally:
            if parent.poll() is None: parent.kill(); parent.wait()
            if child:
                try: os.kill(child, signal.SIGKILL)
                except ProcessLookupError: pass


if __name__ == '__main__': unittest.main()
