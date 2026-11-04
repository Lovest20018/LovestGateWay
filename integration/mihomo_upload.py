"""Bounded, authenticated staging for owner-supplied Mihomo archives."""
import base64
import binascii
from contextlib import contextmanager
import hashlib
import json
import re
import shutil
import time
from manage_common import Layout, atomic_write, operation_lock, require_idle
from mihomo_update import STATE, MAX_COMPRESSED

# Keep base64 JSON below the existing native core/update 32 KiB body limit.
CHUNK_BYTES = 16 * 1024
UPLOAD_TTL = 3600
MAX_UPLOADS = 2


class UploadStore:
    def __init__(self, state=STATE):
        self.state = state
        self.layout = Layout(state.parents[3])
        self.relative = state.relative_to(self.layout.root).as_posix()

    def path(self, name):
        return self.layout.path(self.relative + '/' + name)

    @contextmanager
    def locked(self):
        with operation_lock(self.layout):
            require_idle(self.layout)
            yield

    def directory(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('INVALID_CORE_UPLOAD')
        return self.path('uploads/' + identifier)

    def metadata(self, identifier):
        directory = self.directory(identifier)
        path = directory / 'metadata.json'; self.layout.check(path)
        if not path.is_file() or path.stat().st_size > 2048: raise ValueError('CORE_UPLOAD_EXPIRED')
        try: value = json.loads(path.read_bytes())
        except (OSError, ValueError): raise ValueError('INVALID_CORE_UPLOAD') from None
        if not isinstance(value, dict) or value.get('id') != identifier: raise ValueError('INVALID_CORE_UPLOAD')
        return value

    def clear(self, identifier):
        directory = self.directory(identifier)
        # Remove only known files, never recursively remove a client-provided path.
        for name in ('asset.gz', 'metadata.json'):
            path = directory / name; self.layout.check(path); path.unlink(missing_ok=True)
        directory.rmdir()

    def begin(self, body):
        if set(body) != {'operation', 'id', 'version', 'sha256', 'size'}: raise ValueError('INVALID_CORE_UPLOAD')
        identifier, version, sha, size = (body.get(key) for key in ('id', 'version', 'sha256', 'size'))
        directory = self.directory(identifier)
        if (not isinstance(version, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version)
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)
                or type(size) is not int or not 0 < size <= MAX_COMPRESSED):
            raise ValueError('INVALID_CORE_UPLOAD')
        with self.locked():
            uploads = self.path('uploads'); uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.exists():
                value = self.metadata(identifier)
                if any(value.get(key) != body[key] for key in ('version', 'sha256', 'size')) or value.get('ready'):
                    raise ValueError('INVALID_CORE_UPLOAD')
                return {'id': identifier, 'received': self.asset(identifier).stat().st_size, 'chunk_bytes': CHUNK_BYTES}
            active = 0
            for child in uploads.iterdir():
                if not child.is_dir() or child.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}', child.name):
                    raise ValueError('INVALID_CORE_UPLOAD')
                meta = self.metadata(child.name)
                if time.time() - meta.get('created_at', 0) > UPLOAD_TTL: self.clear(child.name)
                else: active += 1
            if active >= MAX_UPLOADS: raise ValueError('CORE_UPLOAD_BUSY')
            if shutil.disk_usage(uploads).free < size + 16 * 1024 * 1024: raise ValueError('CORE_DISK_FULL')
            directory.mkdir(mode=0o700)
            try:
                atomic_write(self.layout, self.relative + '/uploads/' + identifier + '/asset.gz', b'')
                value = {key: body[key] for key in ('id', 'version', 'sha256', 'size')}
                value.update(created_at=time.time(), ready=False)
                atomic_write(self.layout, self.relative + '/uploads/' + identifier + '/metadata.json', json.dumps(value).encode())
            except Exception:
                self.clear(identifier)
                raise
            return {'id': identifier, 'received': 0, 'chunk_bytes': CHUNK_BYTES}

    def asset(self, identifier):
        path = self.directory(identifier) / 'asset.gz'; self.layout.check(path)
        if not path.is_file(): raise ValueError('CORE_UPLOAD_EXPIRED')
        return path

    def chunk(self, body):
        if set(body) != {'operation', 'id', 'offset', 'data'}: raise ValueError('INVALID_CORE_UPLOAD')
        identifier, offset, encoded = body['id'], body['offset'], body['data']
        if type(offset) is not int or offset < 0 or not isinstance(encoded, str) or not 0 < len(encoded) <= 4 * ((CHUNK_BYTES + 2) // 3):
            raise ValueError('INVALID_CORE_UPLOAD')
        try: raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error): raise ValueError('INVALID_CORE_UPLOAD') from None
        if not 0 < len(raw) <= CHUNK_BYTES: raise ValueError('INVALID_CORE_UPLOAD')
        with self.locked():
            meta = self.metadata(identifier)
            if meta.get('ready') or offset + len(raw) > meta['size']: raise ValueError('INVALID_CORE_UPLOAD')
            asset = self.asset(identifier); received = asset.stat().st_size
            with asset.open('r+b') as stream:
                if offset < received:
                    stream.seek(offset)
                    if offset + len(raw) > received or stream.read(len(raw)) != raw: raise ValueError('CORE_UPLOAD_OFFSET')
                elif offset == received:
                    stream.seek(offset); stream.write(raw); stream.flush()
                    import os
                    os.fsync(stream.fileno())
                    received += len(raw)
                else: raise ValueError('CORE_UPLOAD_OFFSET')
            return {'id': identifier, 'received': received}

    def commit(self, identifier):
        with self.locked():
            meta = self.metadata(identifier); asset = self.asset(identifier)
            if asset.stat().st_size != meta['size']: raise ValueError('CORE_UPLOAD_INCOMPLETE')
            with asset.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != meta['sha256']: raise ValueError('MIHOMO_CHECKSUM_MISMATCH')
            meta['ready'] = True
            atomic_write(self.layout, self.relative + '/uploads/' + identifier + '/metadata.json', json.dumps(meta).encode())
            atomic_write(self.layout, self.relative + '/local-install.json', json.dumps({'id': identifier}).encode())
            return {'id': identifier, 'version': meta['version']}

    def pending(self):
        with self.locked():
            try: value = json.loads(self.path('local-install.json').read_bytes())
            except (OSError, ValueError): raise ValueError('CORE_UPLOAD_EXPIRED') from None
            if not isinstance(value, dict) or set(value) != {'id'}: raise ValueError('INVALID_CORE_UPLOAD')
            meta = self.metadata(value['id'])
            if not meta.get('ready'): raise ValueError('CORE_UPLOAD_INCOMPLETE')
            return meta, self.asset(value['id'])

    def cancel(self, identifier):
        with self.locked():
            self.clear(identifier)
            pending = self.path('local-install.json')
            if pending.is_file():
                try: value = json.loads(pending.read_bytes())
                except ValueError: value = None
                if isinstance(value, dict) and value.get('id') == identifier: pending.unlink()
            return {'status': 'cancelled', 'id': identifier}
