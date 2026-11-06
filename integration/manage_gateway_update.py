"""Owner-confirmed gateway releases, executed outside the services they replace."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from manage_common import Layout, ManagementError, read_bytes, write_json, operation_lock, require_idle
from manage_operations import ACTIVE, alive, execution, interrupted, lifecycle_lock, record, service_state
from manage_release import RELEASES, download, identity, unpack

BASE = 'var/lib/paopaogw/admin/gateway-update/'
BUILD = 'usr/local/lib/paopaogw/build.json'
JOURNAL = 'var/lib/paopaogw/admin/transaction.json'
ID = re.compile(r'[0-9a-f]{32}')
TTL = 1800


def version(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z0-9._-]+)?', str(value))
    return tuple(int(n) for n in match.groups()) if match else None


class GatewayUpdate:
    def __init__(self, layout=None, start=None, fetch=None, install=None):
        self.layout = layout or Layout()
        self.start = start or self.start_unit
        self.fetch = fetch or download
        self.install = install or self.install_package

    def read(self, name, default=None):
        return json.loads(read_bytes(self.layout, BASE + name, json.dumps(default).encode()))

    def installed(self):
        value = json.loads(read_bytes(self.layout, BUILD, b'{}'))
        return {key: value.get(key) for key in ('version', 'build_commit', 'architecture')}

    def save(self, job):
        write_json(self.layout, BASE + job['id'] + '.json', job)
        record(self.layout, 'gateway', job)

    def job(self, identifier):
        if not isinstance(identifier, str) or not ID.fullmatch(identifier): raise ManagementError('INVALID_JOB_ID')
        value = self.read(identifier + '.json')
        if not value: raise ManagementError('JOB_NOT_FOUND')
        if value['status'] in ACTIVE and self.layout.live:
            # A queued job belongs to its dedicated unit, not the restarting settings process.
            live = alive(value.get('execution'))
            if value['status'] == 'queued':
                state = service_state(self.unit(identifier))
                if time.time() - value['created_at'] < 60 or state is None or state.get('ActiveState') in {'active', 'activating', 'deactivating'}:
                    return value
                live = False
            if live is False:
                value = interrupted(value); self.save(value)
        return value

    @staticmethod
    def unit(identifier): return 'paopaogw-gateway-update@' + identifier + '.service'

    def start_unit(self, identifier):
        if not self.layout.live: raise ManagementError('GATEWAY_UPDATE_REQUIRES_LIVE_SYSTEM')
        subprocess.run(['systemctl', 'start', '--no-block', self.unit(identifier)],
                       check=True, capture_output=True, timeout=10)

    def eligible(self, candidate):
        if not candidate: return False
        installed = self.installed()
        current, target = version(installed['version']), version(candidate['version'])
        return bool(current and target and target > current and installed['architecture'] == 'amd64'
                    and re.fullmatch(r'[0-9a-f]{40}', installed['build_commit'] or '')
                    and installed['build_commit'] == candidate['base_commit']
                    and installed['build_commit'] != candidate['commit'] and time.time() < candidate['expires_at'])

    def status(self, identifier=None):
        with lifecycle_lock(self.layout):
            latest = self.read('latest.json')
            job = self.job(identifier or latest['id']) if identifier or latest else None
            candidate = self.read('candidate.json')
            public = {k: v for k, v in (candidate or {}).items() if k != 'manifest'}
            return {'installed': self.installed(), 'candidate': public or None,
                    'available': self.eligible(candidate), 'operation': self.public_job(job)}

    @staticmethod
    def public_job(job):
        return {k: v for k, v in job.items() if k not in {'request', 'candidate', 'execution'}} if job else None

    def submit(self, action, body):
        keys = {'id'} if action == 'check' else {'id', 'candidate'}
        if (action not in {'check', 'update'} or not isinstance(body, dict) or set(body) != keys
                or not isinstance(body.get('id'), str) or not ID.fullmatch(body['id'])
                or (action == 'update' and not re.fullmatch(r'[0-9a-f]{64}', str(body['candidate'])))):
            raise ManagementError('INVALID_GATEWAY_UPDATE_REQUEST')
        identifier = body['id']
        with lifecycle_lock(self.layout):
            old = self.read(identifier + '.json')
            request = {'action': action, **body}
            if old:
                if old['request'] != request: raise ManagementError('JOB_ID_REUSED')
                return self.public_job(self.job(identifier))
            latest = self.read('latest.json')
            if latest and self.job(latest['id'])['status'] in ACTIVE: raise ManagementError('MANAGEMENT_BUSY')
            candidate = self.read('candidate.json') if action == 'update' else None
            if action == 'update' and (not self.eligible(candidate) or candidate['token'] != body['candidate']):
                raise ManagementError('GATEWAY_UPDATE_CHECK_REQUIRED')
            # Checks never install. Updates reserve a pinned identity, never re-read /latest.
            with operation_lock(self.layout): require_idle(self.layout)
            job = {'id': identifier, 'kind': 'gateway_' + action, 'action': action, 'request': request,
                   'status': 'queued', 'stage': 'queued', 'created_at': time.time(), 'candidate': candidate}
            self.save(job); write_json(self.layout, BASE + 'latest.json', {'id': identifier})
            if action == 'check': write_json(self.layout, BASE + 'candidate.json', None)
            try: self.start(identifier)
            except Exception:
                job.update(status='failed', stage='complete', error='GATEWAY_UPDATE_START_FAILED', finished_at=time.time())
                self.save(job)
            # Bound metadata; retain the current job and recent terminal jobs for refresh recovery.
            paths = sorted(self.layout.path(BASE).glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths[100:]:
                if ID.fullmatch(path.stem) and path.stem != identifier: path.unlink(missing_ok=True)
            return self.public_job(job)

    def install_package(self, package, archive, candidate):
        # Incoming installer understands its own package layout and owns the deployment lock.
        # This worker is a separate systemd unit and survives the settings/core restart.
        script = '''import sys,json
sys.path.insert(0, sys.argv[1])
from manage_common import Layout,operation_lock,read_bytes,ManagementError
from manage_deploy import Deployer
layout=Layout(sys.argv[5])
with operation_lock(layout):
    if json.loads(read_bytes(layout,'usr/local/lib/paopaogw/build.json',b'{}')).get('build_commit') != sys.argv[4]:
        raise ManagementError('GATEWAY_UPDATE_CHECK_REQUIRED')
    Deployer(layout).deploy(sys.argv[2],sys.argv[3],startup='preserve')
'''
        _, _, _, asset = identity(candidate['manifest'])
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(package / 'lib'),
                                 str(archive), asset['sha256'], candidate['base_commit'], str(self.layout.root)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            journal = json.loads(read_bytes(self.layout, JOURNAL, b'{}'))
            code = {'rolled_back': 'DEPLOY_FAILED_ROLLED_BACK', 'conflict': 'EXTERNAL_CONFIG_CONFLICT',
                    'rollback_unconfirmed': 'ROLLBACK_UNCONFIRMED'}.get(journal.get('phase'), 'GATEWAY_INSTALL_FAILED')
            raise ManagementError(code)

    def run(self, identifier):
        with lifecycle_lock(self.layout):
            job = self.job(identifier)
            if job['status'] != 'queued': return
            job.update(status='running', stage='checking' if job['action'] == 'check' else 'downloading',
                       started_at=time.time(), execution=execution(self.unit(identifier)))
            self.save(job)
        try:
            with tempfile.TemporaryDirectory(prefix='lovestgateway-update-') as temporary:
                stage = Path(temporary)
                if job['action'] == 'check':
                    try:
                        manifest = json.loads(self.fetch(RELEASES + '/latest/download/release-manifest.json', stage / 'release.json', 1024*1024))
                        target, commit, _, _ = identity(manifest)
                        if not re.fullmatch(r'\d+\.\d+\.\d+', target): raise ValueError()
                    except (OSError, ValueError, TypeError, KeyError):
                        raise ManagementError('GATEWAY_RELEASE_CHECK_FAILED') from None
                    candidate = {'version': target, 'commit': commit, 'manifest': manifest,
                                 'base_commit': self.installed()['build_commit'], 'checked_at': time.time(),
                                 'expires_at': time.time() + TTL, 'release_url': RELEASES + '/tag/v' + target}
                    candidate['token'] = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
                    write_json(self.layout, BASE + 'candidate.json', candidate)
                    job['outcome'] = 'update_available' if self.eligible(candidate) else 'checked'
                else:
                    candidate = job['candidate']
                    if not self.eligible(candidate): raise ManagementError('GATEWAY_UPDATE_CHECK_REQUIRED')
                    target, commit, name, asset = identity(candidate['manifest'])
                    try: raw = self.fetch(RELEASES + '/download/v' + target + '/' + name, stage / name, asset['bytes'])
                    except (OSError, ValueError): raise ManagementError('GATEWAY_DOWNLOAD_FAILED') from None
                    job['stage'] = 'verifying'; self.save(job)
                    try: unpack(raw, stage / 'package', candidate['manifest'])
                    except Exception: raise ManagementError('GATEWAY_RELEASE_INVALID') from None
                    if not self.eligible(candidate): raise ManagementError('GATEWAY_UPDATE_CHECK_REQUIRED')
                    job['stage'] = 'installing'; self.save(job)
                    self.install(stage / 'package', stage / name, candidate)
                    if self.installed()['build_commit'] != commit or self.installed()['version'] != target:
                        raise ManagementError('GATEWAY_INSTALLED_VERSION_MISMATCH')
                    journal = json.loads(read_bytes(self.layout, JOURNAL, b'{}'))
                    if journal.get('phase') != 'complete': raise ManagementError('RECOVERY_REQUIRED')
                    if journal.get('backup'): job['backup'] = journal['backup']
                    job['outcome'] = 'upgraded'
                job.update(status='succeeded', stage='complete')
        except Exception as error:
            code = str(error) if isinstance(error, ManagementError) and re.fullmatch(r'[A-Z_]{3,64}', str(error)) else 'GATEWAY_UPDATE_FAILED'
            job.update(status='failed', stage='complete', error=code)
        finally:
            job['finished_at'] = time.time()
            with lifecycle_lock(self.layout): self.save(job)


if __name__ == '__main__':
    if len(sys.argv) != 2 or not ID.fullmatch(sys.argv[1]): raise SystemExit(2)
    GatewayUpdate().run(sys.argv[1])
