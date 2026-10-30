"""Small filesystem and locking primitives shared by all administrator entry points."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading


class ManagementError(ValueError):
    """A public, credential-free error code."""


class Layout:
    def __init__(self, root='/'):
        original = Path(root).absolute()
        if original.is_symlink():
            raise ManagementError('UNSAFE_ROOT')
        self.root = original.resolve()

    @property
    def live(self):
        return self.root == Path('/').resolve()

    def path(self, relative):
        name = str(relative)
        parts = PurePosixPath(name)
        if (not name or '\\' in name or ':' in name or parts.is_absolute()
                or any(p in {'.', '..'} for p in name.split('/'))):
            raise ManagementError('UNSAFE_PATH')
        candidate = self.root.joinpath(*parts.parts)
        self.check(candidate)
        return candidate

    def check(self, path):
        path = Path(path).absolute()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ManagementError('OUTSIDE_MANAGED_ROOT') from None
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ManagementError('SYMLINK_REFUSED')
        return path

    def require_root(self):
        if self.live and (not hasattr(os, 'geteuid') or os.geteuid() != 0):
            raise ManagementError('ROOT_REQUIRED')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bytes(layout, relative, default=None, limit=2 * 1024 * 1024):
    path = layout.path(relative)
    if not path.exists():
        return default
    if not path.is_file() or path.stat().st_size > limit:
        raise ManagementError('INVALID_FILE')
    return path.read_bytes()


def atomic_write(layout, relative, raw, mode=0o600):
    path = layout.path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.check(path)
    previous = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(temporary, stat.S_IMODE(previous.st_mode) if previous else mode)
            if previous and hasattr(os, 'chown'):
                os.chown(temporary, previous.st_uid, previous.st_gid)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(layout, relative, value):
    atomic_write(layout, relative, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def require_idle(layout):
    raw = read_bytes(layout, 'var/lib/paopaogw/admin/transaction.json')
    if raw:
        try: phase = json.loads(raw)['phase']
        except (ValueError, KeyError): raise ManagementError('INVALID_TRANSACTION_JOURNAL') from None
        if phase not in {'complete', 'rolled_back'}: raise ManagementError('RECOVERY_REQUIRED')


_local = threading.local()


def operation_lock_descriptor(layout):
    """Share the held POSIX flock with a writer subprocess for its entire lifetime."""
    stream = getattr(_local, 'locks', {}).get(str(layout.root))
    if stream is None: raise ManagementError('MANAGEMENT_LOCK_REQUIRED')
    return stream.fileno()


@contextmanager
def operation_lock(layout=None, reentrant=True):
    layout = layout or Layout()
    layout.require_root()
    key = str(layout.root)
    held = getattr(_local, 'locks', {})
    if reentrant and key in held:
        yield
        return
    path = layout.path('var/lib/paopaogw/admin/management.lock')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    layout.check(path)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ManagementError('MANAGEMENT_BUSY') from None
        else:
            import fcntl
            os.fchmod(stream.fileno(), 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ManagementError('MANAGEMENT_BUSY') from None
        _local.locks = {**held, key: stream}
        try:
            yield
        finally:
            _local.locks = held
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
