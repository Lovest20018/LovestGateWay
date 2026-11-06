"""Exercise actual Git history and archives without network or release credentials."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


upstream = module('check-upstream')
release = module('release-assets')


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-q', '--initial-branch=main')
        self.git('config', 'user.name', 'Release Tests')
        self.git('config', 'user.email', 'release-tests@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        (self.root / 'LICENSE').write_bytes(b'Test fixture only\n')
        self.base = self.commit('initial fixture')
        self.ledger = {'schema': 1, 'repository': 'kkkgo/PaoPaoGateWay', 'branch': 'main',
                       'merged_base': self.base, 'reviewed_head': self.base, 'reviewed_release': 'v1.0.0'}

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + self.root.as_posix(),
                                        '-C', str(self.root), *args], stderr=subprocess.PIPE)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD').decode().strip()


class UpstreamTests(RepositoryTests):
    def test_no_changes_and_release_only_update(self):
        self.assertFalse(upstream.inspect(self.root, self.ledger, 'HEAD', 'v1.0.0')['pending'])
        changed = upstream.inspect(self.root, self.ledger, 'HEAD', 'v1.0.1')
        self.assertTrue(changed['pending'])
        self.assertEqual(changed['commits'], [])
        self.assertFalse(changed['history_rewritten'])

    def test_new_commit_report_escapes_untrusted_subjects(self):
        (self.root / 'example.txt').write_text('new content', encoding='utf-8')
        head = self.commit('<img> @someone [link](https://example.invalid) `code` | $(command)')
        value = upstream.inspect(self.root, self.ledger, 'HEAD', 'v1.0.0')
        self.assertTrue(value['pending'])
        self.assertEqual(value['commits'][0]['sha'], head)
        self.assertEqual(value['changed_files'], ['example.txt'])
        report = upstream.markdown(value)
        for raw in ('<img>', '@someone', '[link]', '`code`', '| $(command)'):
            self.assertNotIn(raw, report)

    def test_rewritten_history_does_not_advance_reviewed_head(self):
        (self.root / 'new.txt').write_text('change', encoding='utf-8')
        reviewed = self.commit('reviewed update')
        self.ledger['reviewed_head'] = reviewed
        value = upstream.inspect(self.root, self.ledger, self.base, 'v1.0.0')
        self.assertTrue(value['history_rewritten'])
        self.assertTrue(value['pending'])
        self.assertEqual(value['reviewed_head'], reviewed)
        self.assertEqual(value['commits'], [])

    def test_missing_baseline_and_invalid_release_fail_closed(self):
        with self.assertRaises(ValueError):
            upstream.inspect(self.root, self.ledger, 'HEAD', '../../untrusted')
        self.ledger['reviewed_head'] = '0' * 40
        with self.assertRaises(subprocess.CalledProcessError):
            upstream.inspect(self.root, self.ledger, 'HEAD', 'v1.0.0')

    def test_ledger_rejects_other_repository_or_invalid_sha(self):
        for changes in ({'repository': 'other/repo'}, {'reviewed_head': 'HEAD'}, {'reviewed_release': 'x\nvalue'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                upstream.validate_ledger({**self.ledger, **changes})


class ReleaseTests(RepositoryTests):
    version = '1.2.3-test.1'

    def setUp(self):
        super().setUp()
        patcher = patch.object(release, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / '.github').mkdir()
        self.ledger_path = self.root / '.github/upstream.json'
        self.ledger_path.write_text(json.dumps(self.ledger), encoding='utf-8')
        self.commit_id = self.commit('record baseline')
        self.directory = self.root / 'dist'
        self.directory.mkdir()
        self.source = self.directory / f'lovestgateway-{self.version}-source.tar.gz'
        self.package = self.directory / f'lovestgateway-{self.version}-debian-amd64.tar.gz'
        self.export()
        self.source_sha = release.verify_source(self.source, self.commit_id, self.version)
        self.make_package()

    def export(self, prefixed=True):
        args = ['archive', '--format=tar.gz', '--output=' + str(self.source)]
        if prefixed: args.append('--prefix=lovestgateway-' + self.version + '-source/')
        self.git(*args, self.commit_id)
        self.checksum(self.source)

    def checksum(self, path):
        path.with_suffix('.gz.sha256').write_text(f'{release.file_sha(path)}  {path.name}\n', encoding='ascii')

    def tar(self, path, files, extra=None):
        with tarfile.open(path, 'w:gz') as archive:
            for name, raw in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
            if extra is not None: archive.addfile(extra)
        self.checksum(path)

    def make_package(self, **changes):
        build = {'architecture': 'amd64', 'version': self.version,
                 'build_commit': self.commit_id, 'source_sha256': self.source_sha, **changes}
        files = {'install.py': b'# fixture, never executed\n',
                 'lib/bin/sniffbox': b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00',
                 'lib/build.json': json.dumps(build).encode()}
        files['manifest.json'] = json.dumps({name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}).encode()
        self.tar(self.package, {'release/' + name: raw for name, raw in files.items()})

    def validate(self):
        return release.validate(self.directory, self.version, self.commit_id, self.ledger_path)

    def test_valid_bundle_uses_baseline_at_build_commit(self):
        self.ledger_path.write_text(json.dumps({**self.ledger, 'reviewed_head': 'a' * 40}), encoding='utf-8')
        result = self.validate()
        self.assertEqual(result['commit'], self.commit_id)
        self.assertEqual(result['upstream']['reviewed_head'], self.base)
        self.assertEqual(result['source_export'], 'git-archive')
        self.assertEqual(len(result['assets']), 4)

    def test_root_archive_is_also_exact_source(self):
        self.export(prefixed=False)
        self.assertEqual(self.validate()['source_manifest_sha256'], self.source_sha)

    def test_online_installer_is_required_and_bound_to_release_commit(self):
        installer = b'#!/bin/sh\necho fixture\n'
        (self.root / 'install.sh').write_bytes(installer)
        self.commit_id = self.commit('add online installer')
        self.export()
        self.source_sha = release.verify_source(self.source, self.commit_id, self.version)
        self.make_package()
        with self.assertRaisesRegex(ValueError, 'Online installer'): self.validate()
        (self.directory / 'install.sh').write_bytes(installer)
        self.assertIn('install.sh', self.validate()['assets'])
        (self.directory / 'install.sh').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Online installer'): self.validate()

    def test_rejects_external_checksum_mismatch(self):
        self.source.with_suffix('.gz.sha256').write_text('0' * 64 + '  ' + self.source.name, encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'external checksum'):
            self.validate()

    def test_rejects_wrong_package_identity_or_source_digest(self):
        for changes in ({'version': 'other'}, {'build_commit': self.base}, {'source_sha256': '0' * 64}):
            with self.subTest(changes=changes):
                self.make_package(**changes)
                with self.assertRaises(ValueError): self.validate()

    def test_rejects_missing_extra_or_changed_source_files(self):
        files = {'LICENSE': b'Test fixture only\n', '.github/upstream.json': self.ledger_path.read_bytes()}
        for modified in ({'LICENSE': files['LICENSE']}, {**files, 'extra.txt': b'untracked'}, {**files, 'LICENSE': b'changed'}):
            with self.subTest(files=list(modified)):
                self.tar(self.source, modified)
                with self.assertRaisesRegex(ValueError, 'does not match'): self.validate()

    def test_rejects_source_traversal_and_links(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('LICENSE', tarfile.SYMTYPE)]:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = '/outside'
            self.tar(self.source, {}, extra=member)
            with self.assertRaises(ValueError): self.validate()

    def test_first_release_checkout_line_endings_are_explicit_and_bounded(self):
        self.commit_id = self.base
        self.ledger_path.write_text(json.dumps({**self.ledger, 'initial_release_commit': self.base}), encoding='utf-8')
        self.tar(self.source, {'LICENSE': b'Test fixture only\r\n'})
        with self.assertRaisesRegex(ValueError, 'does not match'):
            release.verify_source(self.source, self.commit_id, self.version)
        self.source_sha = release.verify_source(self.source, self.commit_id, self.version, normalize_lf=True)
        self.make_package()
        self.assertEqual(self.validate()['source_export'], 'git-tree-checkout-crlf')
        self.tar(self.source, {'LICENSE': b'Different contents\r\n'})
        with self.assertRaisesRegex(ValueError, 'does not match'): self.validate()

    def test_other_commit_cannot_borrow_current_baseline(self):
        self.commit_id = self.base
        with self.assertRaisesRegex(ValueError, 'no recorded upstream baseline'): self.validate()


if __name__ == '__main__': unittest.main()
