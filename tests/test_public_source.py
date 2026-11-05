"""Exercise the publication guard against real, disposable Git histories."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        shutil.copyfile(ROOT / 'scripts/check-public-source.py', self.root / 'scripts/check-public-source.py')
        self.git('init', '-q')
        self.git('add', 'scripts/check-public-source.py')
        self.git('commit', '-qm', 'clean baseline')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + self.root.as_posix(),
            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com', '-C', str(self.root), *args])

    def check(self, pre_push=False, sha=None):
        return subprocess.run([sys.executable, str(self.root / 'scripts/check-public-source.py'),
                               *(['--pre-push'] if pre_push else [])],
            input=f'refs/heads/main {sha or self.git("rev-parse", "HEAD").decode().strip()} refs/heads/main {"0" * 40}\n',
            capture_output=True, text=True, cwd=self.root)

    def test_clean_source_passes_for_ci_and_push(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.check(pre_push=True).returncode, 0)

    def test_deleted_deployment_record_still_blocks_history_push(self):
        record = self.root / 'docs/validation/synthetic.md'
        record.parent.mkdir(parents=True); record.write_text('synthetic deployment record')
        self.git('add', 'docs'); self.git('commit', '-qm', 'synthetic record')
        self.git('rm', '-q', 'docs/validation/synthetic.md'); self.git('commit', '-qm', 'delete current file')
        for pre_push in (False, True):
            with self.subTest(pre_push=pre_push):
                result = self.check(pre_push=pre_push)
                self.assertEqual(result.returncode, 1)
                self.assertIn('reachable history', result.stderr)

    def assert_private_copy_is_rejected(self, private):
        for deleted in (False, True):
            if deleted:
                self.git('rm', '-qr', private)
                self.git('commit', '-qm', 'delete private copy')
            for pre_push in (False, True):
                with self.subTest(deleted=deleted, pre_push=pre_push):
                    result = self.check(pre_push=pre_push)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('reachable history', result.stderr)

    def test_reused_blob_does_not_hide_private_paths(self):
        public = self.root / 'docs/example.md'
        public.parent.mkdir(); public.write_text('synthetic duplicate contents')
        self.git('add', 'docs'); self.git('commit', '-qm', 'public example')
        private = self.root / 'docs/validation/synthetic.md'
        private.parent.mkdir(); shutil.copyfile(public, private)
        self.git('add', 'docs'); self.git('commit', '-qm', 'private copy of existing blob')
        self.assert_private_copy_is_rejected('docs/validation/synthetic.md')

    def test_reused_subtree_does_not_hide_private_prefixes(self):
        public = self.root / 'docs/examples'
        public.mkdir(parents=True)
        (public / 'one.md').write_text('first synthetic example')
        (public / 'two.md').write_text('second synthetic example')
        self.git('add', 'docs'); self.git('commit', '-qm', 'public examples')
        shutil.copytree(public, self.root / 'docs/validation')
        self.git('add', 'docs'); self.git('commit', '-qm', 'private copy of existing tree')
        tree = self.git('rev-parse', 'HEAD^{tree}').decode().strip()
        self.git('tag', '-a', 'private-tree', '-m', 'synthetic tree tag', tree)
        tag = self.git('rev-parse', 'private-tree').decode().strip()
        for target in (tree, tag):
            with self.subTest(target=target):
                result = self.check(pre_push=True, sha=target)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('reachable history', result.stderr)
        self.assert_private_copy_is_rejected('docs/validation')

    def test_staged_runtime_credential_is_rejected_before_commit(self):
        path = self.root / 'etc/paopaogw/read-token'
        path.parent.mkdir(parents=True); path.write_text('synthetic-credential-only')
        self.git('add', 'etc')
        self.assertEqual(self.check().returncode, 1)


if __name__ == '__main__': unittest.main()
