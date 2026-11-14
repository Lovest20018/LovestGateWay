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
    def test_routine_logs_are_quiet_but_warnings_and_verbose_diagnostics_remain(self):
        source=(ROOT/'FILES/usr/bin/ppg.sh').read_text(encoding='utf-8')
        helper=source[source.index('log() {'):source.index('read_dns() {')]
        script='set -e\nlog_ts() { log_hms=120000; }\nppgwecho() { printf "%s\\n" "$1"; }\n'+helper+r'''
PPGW_DEBIAN=1
mode=suburl
log 'routine hash' debug
log 'failure preserved' warn
PPGW_LOG_VERBOSE=1
log 'verbose enabled' debug
unset PPGW_LOG_VERBOSE
if [ -r /proc/uptime ]; then
    log_heartbeat
    log_heartbeat
    heartbeat_last=0
    log_heartbeat
fi
'''
        result=subprocess.run([SHELL,'-s'],input=script,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('routine hash',result.stdout)
        self.assertIn('failure preserved',result.stdout)
        self.assertIn('verbose enabled',result.stdout)
        self.assertLessEqual(result.stdout.count('Controller polling;'),2)

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
