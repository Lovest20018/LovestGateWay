"""Probe provider reload boundaries in an isolated container/network namespace.

Example: python3 evaluate-mihomo-provider-reload.py --binary /experiment/mihomo
         --work-dir /experiment/provider-new-run
The work directory must not exist, so cached nodes cannot outlive ephemeral
fixture ports. No production mounts or exposed ports are needed. All traffic
uses local HTTP, authenticated SOCKS and TCP echo. Ports 19081/19191 are fixed
inside the isolated namespace. This does not enable a product reload path.
"""
from pathlib import Path
import argparse
import hashlib
import json
import select
import socket
import socketserver
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError

SECRET='synthetic-provider-controller'
accepted={'one'};auth_attempts=[];provider={'password':'one','mode':'valid','requests':0}


def receive(sock,length):
    value=b''
    while len(value)<length:
        part=sock.recv(length-len(value))
        if not part:raise EOFError()
        value+=part
    return value


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            value=self.request.recv(4096)
            if not value:return
            self.request.sendall(value)


class Socks(socketserver.BaseRequestHandler):
    def handle(self):
        peer=None
        try:
            assert receive(self.request,1)==b'\x05';methods=receive(self.request,receive(self.request,1)[0]);assert 2 in methods
            self.request.sendall(b'\x05\x02');assert receive(self.request,1)==b'\x01'
            user=receive(self.request,receive(self.request,1)[0]).decode();password=receive(self.request,receive(self.request,1)[0]).decode();auth_attempts.append(password)
            if user!='fixture' or password not in accepted:self.request.sendall(b'\x01\x01');return
            self.request.sendall(b'\x01\x00');header=receive(self.request,4);assert header[:2]==b'\x05\x01'
            if header[3]==1:host=socket.inet_ntoa(receive(self.request,4))
            elif header[3]==3:host=receive(self.request,receive(self.request,1)[0]).decode()
            else:raise ValueError('unsupported synthetic address')
            port=struct.unpack('!H',receive(self.request,2))[0];peer=socket.create_connection((host,port),timeout=4)
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            while True:
                ready,_,_=select.select([self.request,peer],[],[],10)
                if not ready:continue
                for source in ready:
                    raw=source.recv(4096)
                    if not raw:return
                    (peer if source is self.request else self.request).sendall(raw)
        except (OSError,EOFError,ValueError,AssertionError):pass
        finally:
            if peer:peer.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--work-dir',required=True,type=Path)
    args=parser.parse_args();binary=args.binary.resolve();ROOT=args.work_dir.resolve()
    if not binary.is_file():parser.error('binary must be an existing executable')
    ROOT.mkdir(exist_ok=False)
    echo=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo);echo.daemon_threads=True
    upstream=socketserver.ThreadingTCPServer(('127.0.0.1',0),Socks);upstream.daemon_threads=True
    for server in (echo,upstream):threading.Thread(target=server.serve_forever,daemon=True).start()
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            provider['requests']+=1
            if provider['mode']=='unavailable':self.send_response(503);self.end_headers();return
            proxies=[{'name':'ProviderNode','type':'socks5','server':'127.0.0.1','port':upstream.server_address[1],
                      'username':'fixture','password':provider['password']}]
            if provider['mode']=='invalid':proxies=[{'name':'ProviderNode','type':'synthetic-invalid'}]
            raw=json.dumps({'proxies':proxies}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Provider);threading.Thread(target=http.serve_forever,daemon=True).start()
    config={'socks-port':19081,'allow-lan':False,'bind-address':'127.0.0.1','mode':'rule','log-level':'info',
            'external-controller':'127.0.0.1:19191','secret':SECRET,'dns':{'enable':False},
            'profile':{'store-selected':True},'proxy-providers':{'local':{'type':'http','url':f'http://127.0.0.1:{http.server_port}/provider',
               'path':'./providers/local.json','interval':3600,'proxy':'DIRECT'}},
            'proxy-groups':[{'name':'PERSIST','type':'select','proxies':['REJECT'],'use':['local']}],
            'rules':['MATCH,PERSIST']}
    base=ROOT/'base.json';base.write_text(json.dumps(config));log=(ROOT/'mihomo.log').open('w')
    process=subprocess.Popen([str(binary),'-d',str(ROOT),'-f',str(base)],stdout=log,stderr=subprocess.STDOUT)
    report={'synthetic_only':True,'production_touched':False,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'version':subprocess.check_output([str(binary),'-v'],text=True).strip(),'observations':[]}
    opener=build_opener(ProxyHandler({}));keep=None
    def save(): (ROOT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    def note(name,**detail):report['observations'].append({'name':name,**detail});save();print(name,json.dumps(detail),flush=True)
    def api(path,body=None,method=None):
        req=Request('http://127.0.0.1:19191'+path,data=None if body is None else json.dumps(body).encode(),
                    headers={'Authorization':'Bearer '+SECRET,'Content-Type':'application/json'},method=method)
        try:
            with opener.open(req,timeout=8) as result:
                raw=result.read();return result.status,json.loads(raw) if raw else None
        except HTTPError as error:return error.code,error.read().decode()
    def connection():
        sock=socket.create_connection(('127.0.0.1',19081),timeout=4);sock.settimeout(4);sock.sendall(b'\x05\x01\x00');assert receive(sock,2)==b'\x05\x00'
        sock.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01'+struct.pack('!H',echo.server_address[1]));header=receive(sock,4)
        if header[1]:sock.close();return None
        receive(sock,(4 if header[3]==1 else 16)+2);return sock
    def echoed(sock,message):
        try:sock.sendall(message);return receive(sock,len(message))==message
        except (OSError,EOFError):return False
    def fresh():
        sock=None
        try:sock=connection();return sock is not None and echoed(sock,b'fresh-provider')
        except (OSError,EOFError):return False
        finally:
            if sock:sock.close()
    def cached_password():
        path=ROOT/'providers/local.json'
        if not path.exists():return None
        text=path.read_text()
        return 'two' if 'two' in text else 'one' if 'one' in text else 'unknown'
    def tracked(identifier):return any(row['id']==identifier for row in (api('/connections')[1].get('connections') or []))
    def identity(sock):
        port=sock.getsockname()[1]
        return next(row['id'] for row in api('/connections')[1]['connections']
                    if row['metadata']['sourcePort'] in (port,str(port)))
    def reload_candidate(candidate):
        validation=subprocess.run([str(binary),'-t','-d',str(ROOT),'-f',str(candidate)],capture_output=True,text=True,timeout=20)
        assert validation.returncode==0,'synthetic candidate failed validation'
        status,_=api('/configs?force=false',{'path':str(candidate)},'PUT')
        time.sleep(.5)
        return status
    try:
        deadline=time.monotonic()+25
        while True:
            try:
                status,value=api('/providers/proxies/local')
                if status==200 and value.get('proxies'):break
            except Exception:pass
            if time.monotonic()>deadline or process.poll() is not None:raise RuntimeError('provider not ready')
            time.sleep(.2)
        assert api('/proxies/PERSIST',{'name':'ProviderNode'},'PUT')[0]==204
        keep=connection();assert keep and echoed(keep,b'provider-before');identifier=identity(keep)
        note('authenticated_provider_node_selected',password_seen=auth_attempts[-1],selected=api('/proxies/PERSIST')[1]['now'])
        status=reload_candidate(base)
        note('identical_configuration_reload_with_provider',controller_status=status,old_connection_echo=echoed(keep,b'identical-reload'),
             connection_id_present=tracked(identifier),new_connection_echo=fresh(),cached_password=cached_password())
        keep.close();keep=connection();assert keep and echoed(keep,b'provider-before-auth-change')
        identifier=identity(keep)
        provider['password']='two';accepted.clear();accepted.add('two')
        assert echoed(keep,b'auth-changed-without-reload')
        note('remote_auth_changed_without_reload',old_connection_echo=True,connection_id_present=tracked(identifier))
        changed=dict(config);changed['proxy-groups']=config['proxy-groups']+[{'name':'SECOND','type':'select','use':['local']}];candidate=ROOT/'candidate.json';candidate.write_text(json.dumps(changed))
        status=reload_candidate(candidate)
        note('reload_group_structure_with_remote_auth_changed',controller_status=status,old_connection_echo=echoed(keep,b'after-group-reload'),connection_id_present=tracked(identifier),new_connection_echo=fresh(),cached_password=cached_password(),last_auth_attempt=auth_attempts[-1],selected=api('/proxies/PERSIST')[1].get('now'))
        status,body=api('/providers/proxies/local',{},'PUT');time.sleep(.4)
        note('explicit_provider_refresh_after_auth_change',controller_status=status,new_connection_echo=fresh(),cached_password=cached_password(),last_auth_attempt=auth_attempts[-1],old_connection_echo=echoed(keep,b'after-provider-refresh'))
        for mode in ('invalid','unavailable'):
            provider['mode']=mode
            status,body=api('/providers/proxies/local',{},'PUT')
            reload_status=reload_candidate(candidate)
            note(mode+'_provider_refresh_and_config_reload',provider_status=status,reload_status=reload_status,old_connection_echo=echoed(keep,('after-'+mode).encode()),new_connection_echo=fresh(),cached_password=cached_password(),selected=api('/proxies/PERSIST')[1].get('now'))
        assert process.poll() is None
        report.update(complete=True,pid_unchanged=True,connection_id_present=tracked(identifier),
                      provider_fetches=provider['requests'],auth_attempts=auth_attempts,conclusion='Assess observations; config 204 alone does not prove provider refresh or complete candidate equivalence.')
        save()
    except Exception as error:report.update(complete=False,error_type=type(error).__name__,error=str(error));save();raise
    finally:
        if keep:keep.close()
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=4)
        log.close()
        for server in (http,upstream,echo):server.shutdown();server.server_close()


if __name__=='__main__':main()
