"""Verify local releases before execution and upgrade under a recoverable snapshot."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
from manage_common import ManagementError, operation_lock, operation_lock_descriptor, read_bytes, write_json, digest
from manage_backup import BackupStore, UNITS
from manage_model import load_settings
from manage_runtime import System
from manage_config import JOURNAL

LIB = {'runtime.sh', 'network.py', 'launch.py', 'test_profile.py', 'ppg.sh', 'nft.sh', 'gateway_api.py',
       'staged_probe.py', 'subscription_admin.py', 'clash_ppsub.py', 'clash_policy.py', 'mihomo_update.py',
       'maintenance.py', 'history_store.py', 'observe.py', 'base.yaml', 'build.json', 'bin/sniffbox',
       'manage.py', 'manage_common.py', 'manage_model.py', 'manage_runtime.py', 'manage_config.py',
       'manage_backup.py', 'manage_deploy.py', 'manage_assets.py', 'ppgw-admin'}
TOP = {'install.py', 'uninstall.sh', 'install.sh', 'deploy.py', 'LICENSE'}
DOCS = {'debian-deployment.md', 'debian-management.md', 'integration-api-v1.md', 'openapi-v1.json'}


def allowed(name):
    return name in TOP or name in {'lib/' + x for x in LIB} or name in {'units/' + x for x in UNITS} or name in {'docs/' + x for x in DOCS} or (
        name.startswith('ui/') and bool(re.fullmatch(r'[A-Za-z0-9_./@+-]+', name)))


@contextmanager
def verify_package(path, sha256):
    path = Path(path)
    if not re.fullmatch(r'[0-9a-f]{64}', sha256 or '') or path.is_symlink() or not path.is_file() or path.stat().st_size > 256*1024*1024:
        raise ManagementError('INVALID_RELEASE_INPUT')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != sha256: raise ManagementError('RELEASE_SHA256_MISMATCH')
        stream.seek(0)
        with tempfile.TemporaryDirectory(prefix='ppgw-release-') as temporary:
            output = Path(temporary); records = {}; root = None; total = 0
            try:
                with tarfile.open(fileobj=stream, mode='r:gz') as archive:
                    for member in archive:
                        parts = PurePosixPath(member.name).parts
                        if (not parts or member.name.startswith('/') or '\\' in member.name or ':' in member.name
                                or any(x in {'.', '..', ''} for x in member.name.split('/'))
                                or not (member.isdir() or member.isfile()) or member.mode & 0o7000):
                            raise ManagementError('UNSAFE_RELEASE_ENTRY')
                        if root is None: root = parts[0]
                        if parts[0] != root: raise ManagementError('MULTIPLE_RELEASE_ROOTS')
                        name = '/'.join(parts[1:])
                        if member.isdir(): continue
                        if not name or name in records or (name != 'manifest.json' and not allowed(name)):
                            raise ManagementError('UNEXPECTED_RELEASE_FILE')
                        total += member.size
                        if len(records) >= 4096 or member.size > 128*1024*1024 or total > 512*1024*1024: raise ManagementError('RELEASE_TOO_LARGE')
                        raw = archive.extractfile(member).read(member.size + 1)
                        if len(raw) != member.size: raise ManagementError('TRUNCATED_RELEASE')
                        target = output / name; target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw); target.chmod(member.mode & 0o777)
                        records[name] = digest(raw)
                manifest = json.loads((output / 'manifest.json').read_bytes())
                if not isinstance(manifest, dict) or manifest != {k:v for k,v in records.items() if k != 'manifest.json'}: raise ManagementError('RELEASE_MANIFEST_MISMATCH')
                build = json.loads((output / 'lib/build.json').read_bytes())
                header = (output / 'lib/bin/sniffbox').read_bytes()[:20]
                if build.get('architecture') != 'amd64' or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00': raise ManagementError('RELEASE_ARCHITECTURE_MISMATCH')
                if 'install.py' not in records: raise ManagementError('INSTALLER_MISSING')
                yield SimpleNamespace(path=output, build=build, manifest=manifest)
            except ManagementError:
                raise
            except (OSError, ValueError, tarfile.TarError, KeyError):
                raise ManagementError('INVALID_RELEASE') from None


class Deployer:
    def __init__(self, layout, system=None, installer=None):
        self.layout = layout; self.system = system or System(layout); self.store = BackupStore(layout)
        self.installer = installer or self.install
    def install(self, package):
        with operation_lock(self.layout):
            # SIGKILL of this process must not release exclusion while install.py still writes.
            inherited = {'pass_fds': (operation_lock_descriptor(self.layout),)} if os.name == 'posix' else {}
            result = subprocess.run([os.sys.executable, '-B', str(package.path / 'install.py'), '--root', str(self.layout.root)],
                                    capture_output=True, timeout=120, **inherited)
        if result.returncode: raise ManagementError('INSTALLER_FAILED')
    @contextmanager
    def candidate(self, package, fresh, interface):
        """Precompute generated credentials/defaults in a staging root, before live writes."""
        from manage_common import Layout, atomic_write
        from manage_model import render_updates
        with tempfile.TemporaryDirectory(prefix='ppgw-install-plan-') as temporary:
            stage = Layout(temporary)
            existing = self.layout.path('etc/paopaogw')
            if existing.exists():
                for source in existing.rglob('*'):
                    self.layout.check(source)
                    if source.is_file():
                        relative = source.relative_to(self.layout.root).as_posix()
                        atomic_write(stage, relative, source.read_bytes())
            result = subprocess.run([os.sys.executable, '-B', str(package.path / 'install.py'), '--root', str(stage.root)], capture_output=True, timeout=120)
            if result.returncode: raise ManagementError('INSTALLER_PREFLIGHT_FAILED')
            if fresh:
                updates, _ = render_updates(stage, {'service': {'PPGW_INTERFACE': interface}})
                for path, raw in updates.items(): atomic_write(stage, path, raw)
            candidates = {}
            for name in package.manifest:
                if name.startswith('lib/'): candidates['usr/local/lib/paopaogw/' + name[4:]] = (package.path / name).read_bytes()
                if name.startswith('ui/'): candidates['var/lib/paopaogw/clash/clash-dashboard/' + name] = (package.path / name).read_bytes()
                if name.startswith('units/'): candidates['etc/systemd/system/' + name[6:]] = (package.path / name).read_bytes()
            if 'lib/ppgw-admin' in package.manifest:
                candidates['usr/local/bin/ppgw-admin'] = (package.path / 'lib/ppgw-admin').read_bytes()
            config = stage.path('etc/paopaogw')
            if config.exists():
                for source in config.rglob('*'):
                    if source.is_file(): candidates[source.relative_to(stage.root).as_posix()] = source.read_bytes()
            hosts = stage.path('var/lib/paopaogw/hosts')
            if hosts.exists() and not self.layout.path('var/lib/paopaogw/hosts').exists(): candidates['var/lib/paopaogw/hosts'] = hosts.read_bytes()
            if self.layout.live: self.system.preflight(load_settings(stage))
            yield candidates
    def deploy(self, archive, sha256, interface=None):
        with verify_package(archive, sha256) as package, operation_lock(self.layout):
            current = read_bytes(self.layout, JOURNAL)
            if current and json.loads(current)['phase'] not in {'complete', 'rolled_back'}: raise ManagementError('RECOVERY_REQUIRED')
            fresh = not any(self.layout.path('etc/paopaogw/' + p).exists() for p in ('files/ppgw.ini', 'instance-id'))
            if fresh and not interface: raise ManagementError('FIRST_INSTALL_INTERFACE_REQUIRED')
            with self.candidate(package, fresh, interface) as candidates:
                return self._apply(package, candidates, fresh)

    def _apply(self, package, candidates, fresh):
            from manage_common import atomic_write
            before = self.system.status()
            # All new files are included as absent records so rollback removes a partial install.
            paths = set(self.store.paths()) | set(candidates)
            # Persist rollback intent before stopping services. Snapshot after stop for SQLite consistency.
            journal = {'kind': 'deploy', 'phase': 'stopping', 'services': before, 'backup': None}
            write_json(self.layout, JOURNAL, journal)
            try:
                self.system.stop_all()
                backup = self.store.create(paths, {'services': before, 'kind': 'deploy'})
                original = self.store.index(backup)['files']
                # Runtime logs/SQLite are expected to change when services resume; snapshots restore them.
                # External-edit protection applies to persistent configuration and installed program files.
                expected = {path: [None if record is None else record['sha256']] for path,record in original.items() if not path.startswith('var/lib/')}
                for path, raw in candidates.items():
                    if path in expected: expected[path].append(digest(raw))
                journal.update(phase='installing', backup=backup, expected=expected); write_json(self.layout, JOURNAL, journal)
                # Seed the exact generated config so the live installer never invents different credentials.
                for path, raw in candidates.items():
                    if path.startswith('etc/paopaogw/') or path == 'var/lib/paopaogw/hosts':
                        old = read_bytes(self.layout, path)
                        if path in expected and (None if old is None else digest(old)) not in expected[path]: raise ManagementError('EXTERNAL_CONFIG_CONFLICT')
                        mode = 0o640 if path.rsplit('/', 1)[-1] in {'api.json', 'instance-id', 'read-token', 'service.env', 'network', 'maintenance.json', 'hosts'} else 0o600
                        if raw != old: atomic_write(self.layout, path, raw, mode)
                self.installer(package)
                for path, raw in candidates.items():
                    actual = read_bytes(self.layout, path, limit=512*1024*1024)
                    if actual is None or digest(actual) != digest(raw): raise ManagementError('INSTALLED_FILE_MISMATCH')
                if fresh:
                    before = {unit: dict(state) for unit,state in before.items()}
                    if self.layout.live:
                        for unit in ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-observe', 'paopaogw-maintenance.timer'):
                            before[unit] = {'active': True, 'enabled': True}
                self.system.restore_services(before)
                if before.get('paopaogw', {}).get('active'): self.system.wait(load_settings(self.layout))
                if before.get('paopaogw-api', {}).get('active') and hasattr(self.system, 'wait_api'): self.system.wait_api(load_settings(self.layout))
                journal['phase'] = 'complete'; write_json(self.layout, JOURNAL, journal)
                return {'status': 'installed' if fresh else 'upgraded', 'backup': backup, 'version': package.build['version']}
            except Exception:
                self.recover()
                raise ManagementError('DEPLOY_FAILED_ROLLED_BACK') from None
    def recover(self):
        with operation_lock(self.layout):
            journal = json.loads(read_bytes(self.layout, JOURNAL, b'{}'))
            if journal.get('phase') in {None, 'complete', 'rolled_back'}: return {'status': 'nothing_to_recover'}
            self.system.stop_all()
            if journal.get('backup'):
                try: self.store.restore(journal['backup'], expected=journal.get('expected'))
                except ManagementError:
                    journal['phase'] = 'conflict'; write_json(self.layout, JOURNAL, journal)
                    raise ManagementError('EXTERNAL_CONFIG_CONFLICT') from None
            self.system.restore_services(journal['services'])
            journal['phase'] = 'rolled_back'; write_json(self.layout, JOURNAL, journal)
            return {'status': 'rolled_back', 'backup': journal.get('backup')}
