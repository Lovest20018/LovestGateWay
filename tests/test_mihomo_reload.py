from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'integration'))
from mihomo_reload import plan, rule_rows, reload_verified
from manage_common import ManagementError

BASE = {'mode': 'rule', 'dns': {'enable': False},
        'proxies': [{'name':'Fixture','type':'socks5','server':'127.0.0.1','port':9000,'password':'old'}],
        'proxy-groups': [{'name':'MAIN','type':'select','proxies':['Fixture','DIRECT']}], 'rules':['MATCH,MAIN']}

class Controller:
    def __init__(self):
        self.doc=deepcopy(BASE);self.mode='global';self.selected='DIRECT';self.calls=[];self.peer=(123,'456');self.ignore=False;self.changed=False
    def request(self, method, path, body=None):
        self.calls.append((method,path,body))
        if path=='/configs' and method=='GET':result={'mode':self.mode}
        elif path.startswith('/configs?'):
            if not self.ignore:self.doc=json.loads(body['payload']);self.selected='Fixture'
            if self.changed:self.peer=(999,'999')
            result=None
        elif path=='/proxies':result={'proxies':{'MAIN':{'type':'Selector','all':self.doc['proxy-groups'][0]['proxies'],'now':self.selected}}}
        elif path.startswith('/proxies/'):
            self.selected=body['name'];result=None
        elif path=='/rules':result={'rules':[{'type':kind,'payload':payload,'proxy':proxy} for kind,payload,proxy in rule_rows(self.doc)]}
        else:result={'version':'fixture'}
        return result,self.peer

class ReloadTests(unittest.TestCase):
    def candidate(self):
        document=deepcopy(BASE);document['rules'].insert(0,'DOMAIN,blocked.example,REJECT');return document
    def test_static_policy_allowlist_rejects_providers_credentials_network_and_unknown_rules(self):
        after=self.candidate();self.assertTrue(plan(BASE,after)['eligible'])
        for key,value in [('secret','new'),('proxies',[]),('dns',{'enable':True}),('listeners',[{}]),('x-ppgw-features',{})]:
            changed=deepcopy(after);changed[key]=value;self.assertFalse(plan(BASE,changed)['eligible'],key)
        for key in ['proxy-providers','rule-providers','sub-rules']:
            previous=deepcopy(BASE);previous[key]={'fixture':{}};changed=deepcopy(after);changed[key]=previous[key];self.assertFalse(plan(previous,changed)['eligible'])
        for rule in ['RULE-SET,provider,MAIN','GEOIP,CN,DIRECT','AND,((NETWORK,TCP)),DIRECT']:
            after['rules']=[rule];self.assertFalse(plan(BASE,after)['eligible'])
    def test_acceptance_reads_actual_rules_and_preserves_mode_and_manual_selection(self):
        controller=Controller();events=[]
        result=reload_verified(BASE,self.candidate(),controller,lambda doc:events.append(('validate',deepcopy(doc))),lambda doc:events.append(('persist',deepcopy(doc))))
        self.assertEqual([event[0] for event in events],['validate','persist']);self.assertEqual(events[-1][1]['mode'],'global');self.assertEqual(controller.selected,'DIRECT');self.assertEqual(result['method'],'mihomo_reload');self.assertEqual(result['connection_continuity'],'not_measured')
        self.assertEqual(len([call for call in controller.calls if call[0]=='PUT' and call[1].startswith('/configs?')]),1)
    def test_controller_rule_names_normalize_ipv6_cidr_and_network_without_ignoring_targets(self):
        self.assertEqual(rule_rows({'rules':['IP-CIDR6,2001:db8::/32,DIRECT,no-resolve','NETWORK,UDP,MAIN']}),
                         [('ipcidr','2001:db8::/32','DIRECT'),('network','udp','MAIN')])
        controller=Controller();controller.doc['rules']=['MATCH,DIRECT'];written=[]
        with self.assertRaisesRegex(ManagementError,'STALE_POLICY_SNAPSHOT'):
            reload_verified(BASE,self.candidate(),controller,lambda doc:None,written.append)
        self.assertFalse(any(call[0]=='PUT' for call in controller.calls));self.assertEqual(written,[])
    def test_automatic_group_manual_lock_is_restored_and_verified(self):
        class LockedController(Controller):
            def __init__(self):super().__init__();self.fixed='Fixture'
            def request(self,method,path,body=None):
                if path=='/proxies/Auto':
                    self.calls.append((method,path,body));self.fixed=body['name'];return None,self.peer
                value,peer=super().request(method,path,body)
                if path.startswith('/configs?'):self.fixed=''
                if path=='/proxies':
                    value['proxies']['Auto']={'type':'URLTest','all':['Fixture','DIRECT'],'fixed':self.fixed}
                return value,peer
        before=deepcopy(BASE);before['proxy-groups'].append({'name':'Auto','type':'url-test','proxies':['Fixture','DIRECT']})
        after=deepcopy(before);after['rules'].insert(0,'DOMAIN,blocked.example,REJECT')
        controller=LockedController();controller.doc=deepcopy(before)
        reload_verified(before,after,controller,lambda doc:None,lambda doc:None)
        self.assertEqual(controller.fixed,'Fixture')
        self.assertTrue(any(call[:2]==('PUT','/proxies/Auto') for call in controller.calls))
    def test_204_without_rules_loaded_and_process_replacement_never_persist_success(self):
        for kind in ['ignore','changed']:
            controller=Controller();setattr(controller,kind,True);written=[]
            with self.assertRaises(ManagementError):reload_verified(BASE,self.candidate(),controller,lambda doc:None,written.append)
            self.assertEqual(written,[])
    def test_config_transaction_reports_hot_action_and_rolls_back_persistent_policy_on_failure(self):
        import tempfile
        from manage_common import Layout
        from manage_model import settings_revision
        from manage_config import ConfigManager
        from test_manage_config import FakeSystem
        with tempfile.TemporaryDirectory() as directory:
            layout=Layout(directory);ini=layout.path('etc/paopaogw/files/ppgw.ini');ini.parent.mkdir(parents=True);ini.write_text('#paopao-gateway\nmode=yaml\nclash_web_password=long-fixture-password\n')
            policy=ini.parent/'clash-policy.json';policy.write_text('{"old":true}');original=policy.read_bytes();system=FakeSystem();manager=ConfigManager(layout,system);update={'before':deepcopy(BASE),'after':self.candidate()}
            preview=manager.apply({},settings_revision(layout),policy_update=b'{"new":true}',mihomo_update=update,dry_run=True)
            self.assertEqual(preview['action'],'mihomo_reload');self.assertEqual(policy.read_bytes(),original)
            system.fail=True
            with self.assertRaisesRegex(ManagementError,'APPLY_FAILED_ROLLED_BACK'):manager.apply({},settings_revision(layout),policy_update=b'{"new":true}',mihomo_update=update)
            self.assertEqual(system.calls,['mihomo_reload','restore']);self.assertEqual(policy.read_bytes(),original);self.assertEqual(manager.journal()['phase'],'rolled_back')
            preview=manager.apply({'ini':{'subtime':'6h'}},settings_revision(layout),policy_update=b'{"new":true}',mihomo_update=update,dry_run=True);self.assertEqual(preview['action'],'restart')

if __name__=='__main__':unittest.main()
