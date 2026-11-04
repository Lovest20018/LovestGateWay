"""Exercise the actual controller handshake with files; no services or network."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which('sh')
if not SHELL and os.name == 'nt' and (git := shutil.which('git')):
    candidate = Path(git).parent.parent / 'usr/bin/sh.exe'
    if candidate.is_file(): SHELL = str(candidate)


class CoreReloadShellTests(unittest.TestCase):
    @unittest.skipUnless(SHELL, 'requires a POSIX shell')
    def test_pending_target_and_accepted_snapshot_gate_the_polling_controller(self):
        helper = (ROOT / 'deploy/debian/runtime.sh').read_text(encoding='utf-8')
        helper = helper[helper.index('prepare_core_reload()'):]
        helper = helper.replace('/www/ppgw.ini', 'persistent.ini').replace('/tmp/', './')
        script = r'''
set -eu
ppgw() { [ "$1" = -ini-export ]; cat "$2"; }
md5sum() {
    "$PPGW_PYTHON" -c 'import hashlib,sys;print(hashlib.md5(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
}
''' + helper + r'''
SNIFFBOX_SNAPSHOT=running.ini
hash=old
last_hash=old
expect_rc() {
    actual=0
    accept_core_reload || actual=$?
    [ "$actual" = "$1" ]
}
expect_rc 1
printf 'pending\n' >ppgw-core-only.hash
expect_rc 2
[ "$last_hash" = old ]
printf 'max_rec=9000\n' >persistent.ini
printf 'max_rec=5000\n' >running.ini
prepare_core_reload
hash=$(cat ppgw-core-only.hash)
expect_rc 2
[ "$last_hash" = old ]
cp persistent.ini running.ini
expect_rc 0
[ "$last_hash" = "$hash" ]
[ ! -f ppgw-core-only.hash ]
hash=external_change
expect_rc 1
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = {**os.environ, 'PPGW_PYTHON': Path(sys.executable).as_posix()}
            environment['PATH'] = str(Path(SHELL).parent) + os.pathsep + environment.get('PATH', '')
            result = subprocess.run([SHELL, '-s'], input=script, text=True, cwd=temporary,
                                    env=environment,
                                    capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
