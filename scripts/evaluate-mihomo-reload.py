"""Run only in an isolated network namespace/container, without production mounts.

Example: python3 evaluate-mihomo-reload.py --binary /experiment/mihomo
         --work-dir /experiment/basic-new-run
The work directory must not exist. All listeners and destinations are loopback;
fixed ports 19080/19190 are intended for an otherwise empty isolated namespace.
This experiment does not enable a product reload path.
"""
from pathlib import Path
import argparse
import hashlib
import json
import socket
import socketserver
import struct
import subprocess
import threading
import time
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError

SECRET='synthetic-controller-reload-token'


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            value=self.request.recv(4096)
            if not value:return
            self.request.sendall(value)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--work-dir',required=True,type=Path)
    args=parser.parse_args();binary=args.binary.resolve();ROOT=args.work_dir.resolve()
    if not binary.is_file():parser.error('binary must be an existing executable')
    ROOT.mkdir(exist_ok=False)
    checks=[];report={'synthetic_only':True,'production_touched':False,'passed':checks,
                      'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                      'version':subprocess.check_output([str(binary),'-v'],text=True).strip()}
    def save(): (ROOT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    def passed(name):checks.append(name);save();print('PASS '+name,flush=True)
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start();echo_port=server.server_address[1]
    controller=19190;socks_port=19080
    config={'socks-port':socks_port,'allow-lan':False,'bind-address':'127.0.0.1',
            'mode':'rule','log-level':'info','external-controller':f'127.0.0.1:{controller}',
            'secret':SECRET,'ipv6':False,'dns':{'enable':False},'hosts':{'probe.test':'127.0.0.1'},
            'geodata-mode':False,'profile':{'store-selected':True},'proxies':[],
            'proxy-groups':[{'name':'PERSIST','type':'select','proxies':['REJECT','DIRECT']}],
            'rules':['MATCH,PERSIST']}
    base=ROOT/'base.json';base.write_text(json.dumps(config))
    log=(ROOT/'mihomo.log').open('w');process=subprocess.Popen([str(binary),'-d',str(ROOT/'state'),'-f',str(base)],stdout=log,stderr=subprocess.STDOUT)
    opener=build_opener(ProxyHandler({}));base_url=f'http://127.0.0.1:{controller}'
    def api(path,body=None,method=None):
        request=Request(base_url+path,data=None if body is None else json.dumps(body).encode(),
                        headers={'Authorization':'Bearer '+SECRET,'Content-Type':'application/json'},method=method)
        try:
            with opener.open(request,timeout=6) as response:
                raw=response.read();return response.status,json.loads(raw) if raw else None
        except HTTPError as error:
            error.response_body=error.read().decode('utf-8',errors='replace')
            raise
    def receive(sock,length):
        raw=b''
        while len(raw)<length:
            part=sock.recv(length-len(raw))
            if not part:raise RuntimeError('unexpected socket EOF')
            raw+=part
        return raw
    def connect(host='127.0.0.1'):
        sock=socket.create_connection(('127.0.0.1',socks_port),timeout=5);sock.settimeout(5)
        sock.sendall(b'\x05\x01\x00');assert receive(sock,2)==b'\x05\x00'
        name=host.encode();sock.sendall(b'\x05\x01\x00\x03'+bytes([len(name)])+name+struct.pack('!H',echo_port))
        reply=receive(sock,4)
        if reply[1]!=0:sock.close();return None,reply[1]
        size={1:4,4:16}.get(reply[3]);size=receive(sock,1)[0] if reply[3]==3 else size
        receive(sock,size+2);return sock,0
    def echo(sock,value):sock.sendall(value);assert receive(sock,len(value))==value
    def rejected(host):
        sock,code=connect(host)
        if code!=0:return True
        try:
            sock.sendall(b'must-not-echo')
            return sock.recv(128)==b''
        except (ConnectionResetError,BrokenPipeError):return True
        finally:sock.close()
    keep=None
    try:
        deadline=time.monotonic()+20
        while True:
            try:api('/version');break
            except Exception:
                if process.poll() is not None or time.monotonic()>deadline:raise
                time.sleep(.2)
        api('/proxies/PERSIST',{'name':'DIRECT'},'PUT');assert api('/proxies/PERSIST')[1]['now']=='DIRECT'
        keep,code=connect();assert code==0;echo(keep,b'before-reload')
        before_connections=api('/connections')[1]['connections'];assert len(before_connections)==1
        connection_id=before_connections[0]['id'];pid=process.pid
        passed('explicit_selection_and_long_lived_socks_echo_established')
        updated=dict(config);updated['rules']=['DOMAIN,probe.test,REJECT','MATCH,PERSIST'];candidate=ROOT/'state/candidate.json';candidate.write_text(json.dumps(updated))
        validation=subprocess.run([str(binary),'-t','-d',str(ROOT/'state'),'-f',str(candidate)],capture_output=True,text=True,timeout=20);assert validation.returncode==0
        status,_=api('/configs?force=false',{'path':str(candidate)},'PUT');assert status==204
        assert process.poll() is None and process.pid==pid
        echo(keep,b'after-valid-reload');assert any(row['id']==connection_id for row in api('/connections')[1]['connections'])
        passed('validated_reload_preserves_same_tcp_connection_and_process')
        assert api('/proxies/PERSIST')[1]['now']=='DIRECT';passed('explicit_selector_choice_preserved_across_yaml_reload')
        assert rejected('probe.test')
        fresh,code=connect();assert code==0;echo(fresh,b'fresh-after-rule-change');fresh.close()
        rules=api('/rules')[1]['rules'];assert any(row['payload']=='probe.test' and row['proxy']=='REJECT' for row in rules)
        passed('new_rule_rejects_matching_new_connection_and_allows_other_new_connection')
        broken=dict(updated);broken['proxies']=[{'name':'broken','type':'synthetic-invalid-proxy'}];bad=ROOT/'state/invalid.json';bad.write_text(json.dumps(broken))
        try:api('/configs?force=false',{'path':str(bad)},'PUT');raise AssertionError('invalid candidate accepted')
        except HTTPError as error:assert error.code>=400;error.close()
        echo(keep,b'after-invalid-reload');assert any(row['id']==connection_id for row in api('/connections')[1]['connections'])
        fresh,code=connect();assert code==0;echo(fresh,b'new-after-invalid-reload');fresh.close()
        assert rejected('probe.test')
        assert api('/proxies/PERSIST')[1]['now']=='DIRECT'
        passed('failed_candidate_keeps_loaded_rules_selection_active_connection_and_new_connections')
        report.update(complete=True,process_restarted=False,connection_identity_preserved=True,
                      scope='single synthetic DIRECT selector and local SOCKS echo; broader modes and provider loads unproven')
        save()
    except Exception as error:
        report.update(complete=False,error_type=type(error).__name__,error=str(error),response_body=getattr(error,'response_body',None));save();raise
    finally:
        if keep:keep.close()
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close();server.shutdown();server.server_close()


if __name__=='__main__':main()
