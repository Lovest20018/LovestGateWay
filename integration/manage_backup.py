"""Private, checksummed file snapshots with an explicit managed-path boundary."""
import json
import os
import re
import stat
import time
import uuid
from manage_common import ManagementError, atomic_write, read_bytes, write_json, digest, operation_lock


def generated(path):
    return '__pycache__' in path.split('/') or path.endswith(('.pyc', '.pyo'))

ROOTS = ('etc/paopaogw', 'usr/local/lib/paopaogw', 'var/lib/paopaogw')
UNITS = ('paopaogw.service', 'paopaogw-api.service', 'paopaogw-subscription.service',
         'paopaogw-settings.service',
         'paopaogw-observe.service', 'paopaogw-maintenance.service', 'paopaogw-maintenance.timer',
         'paopaogw-core-action@.service', 'paopaogw-gateway-update@.service')
EXACT = {'usr/local/bin/ppgw-admin', *(f'etc/systemd/system/{name}' for name in UNITS)}


def managed(path):
    return (path in EXACT or any(path == root or path.startswith(root + '/') for root in ROOTS)) and not (
        path.startswith('var/lib/paopaogw/admin/') or path in {'usr/local/lib/paopaogw/bin/clash',
        'usr/local/lib/paopaogw/bin/clash.previous'})


class BackupStore:
    base = 'var/backups/paopaogw'
    def __init__(self, layout): self.layout = layout
    def directory(self, identifier):
        if not re.fullmatch(r'[0-9]{14}-[0-9a-f]{12}', identifier): raise ManagementError('INVALID_BACKUP_ID')
        return self.layout.path(self.base + '/' + identifier)
    def list(self):
        base = self.layout.path(self.base)
        if not base.exists(): return []
        return sorted([p.name for p in base.iterdir() if re.fullmatch(r'[0-9]{14}-[0-9a-f]{12}', p.name)], reverse=True)
    def paths(self):
        result = set(EXACT)
        for root in ROOTS:
            directory = self.layout.path(root)
            if directory.exists():
                for item in directory.rglob('*'):
                    relative = item.relative_to(self.layout.root).as_posix()
                    if managed(relative) and not generated(relative):
                        self.layout.check(item)
                        if item.is_file(): result.add(relative)
        return sorted(p for p in result if managed(p))
    def create(self, paths=None, metadata=None):
        self.layout.require_root()
        identifier = time.strftime('%Y%m%d%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:12]
        directory = self.directory(identifier); directory.mkdir(parents=True, mode=0o700)
        if os.name != 'nt': directory.parent.chmod(0o700)
        records = {}
        for index, path in enumerate(sorted(set(paths if paths is not None else self.paths()))):
            if not managed(path): raise ManagementError('UNMANAGED_BACKUP_PATH')
            source = self.layout.path(path)
            if source.exists() and not source.is_file(): raise ManagementError('NOT_REGULAR_FILE')
            raw = read_bytes(self.layout, path, limit=512 * 1024 * 1024)
            if raw is None: records[path] = None; continue
            info = source.stat(); data = f'{self.base}/{identifier}/data/{index:06d}'
            atomic_write(self.layout, data, raw)
            records[path] = {'data': f'{index:06d}', 'sha256': digest(raw), 'mode': stat.S_IMODE(info.st_mode),
                             'uid': getattr(info, 'st_uid', 0), 'gid': getattr(info, 'st_gid', 0)}
        write_json(self.layout, f'{self.base}/{identifier}/index.json', {'files': records, 'metadata': metadata or {}, 'full': paths is None})
        return identifier
    def index(self, identifier):
        directory = self.directory(identifier)
        try: value = json.loads(read_bytes(self.layout, f'{self.base}/{identifier}/index.json', limit=8*1024*1024))
        except (OSError, ValueError): raise ManagementError('INVALID_BACKUP') from None
        if not isinstance(value, dict) or not isinstance(value.get('files'), dict): raise ManagementError('INVALID_BACKUP')
        for path, record in value['files'].items():
            if not managed(path): raise ManagementError('UNMANAGED_BACKUP_PATH')
            self.layout.path(path)
            if record is not None:
                if not re.fullmatch(r'\d{6}', record.get('data', '')): raise ManagementError('INVALID_BACKUP')
                raw = read_bytes(self.layout, f'{self.base}/{identifier}/data/{record["data"]}', limit=512 * 1024 * 1024)
                if raw is None or digest(raw) != record.get('sha256'): raise ManagementError('BACKUP_CHECKSUM_MISMATCH')
        return value
    def restore(self, identifier, expected=None):
        with operation_lock(self.layout):
            value = self.index(identifier)
            # Validate every target before restoring the first file.
            if expected is not None:
                for path, allowed in expected.items():
                    if generated(path): continue  # compatibility with early snapshots containing bytecode
                    raw = read_bytes(self.layout, path, limit=512 * 1024 * 1024)
                    actual = None if raw is None else digest(raw)
                    if actual not in allowed: raise ManagementError('EXTERNAL_CONFIG_CONFLICT')
            for path, record in value['files'].items():
                if generated(path): continue  # old snapshots may contain disposable bytecode
                target = self.layout.path(path)
                if record is None: target.unlink(missing_ok=True); continue
                raw = read_bytes(self.layout, f'{self.base}/{identifier}/data/{record["data"]}', limit=512 * 1024 * 1024)
                atomic_write(self.layout, path, raw, record['mode'])
                target.chmod(record['mode'] & 0o777)
                if hasattr(os, 'chown'): os.chown(target, record['uid'], record['gid'])
            return value['metadata']
