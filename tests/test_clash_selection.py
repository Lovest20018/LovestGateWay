"""Execute startup decisions; real Mihomo cache coverage lives in verify-selection-preservation.py."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from clash_selection import saved_global, static_choices, wait_restored

SHELL = shutil.which('sh')
if not SHELL and os.name == 'nt' and (git := shutil.which('git')):
    candidate = Path(git).parent.parent / 'usr/bin/sh.exe'
    if candidate.is_file(): SHELL = str(candidate)


class SelectionTests(unittest.TestCase):
    def test_waits_through_missing_global_and_default_until_cached_choice_is_restored(self):
        for expected in ['Saved Node', '香港 🇭🇰', 'DIRECT']:
            default = 'REJECT' if expected == 'DIRECT' else 'DIRECT'
            states = iter([({}, {}), ({'mode': 'rule'}, {}),
                           ({'mode': 'global'}, {'type': 'Selector', 'now': default, 'all': [default, expected]}),
                           ({'mode': 'global'}, {'type': 'Selector', 'now': expected, 'all': [default, expected]})])
            elapsed = [0]
            calls = []
            def read(remaining):
                calls.append(remaining)
                return next(states)
            def sleep(seconds): elapsed[0] += seconds
            self.assertEqual(wait_restored(expected, read, clock=lambda: elapsed[0], sleep=sleep), 'restored')
            self.assertEqual(len(calls), 4)

    def test_only_initialized_global_members_can_confirm_removal(self):
        self.assertEqual(wait_restored('Removed Node', lambda _: (
            {'mode': 'global'}, {'type': 'Selector', 'now': 'DIRECT', 'all': ['DIRECT', 'REJECT']}),
            candidate_choices={'DIRECT', 'REJECT'}), 'removed')

    def test_dynamic_or_unreadable_candidates_cannot_prove_node_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'candidate.yaml'
            self.assertIsNone(static_choices(path))
            path.write_text('proxies: [{name: Saved Node}]\nproxy-groups: []\n')
            self.assertIn('Saved Node', static_choices(path))
            path.write_text('proxy-providers: {fixture: {type: file, path: fixture.yaml}}\n')
            self.assertIsNone(static_choices(path))
            path.write_text('proxy-groups: [{name: Group, type: select, use: [fixture]}]\n')
            self.assertIsNone(static_choices(path))

    def test_timeout_and_failed_reads_remain_uncertain_instead_of_reselecting(self):
        for state in [({}, {}), ({'mode': 'global'}, {'type': 'Selector', 'now': 'DIRECT', 'all': ['DIRECT', 'Saved Node']}),
                      ({'mode': 'global'}, {'type': 'Selector', 'now': 'DIRECT', 'all': []}),
                      ({'mode': 'global'}, {'type': 'Selector', 'now': 'DIRECT', 'all': ['DIRECT']}), None]:
            elapsed = [0]
            def read(remaining):
                if state is None: raise OSError('temporary controller failure')
                return state
            def sleep(seconds): elapsed[0] += seconds
            self.assertEqual(wait_restored('Saved Node', read, timeout=.25,
                                          clock=lambda: elapsed[0], sleep=sleep), 'uncertain')
            self.assertAlmostEqual(elapsed[0], .25)

    def test_missing_empty_truncated_and_unknown_cache_are_not_saved_choices(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cache.db'
            self.assertEqual(saved_global(path), '')
            for raw in (b'', b'GLOBAL DIRECT', b'\0' * 65536):
                path.write_bytes(raw)
                self.assertEqual(saved_global(path), '')
                self.assertEqual(path.read_bytes(), raw)

    @unittest.skipUnless(SHELL, 'requires POSIX shell')
    def test_actual_controller_preserves_only_restored_native_choices(self):
        source = (ROOT / 'FILES/usr/bin/ppg.sh').read_text(encoding='utf-8')
        source = source[source.index('load_clash() {'):source.index('\nload_ovpn() {')]
        source = source.replace('/tmp/', './').replace('/etc/hosts', './hosts')
        source = source.replace('/etc/config/clash/', './')
        source = source.replace('/usr/bin/python3 /usr/local/lib/paopaogw/clash_selection.py ./cache.db', 'saved_choice')
        source = source.replace('/usr/bin/python3 /usr/local/lib/paopaogw/clash_selection.py --wait-restored ./clash.sock', 'wait_choice')
        script = r'''
set -e
log() { :; }; ulimit() { echo 1048576; }; load_ini() { :; }; sync_ntp() { :; }
clash() { :; }; kill_clash() { echo killed >>calls; }
pidof() { [ -f started ]; }
saved_choice() { printf '%s' "$SAVED"; }
wait_choice() { received=$(cat); [ "$received" = "$SAVED" ] || return 9; return "$WAIT_RC"; }
ppgw() {
    case "$1" in
        -clash-up) touch started;;
        -now_node) printf '%s' "$CURRENT";;
        -fastnode) echo fastest >>calls;;
        -reload) echo reload >>calls;;
    esac
}
PPGWTTY=/dev/null
dns_ip=127.0.0.1; dns_port=53; mode=yaml; fast_node=yes; fall_direct=no
test_node_url=; ext_node=
PPGW_DEBIAN=1
''' + source + r'''
touch clash.yaml
check() {
    rm -f started calls
    PPGW_DEBIAN="$1"; SAVED="$2"; CURRENT="$3"; WAIT_RC="$5"
    load_clash yes
    actual=0
    if [ -f calls ]; then actual=$(wc -l <calls | tr -d ' '); fi
    [ "$actual" = "$4" ]
}
check 1 'Saved Node' 'Saved Node' 0 0
check 1 DIRECT DIRECT 0 0
check 1 '香港 🇭🇰' '香港 🇭🇰' 0 0
check 1 '' DIRECT 1 3
check 1 'Removed Node' DIRECT 1 3
check 1 'Saved Node' DIRECT 0 4
check 1 'Saved Node' DIRECT 0 1
check 0 'Saved Node' 'Saved Node' 1 0
# Health-triggered reloads retain the existing fastest-node recovery path.
PPGW_DEBIAN=1; CURRENT='Saved Node'; touch started; rm -f calls
load_clash yes
[ "$(cat calls)" = "$(printf 'reload\nfastest')" ]
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = {**os.environ, 'PATH': str(Path(SHELL).parent) + os.pathsep + os.environ.get('PATH', '')}
            result = subprocess.run([SHELL, '-s'], input=script, text=True, encoding='utf-8', cwd=temporary,
                                    env=environment, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
