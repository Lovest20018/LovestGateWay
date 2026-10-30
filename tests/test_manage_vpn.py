from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from manage_assets import validate_ovpn


@unittest.skipUnless(shutil.which('sh'), 'controller execution requires POSIX sh')
class VpnControllerTests(unittest.TestCase):
    def test_comments_cannot_disable_route_protection(self):
        source = (ROOT / 'FILES/usr/bin/ppg.sh').read_text()
        start = source.index('load_ovpn() {')
        function = source[start:source.index('\n}', start) + 2]
        for comment in ('# route-nopull', '; route-nopull', '# previously used route-nopull'):
            with self.subTest(comment=comment), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config = 'client\nremote 192.0.2.1 1194\n' + comment + '\n'
                validate_ovpn(config)
                (directory / 'ppgw.ovpn.down').write_text(config)
                isolated = function.replace('/tmp/', directory.as_posix() + '/').replace('/etc/hosts', (directory / 'hosts').as_posix())
                stubs = "log() { :; }; ulimit() { echo 1048576; }; load_ini() { :; }; ppgw() { echo 192.0.2.1; }; dnsserver=; dns_ip=; dns_port=; ovpn_username=; "
                subprocess.run(['sh', '-c', stubs + isolated + '\nload_ovpn'], check=True, capture_output=True)
                active = [shlex.split(line, comments=True) for line in (directory / 'paopao.ovpn').read_text().splitlines() if not line.strip().startswith(';')]
                self.assertIn(['route-nopull'], active)


if __name__ == '__main__': unittest.main()
