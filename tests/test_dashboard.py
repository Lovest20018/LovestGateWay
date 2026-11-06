from pathlib import Path
import hashlib
import json
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_dashboard as dashboard

class DashboardTests(unittest.TestCase):
    def test_source_build_and_opaque_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)
            entry=dashboard.prepare_dashboard(output)
            index=(output/'index.html').read_text(encoding='utf-8')
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertIn(entry,index)
            self.assertIn('/ui/'+manifest['assets']['ppsub_panel.js'],index)
            self.assertIn('/ui/'+manifest['assets']['gateway.js'],index)
            self.assertIn('ppgw-gateway-view',index)
            upstream=ROOT/'FILES/etc/config/clash/clash-dashboard/ui'
            bundle=next(upstream.glob('bundle.*.js'))
            self.assertEqual((output/bundle.name).read_bytes(),bundle.read_bytes())
            shell=(output/entry).read_text(encoding='utf-8')
            self.assertNotIn('legacy-bridge',shell)
            self.assertNotIn('legacy-bridge.js',manifest['assets'])
            for view in ['Connections','Traffic','Distribution','Logs','Region']:
                self.assertIn('mount'+view,shell)
            for label,name in manifest['assets'].items():
                if label.endswith('.js'):
                    source=(output/name).read_text(encoding='utf-8')
                    self.assertNotIn(bundle.name,source)
                    for imported in re.findall(r"(?:from\s*|import\s*\()\s*['\"]\./([^'\"]+)",source):
                        self.assertTrue((output/imported).is_file(),imported)
            self.assertNotIn("el('iframe'",shell)
            self.assertIn('window.ppgwGateway.mount',shell)
            self.assertIn('mountEditor',shell)
            self.assertNotIn('ppgwGatewayRoute',shell)
            self.assertNotIn('Fl(()=>',shell)
            self.assertNotIn('Fl(()=>',Path(dashboard.__file__).read_text(encoding='utf-8'))
            for label,name in manifest['assets'].items():
                self.assertIn(hashlib.sha256((output/name).read_bytes()).hexdigest()[:12],name)
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
            self.assertEqual(dashboard.prepare_dashboard(output),entry)
            after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
            self.assertEqual(before,after)

if __name__=='__main__':
    unittest.main()
