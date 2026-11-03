#!/usr/bin/env python3
"""Temporarily exercise an installed native gateway through disposable netns clients.

Requires root and --run. Preserves saved configuration, proxy selections and foreign
firewall rules. Documentation addresses prove local transport, not public IPv6 or
QUIC protocol compatibility. A real subscription HTTPS case runs before the fixture.
"""
import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

CLIENT = 'ppgw-native-client'
TARGET = 'ppgw-native-target'
CLIENT_LINK = 'ppgwt-c'
TARGET_LINK = 'ppgwt-s'


def run(*args, input=None, check=True):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=25)
    if check and result.returncode:
        raise RuntimeError(f'{args[:4]} failed ({result.returncode}): {result.stderr[-300:]}')
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=15)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


SERVER = r'''
import http.server,json,socket,threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  raw=json.dumps({'peer':self.client_address[0],'path':self.path}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def log_message(self,*args):pass
class V6(http.server.ThreadingHTTPServer):
 address_family=socket.AF_INET6
 def server_bind(self):
  self.socket.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);super().server_bind()
for cls,addr in [(http.server.ThreadingHTTPServer,('0.0.0.0',8088)),(V6,('::',8088))]:
 s=cls(addr,Handler);threading.Thread(target=s.serve_forever,daemon=True).start()
def echo(family,port):
 s=socket.socket(family,socket.SOCK_DGRAM)
 if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
 s.bind(('::' if family==socket.AF_INET6 else '0.0.0.0',port))
 while True:
  data,peer=s.recvfrom(4096);s.sendto(json.dumps({'echo':data.decode(),'peer':peer[0]}).encode(),peer)
for family in (socket.AF_INET,socket.AF_INET6):
 for port in (19000,443):threading.Thread(target=echo,args=(family,port),daemon=True).start()
threading.Event().wait()
'''

DNS = r'''
import socket,struct,json,sys
name=b''.join(bytes([len(p)])+p.encode() for p in 'example.com'.split('.'))+b'\0'
q=b'\x73\x21\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'+name+b'\x00\x01\x00\x01'
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.settimeout(5);s.sendto(q,(sys.argv[1],int(sys.argv[2])));data,_=s.recvfrom(4096)
assert data[:2]==q[:2] and (data[3]&15)==0
count=struct.unpack('!H',data[6:8])[0];offset=12+len(name)+4;answers=[]
for _ in range(count):
 if data[offset]&192==192:offset+=2
 else:
  while data[offset]:offset+=data[offset]+1
  offset+=1
 typ,cls,ttl,n=struct.unpack('!HHIH',data[offset:offset+10]);offset+=10
 if typ==1 and cls==1 and n==4:answers.append(socket.inet_ntoa(data[offset:offset+4]))
 offset+=n
assert answers
print(json.dumps(answers))
'''

UDP = r'''
import socket,json
out={}
for family,addr in ((socket.AF_INET,'203.0.113.2'),(socket.AF_INET6,'2001:db8:91::2')):
 for port in (19000,443):
  with socket.socket(family,socket.SOCK_DGRAM) as s:
   s.settimeout(5);s.sendto(b'native-fixture',(addr,port));data,peer=s.recvfrom(4096)
   observed=json.loads(data)
   assert observed['echo']=='native-fixture' and peer[0]==addr
   assert observed['peer']==('203.0.113.1' if family==socket.AF_INET else '2001:db8:91::1')
   out[('ipv4' if family==socket.AF_INET else 'ipv6')+'_udp_'+str(port)]={'echo':True,'target_observed_peer':observed['peer']}
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.run or os.geteuid() != 0:
        parser.error('requires root and explicit --run')
    sys.path.insert(0, '/usr/local/lib/paopaogw')
    import yaml
    from manage_common import Layout, operation_lock
    from manage_model import load_settings
    from manage_runtime import System
    from gateway_api import CoreClient
    layout = Layout()
    settings = load_settings(layout)
    assert settings['ini']['mode'] == 'suburl' and settings['ini']['udp_enable']
    assert settings['service']['PPGW_IPV6'] is True
    existing_ns = run('ip', 'netns', 'list').stdout.splitlines()
    assert not any(line.split()[0] in {CLIENT, TARGET} for line in existing_ns)
    for link in (CLIENT_LINK, TARGET_LINK):
        assert run('ip', 'link', 'show', 'dev', link, check=False).returncode != 0
    # A retained validation bridge can otherwise steal return traffic silently.
    for family, networks in (('-4', ('10.231.91.0/30', '203.0.113.0/30')),
                              ('-6', ('fd99:113:91::/64', '2001:db8:91::/64'))):
        for route in json.loads(run('ip', family, '-j', 'route', 'show', 'table', 'main').stdout):
            if route.get('dst', 'default') == 'default':
                continue
            existing = ipaddress.ip_network(route['dst'], strict=False)
            assert not any(existing.overlaps(ipaddress.ip_network(network)) for network in networks), 'Fixture subnet is already in use'
    report = {'environment': 'native gateway with disposable netns clients', 'checks': {},
              'coverage': 'real subscription HTTPS; local IPv4/IPv6 TCP and UDP echo, including UDP/443; not public IPv6 or full QUIC'}
    with operation_lock(layout), tempfile.TemporaryDirectory(prefix='ppgw-native-') as temporary:
        system = System(layout)
        before = system.wait(settings)
        pid = int(run('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value').stdout)
        private_tmp = Path(f'/proc/{pid}/root/tmp')
        saved_config = {str(p): digest(p) for p in Path('/etc/paopaogw').rglob('*') if p.is_file()}
        foreign_rules = run('nft', '-j', 'list', 'table', 'inet', 'shellcrash', check=False).stdout
        host_files = {p: digest(p) for p in ('/etc/hosts', '/etc/resolv.conf')}
        main_routes = {family: run('ip', family, '-j', 'route', 'show', 'table', 'main').stdout for family in ('-4', '-6')}
        client = CoreClient(Path('/var/lib/paopaogw/core-token'))
        def mihomo(method, path, data=None):
            connection = UnixHTTP(private_tmp / 'clash.sock')
            try:
                connection.request(method, path, body=None if data is None else json.dumps(data),
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse(); raw = response.read()
                if not 200 <= response.status < 300: raise RuntimeError('Mihomo fixture operation failed: ' + str(response.status))
                return json.loads(raw) if raw else None
            finally:
                connection.close()
        original_mode = mihomo('GET', '/configs')['mode']
        selections = {name: item['now'] for name, item in mihomo('GET', '/proxies')['proxies'].items()
                      if item.get('type') == 'Selector' and 'now' in item}
        original_rules = []
        created_namespaces = []
        target_process = None
        runtime_changed = False
        fixture = private_tmp / 'native-network-fixture.yaml'
        try:
            for ns, host, addr4, addr6 in ((CLIENT, CLIENT_LINK, '10.231.91.1/30', 'fd99:113:91::1/64'),
                                          (TARGET, TARGET_LINK, '203.0.113.1/30', '2001:db8:91::1/64')):
                run('ip', 'netns', 'add', ns)
                created_namespaces.append(ns)
                run('ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', 'peer0', 'netns', ns)
                run('ip', 'addr', 'add', addr4, 'dev', host)
                run('ip', '-6', 'addr', 'add', addr6, 'dev', host, 'nodad')
                run('ip', 'link', 'set', host, 'up')
                run('ip', '-n', ns, 'link', 'set', 'lo', 'up')
                run('ip', '-n', ns, 'link', 'set', 'peer0', 'up')
            for ns, addr4, addr6 in ((CLIENT, '10.231.91.2/30', 'fd99:113:91::2/64'),
                                     (TARGET, '203.0.113.2/30', '2001:db8:91::2/64')):
                run('ip', '-n', ns, 'addr', 'add', addr4, 'dev', 'peer0')
                run('ip', '-n', ns, '-6', 'addr', 'add', addr6, 'dev', 'peer0', 'nodad')
            run('ip', '-n', CLIENT, 'route', 'add', 'default', 'via', '10.231.91.1')
            run('ip', '-n', CLIENT, '-6', 'route', 'add', 'default', 'via', 'fd99:113:91::1')
            # Extend only the gateway's own ingress guard for the temporary client.
            for family, chain in (('ip', 'ppgw_tproxy'), ('ip6', 'ppgw_tproxy6')):
                table = json.loads(run('nft', '-j', '-a', 'list', 'chain', family, 'ppgw', chain).stdout)
                matches = [item['rule'] for item in table['nftables'] if 'rule' in item
                           and any(expr.get('match', {}).get('left') == {'meta': {'key': 'iifname'}} for expr in item['rule']['expr'])]
                assert len(matches) == 1
                original = matches[0]; original_rules.append(original)
                changed = copy.deepcopy(original)
                changed.pop('position', None)
                changed['expr'][0]['match']['right'] = {'set': [settings['service']['PPGW_INTERFACE'], CLIENT_LINK]}
                run('nft', '-j', '-f', '-', input=json.dumps({'nftables': [{'replace': {'rule': changed}}]}))
            time.sleep(.3)
            listener = settings['service']['PPGW_DNS_LISTEN']
            dns_address, dns_port = listener.rsplit(':', 1)
            if dns_address == '0.0.0.0':
                addresses = json.loads(run('ip', '-4', '-j', 'addr', 'show', 'dev', settings['service']['PPGW_INTERFACE']).stdout)
                dns_address = next(item['local'] for link in addresses for item in link['addr_info'] if item['scope'] == 'global')
            answers = json.loads(run('ip', 'netns', 'exec', CLIENT, 'python3', '-c', DNS, dns_address, dns_port).stdout)
            fake = answers[0]
            assert ipaddress.ip_address(fake) in ipaddress.ip_network(settings['ini']['fake_cidr'] or '7.0.0.0/8')
            result = run('ip', 'netns', 'exec', CLIENT, 'curl', '--noproxy', '*', '--silent', '--show-error',
                         '--max-time', '20', '--resolve', 'example.com:443:' + fake, '--output', '/dev/null',
                         '--write-out', '%{http_code}', 'https://example.com/')
            assert result.stdout == '200'
            report['checks']['real_subscription_fakeip_https'] = {'http_status': 200, 'fakeip': fake}
            # Use documentation targets and a temporary DIRECT rule for transport proof.
            config = yaml.safe_load((private_tmp / 'clash.yaml').read_text())
            config['mode'] = 'rule'
            config['rules'] = ['IP-CIDR,203.0.113.0/30,DIRECT,no-resolve',
                               'IP-CIDR6,2001:db8:91::/64,DIRECT,no-resolve'] + config.get('rules', [])
            fixture.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
            fixture.chmod(0o600)
            runtime_changed = True
            mihomo('PUT', '/configs?force=true', {'path': '/tmp/' + fixture.name})
            mihomo('PATCH', '/configs', {'mode': 'rule'})
            server_path = Path(temporary) / 'server.py'; server_path.write_text(SERVER)
            target_process = subprocess.Popen(['ip', 'netns', 'exec', TARGET, 'python3', str(server_path)],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(.5)
            assert target_process.poll() is None
            for family, address, client_address in (('ipv4', '203.0.113.2', '10.231.91.2'),
                                                     ('ipv6', '[2001:db8:91::2]', 'fd99:113:91::2')):
                direct = json.loads(run('curl', '--noproxy', '*', '-g', '--silent', '--show-error',
                                        '--max-time', '3', f'http://{address}:8088/direct-{family}').stdout)
                assert direct['path'] == '/direct-' + family
                report['checks'][family + '_fixture_target_reachable'] = True
                response = json.loads(run('ip', 'netns', 'exec', CLIENT, 'curl', '--noproxy', '*', '-g',
                                          '--silent', '--show-error', '--max-time', '8', f'http://{address}:8088/{family}').stdout)
                assert response['peer'] != client_address and response['path'] == '/' + family
                report['checks'][family + '_transparent_http'] = response
            report['checks'].update(json.loads(run('ip', 'netns', 'exec', CLIENT, 'python3', '-c', UDP).stdout))
            for address in ('10.231.91.2', 'fd99:113:91::2'):
                rows = client.request('/sniffbox/evidence', {'resource': 'connections', 'client': address, 'limit': 200})['data']['items']
                assert any(row.get('inbound') == 'tproxy' and row.get('source_address') == address for row in rows)
            report['checks']['both_families_have_tproxy_lifecycle_evidence'] = True
        except Exception:
            try:
                observed = client.request('/sniffbox/evidence', {'resource': 'connections', 'client': 'fd99:113:91::2', 'limit': 20})
                report['fixture_debug'] = {'ipv6_tproxy_connections': observed.get('data', {}).get('items', []),
                    'ipv6_host_routes': json.loads(run('ip', '-6', '-j', 'route', 'show').stdout),
                    'ipv6_client_routes': json.loads(run('ip', '-n', CLIENT, '-6', '-j', 'route', 'show').stdout)}
            except Exception:
                pass
            raise
        finally:
            cleanup_errors = []
            def cleanup(function):
                try: function()
                except Exception as error: cleanup_errors.append(type(error).__name__)
            if runtime_changed:
                cleanup(lambda: mihomo('PUT', '/configs?force=true', {'path': '/tmp/clash.yaml'}))
                cleanup(lambda: mihomo('PATCH', '/configs', {'mode': original_mode}))
                for name, selected in selections.items():
                    cleanup(lambda: mihomo('PUT', '/proxies/' + quote(name, safe=''), {'name': selected}))
            cleanup(lambda: fixture.unlink(missing_ok=True))
            for original in reversed(original_rules):
                cleanup(lambda: run('nft', '-j', '-f', '-', input=json.dumps({'nftables': [{'replace': {'rule': original}}]})))
            if target_process:
                target_process.terminate()
                try: target_process.wait(timeout=3)
                except subprocess.TimeoutExpired: target_process.kill(); target_process.wait()
            for ns in reversed(created_namespaces):
                cleanup(lambda: run('ip', 'netns', 'del', ns))
            for link in (CLIENT_LINK, TARGET_LINK):
                run('ip', 'link', 'del', link, check=False)
            assert not cleanup_errors, 'Fixture cleanup failed: ' + ', '.join(cleanup_errors)
            for original in original_rules:
                restored = json.loads(run('nft', '-j', '-a', 'list', 'chain', original['family'], 'ppgw', original['chain']).stdout)
                assert any(item.get('rule') == original for item in restored['nftables']), 'Gateway ingress guard was not restored'
            assert all(digest(p) == value for p, value in saved_config.items())
            assert all(digest(p) == value for p, value in host_files.items())
            assert run('nft', '-j', 'list', 'table', 'inet', 'shellcrash', check=False).stdout == foreign_rules
            assert all(run('ip', family, '-j', 'route', 'show', 'table', 'main').stdout == routes for family, routes in main_routes.items())
            assert mihomo('GET', '/configs')['mode'] == original_mode
            assert {name: item['now'] for name, item in mihomo('GET', '/proxies')['proxies'].items()
                    if item.get('type') == 'Selector' and 'now' in item} == selections
            after = system.wait(settings)
            assert before['boot_id'] == after['boot_id']
            report['restored'] = {'saved_configuration': True, 'runtime_mode_and_selections': True,
                                  'foreign_firewall_and_host_files': True, 'gateway_ingress_guards': True, 'main_routes': True,
                                  'same_core_boot': True}
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        report['complete'] = True
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
