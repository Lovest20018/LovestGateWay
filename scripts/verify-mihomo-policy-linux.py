"""Verify native policy transactions in a marked disposable Debian gateway."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path);args=parser.parse_args()
    if os.geteuid()!=0 or not Path('/run/management-validation-only').is_file():
        parser.error('requires a marked disposable root fixture')
    sys.path.insert(0,'/usr/local/lib/paopaogw')
    from manage_common import Layout,atomic_write
    from manage_model import render_updates,load_settings
    from manage_runtime import System
    from manage_policy import PolicyManager
    from manage_config import ConfigManager
    from clash_ppsub import running_document
    import subscription_admin as source
    import unified_editor as editor
    import mihomo_reload
    import socketserver,threading
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while True:
                data=self.request.recv(4096)
                if not data:return
                self.request.sendall(data)
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    layout=Layout();system=System(layout,timeout=60,preserve_units={'paopaogw-subscription'})
    config=Path('/etc/paopaogw');original={p:p.read_bytes() for p in config.rglob('*') if p.is_file()}
    report={'synthetic_only':True,'production_configuration_used':False,'checks':[]};keep=None
    def passed(name):
        report['checks'].append(name);args.report.write_text(json.dumps(report,indent=2)+'\n');print('PASS '+name,flush=True)
    def runtime():
        value=system.runtime();return value.get('data',value)
    def controller():
        pid=system.command('systemctl','show','paopaogw','-p','MainPID','--value').stdout.strip()
        return mihomo_reload.UnixController('/proc/'+pid+'/root/tmp/clash.sock')
    def receive(sock,count):
        result=b''
        while len(result)<count:
            raw=sock.recv(count-len(result));assert raw;result+=raw
        return result
    def connection():
        sock=socket.create_connection(('127.0.0.1',1080),timeout=4);sock.settimeout(4)
        sock.sendall(b'\x05\x01\x00');assert receive(sock,2)==b'\x05\x00'
        sock.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01'+struct.pack('!H',server.server_address[1]))
        reply=receive(sock,4);assert reply[1]==0;receive(sock,6);return sock
    def echo(sock,value):sock.sendall(value);assert receive(sock,len(value))==value
    try:
        atomic_write(layout,'etc/paopaogw/files/reload-fixture.yaml',b'proxies: []\nproxy-groups: [{name: MAIN, type: select, proxies: [DIRECT, REJECT]}]\nrules: ["MATCH,MAIN"]\n')
        layout.path('etc/paopaogw/files/clash-policy.json').unlink(missing_ok=True)
        updates,_=render_updates(layout,{'ini':{'mode':'yaml','yamlfile':'reload-fixture.yaml','fast_node':'no','dns_burn':False}})
        for name,raw in updates.items():atomic_write(layout,name,raw)
        system.command('systemctl','restart','paopaogw');system.wait(load_settings(layout))
        old=runtime();client=controller();_,peer=client.request('GET','/version')
        client.request('PUT','/proxies/MAIN',{'name':'DIRECT'});client.request('PATCH','/configs',{'mode':'global'})
        client.request('PUT','/proxies/GLOBAL',{'name':'DIRECT'})
        keep=connection();echo(keep,b'before-native')
        admin=source.Admin(layout);snapshot=admin.current_config();value=deepcopy(snapshot['config'])
        value['rules'][-1]['fixrule'].insert(0,'DOMAIN,blocked.test,REJECT')
        body={'kind':'editor','edit_kind':'clash_patch','value':json.dumps(value),'base_revision':snapshot['base_revision'],
              'settings_revision':snapshot['settings_revision'],'snapshot_id':snapshot['snapshot_id'],
              'settings_patch':{},'operation_id':'native-reload-001'}
        preview=editor.handle(admin,{**body,'dry_run':True});assert preview['action']=='mihomo_reload'
        operation={'id':body['operation_id'],'status':'applying'};admin.operation=operation
        editor.worker(admin,body,operation);assert operation['status']=='applied',dict(operation)
        assert runtime()['boot_id']==old['boot_id'] and client.request('GET','/version')[1]==peer
        echo(keep,b'after-native');system.verify_current(load_settings(layout))
        status=json.loads(layout.path('var/lib/paopaogw/admin/policy-status.json').read_bytes())
        assert status['apply_method']=='mihomo_reload' and 'rules' in status['local_sections']
        assert client.request('GET','/configs')[0]['mode']=='global'
        passed('unified_editor_preview_and_apply_verify_native_persistence_without_restarting_core_or_mihomo')
        manager=PolicyManager(layout,system);snapshot=manager.show()
        body={'snapshot_id':snapshot['snapshot_id'],'base_revision':snapshot['base_revision'],
              'policy':{key:snapshot[key] for key in ('groups','providers','rules')}}
        body['policy']['rules'].insert(0,'DOMAIN,second.test,DIRECT')
        assert manager.preview(body)['action']=='mihomo_reload'
        result=manager.apply(body);assert result['action']=='mihomo_reload'
        assert runtime()['boot_id']==old['boot_id'];echo(keep,b'after-native-policy')
        accepted=running_document();policy=layout.path('etc/paopaogw/files/clash-policy.json').read_bytes()
        passed('native_policy_api_uses_verified_reload_and_keeps_existing_tcp')
        snapshot=manager.show();body={'snapshot_id':snapshot['snapshot_id'],'base_revision':snapshot['base_revision'],
              'policy':{key:snapshot[key] for key in ('groups','providers','rules')}}
        body['policy']['rules'].insert(0,'DOMAIN,uncertain.test,REJECT')
        request=mihomo_reload.UnixController.request;injected=[]
        def uncertain(self,method,path,body=None):
            result=request(self,method,path,body)
            if method=='PUT' and path.startswith('/configs?'):
                injected.append(True);raise OSError('synthetic lost acknowledgement')
            return result
        with patch.object(mihomo_reload.UnixController,'request',uncertain):
            try:manager.apply(body);raise AssertionError('uncertain application accepted')
            except source.ManagementError as error:assert str(error)=='APPLY_FAILED_ROLLED_BACK'
        assert injected and runtime()['boot_id']!=old['boot_id']
        assert layout.path('etc/paopaogw/files/clash-policy.json').read_bytes()==policy
        assert running_document()['rules']==accepted['rules']
        assert ConfigManager(layout,system).journal()['phase']=='rolled_back'
        fresh=connection();echo(fresh,b'after-native-rollback');fresh.close();system.verify_current(load_settings(layout))
        passed('lost_acknowledgement_restores_policy_and_restarts_real_services_with_readiness_verification')
        report['complete']=True;args.report.write_text(json.dumps(report,indent=2)+'\n')
    finally:
        if keep:keep.close()
        for path in config.rglob('*'):
            if path.is_file() and path not in original:path.unlink()
        for path,raw in original.items():atomic_write(layout,path.relative_to('/').as_posix(),raw)
        system.command('systemctl','restart','paopaogw');system.wait(load_settings(layout))
        server.shutdown();server.server_close()


if __name__=='__main__':main()
