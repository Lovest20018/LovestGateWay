import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
installer = types.ModuleType('online_installer')
source = (ROOT / 'install.sh').read_text(encoding='utf-8').split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
exec(compile(source, str(ROOT / 'install.sh'), 'exec'), installer.__dict__)


class OnlineInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {'install.sh': b'#!/bin/sh\n', 'install.py': b'# fixture\n', 'deploy.py': b'# fixture\n',
                      'lib/build.json': json.dumps({'version': '0.2.1', 'build_commit': 'a' * 40, 'architecture': 'amd64'}).encode(),
                      'lib/bin/sniffbox': b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00'}

    def bundle(self, extra=None, corrupt=False):
        files = {**self.files, 'manifest.json': json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in self.files.items()}).encode()}
        if corrupt: files['install.py'] += b'changed'
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, raw in files.items():
                item = tarfile.TarInfo('lovestgateway-0.2.1-debian-amd64/' + name)
                item.size = len(raw); archive.addfile(item, io.BytesIO(raw))
            if extra is not None: archive.addfile(extra)
        raw = stream.getvalue()
        manifest = {'schema': 1, 'version': '0.2.1', 'tag': 'v0.2.1', 'commit': 'a' * 40, 'architecture': 'amd64',
                    'assets': {'lovestgateway-0.2.1-debian-amd64.tar.gz': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}
        return raw, manifest

    def test_verified_package_is_extracted_without_running_its_installer(self):
        raw, manifest = self.bundle()
        with patch.object(installer.subprocess, 'Popen') as execute:
            installer.unpack(raw, self.root / 'package', manifest)
        self.assertEqual((self.root / 'package/install.sh').read_bytes(), self.files['install.sh'])
        execute.assert_not_called()

    def test_corrupt_external_or_internal_checksum_writes_nothing(self):
        for internal in (False, True):
            raw, manifest = self.bundle(corrupt=internal)
            if not internal: raw += b'tamper'
            with self.subTest(internal=internal), self.assertRaises(ValueError):
                installer.unpack(raw, self.root / 'package', manifest)
            self.assertFalse((self.root / 'package').exists())

    def test_traversal_links_duplicate_and_wrong_root_are_rejected(self):
        prefix = 'lovestgateway-0.2.1-debian-amd64/'
        for name, kind in [(prefix + '../outside', tarfile.REGTYPE), (prefix + 'link', tarfile.SYMTYPE),
                           (prefix + 'install.py', tarfile.REGTYPE), ('other/extra', tarfile.REGTYPE)]:
            item = tarfile.TarInfo(name); item.type = kind; item.linkname = '/outside'
            raw, manifest = self.bundle(extra=item)
            with self.subTest(name=name), self.assertRaises(ValueError):
                installer.unpack(raw, self.root / 'package', manifest)
            self.assertFalse((self.root / 'package').exists())

    def test_release_identity_cannot_redirect_download_or_install_other_commit(self):
        raw, manifest = self.bundle()
        for change in ({'version': '../other'}, {'tag': 'v0.2.2'}, {'architecture': 'arm64'}, {'commit': 'b' * 40}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                installer.unpack(raw, self.root / 'package', {**manifest, **change})
        self.assertFalse((self.root / 'package').exists())

    def process(self, data, code):
        value = Mock(); value.stdout = io.BytesIO(data); value.wait.return_value = code
        value.__enter__ = Mock(return_value=value); value.__exit__ = Mock(return_value=False)
        return value

    def test_download_tries_private_gateway_then_normal_host_network(self):
        with patch.object(installer.subprocess, 'Popen', side_effect=[self.process(b'', 7), self.process(b'file', 0)]) as run:
            self.assertEqual(installer.download(installer.RELEASES, self.root / 'file', 128), b'file')
        self.assertIn('socks5h://127.0.0.1:1079', run.call_args_list[0].args[0])
        self.assertNotIn('--proxy', run.call_args_list[1].args[0])

    def test_http_failure_and_oversize_do_not_execute_or_retry(self):
        for data, code in [(b'', 22), (b'12345', 0)]:
            process = self.process(data, code)
            with self.subTest(code=code), patch.object(installer.subprocess, 'Popen', return_value=process) as run:
                with self.assertRaises(ValueError): installer.download(installer.RELEASES, self.root / 'file', 4)
                self.assertEqual(run.call_count, 1)
            if data: process.kill.assert_called_once()


if __name__ == '__main__': unittest.main()
