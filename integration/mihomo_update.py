#!/usr/bin/env python3
"""Official MetaCubeX/mihomo releases, verified and installed transactionally."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import gzip
import errno
import http.client
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

API = 'https://api.github.com/repos/MetaCubeX/mihomo/releases/latest'
CODE = Path('/usr/local/lib/paopaogw')
STATE = Path('/var/lib/paopaogw/maintenance')
CONFIG = Path('/etc/paopaogw/maintenance.json')
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024
CHECK_CACHE_SECONDS = 300


ERROR_MESSAGES = {
    'INVALID_CORE_UPLOAD': '本地核心文件或上传参数无效，请重新选择官方 .gz 文件、版本和 SHA256。',
    'CORE_UPLOAD_EXPIRED': '上传任务不存在或已过期，请重新选择文件上传。',
    'CORE_UPLOAD_BUSY': '存在其他待处理的核心上传，请取消原上传或稍后重试。',
    'CORE_UPLOAD_OFFSET': '文件上传位置不一致，请重新选择文件上传。',
    'CORE_UPLOAD_INCOMPLETE': '文件尚未上传完整，请完成上传后安装。',
    'INVALID_OFFLINE_ASSET': '离线文件、版本或 SHA256 参数无效，请核对离线包。',
    'RELEASE_DNS_FAILED': '无法解析 GitHub 域名，请检查这台 Debian 的 DNS 设置。',
    'RELEASE_NETWORK_TIMEOUT': '连接 GitHub 超时，请检查这台 Debian 的外网连接后重试。',
    'RELEASE_NETWORK_FAILED': '无法连接 GitHub，请检查这台 Debian 的外网连接。',
    'RELEASE_TLS_FAILED': 'GitHub HTTPS 连接失败，请检查系统时间、CA 证书和网络。',
    'RELEASE_RATE_LIMITED': 'GitHub 请求次数已达上限，请稍后重试。',
    'RELEASE_HTTP_FAILED': 'GitHub 拒绝请求或服务暂不可用，请稍后重试。',
    'RELEASE_DOWNLOAD_INTERRUPTED': 'GitHub 下载中断，请检查网络后重试。',
    'RELEASE_TOO_LARGE': '官方发布信息或下载文件超出允许大小。',
    'RELEASE_ASSET_MISSING': '官方稳定版缺少适用于当前 CPU 和所选型号的 Linux amd64 安装文件。',
    'INVALID_CORE_VARIANT': 'Mihomo 型号设置无效，请选择自动、v1、v2 或 v3。',
    'CORE_VARIANT_UNSUPPORTED': '当前系统未确认支持所选 CPU 指令集，请选择自动或较低型号。',
    'MIHOMO_INSTALLED_VERSION_UNKNOWN': '无法确认已安装核心的稳定版版本，已停止自动替换；可使用本地文件更新或回退。',
    'RELEASE_DIGEST_MISSING': '官方发布缺少可信 SHA256 校验信息，已停止安装。',
    'INVALID_STABLE_RELEASE': '官方稳定版信息无效，请稍后重试。',
    'UNSUPPORTED_ARCHITECTURE': '目前仅支持 Debian amd64 架构的 Mihomo。',
    'UNTRUSTED_RELEASE_URL': '下载地址不属于允许的官方来源，已停止下载。',
    'UNTRUSTED_RELEASE_REDIRECT': '官方请求跳转到了未允许的来源，已停止下载。',
    'MIHOMO_CHECKSUM_MISMATCH': '核心文件的大小或 SHA256 不匹配，已停止安装。',
    'MIHOMO_BINARY_TOO_LARGE': '解压后的 Mihomo 超出允许大小。',
    'INVALID_MIHOMO_ELF': '下载文件不是有效的 Linux amd64 Mihomo 程序。',
    'MIHOMO_VERSION_MISMATCH': '下载文件的版本与官方发布不符，已停止安装。',
    'MIHOMO_UPDATE_ROLLED_BACK': '新核心启动验收失败，已撤销本次安装或恢复原核心。',
    'MIHOMO_ROLLBACK_UNAVAILABLE': '尚无可回退的 Mihomo 版本。',
    'MIHOMO_ROLLBACK_FAILED': '回退验收失败，已尝试恢复回退前的核心。',
    'MANAGEMENT_BUSY': '其他管理任务正在进行，请稍后重试。',
    'RECOVERY_REQUIRED': '上次管理事务需要恢复，请先运行 sudo ppgw-admin recover --yes。',
    'CORE_PERMISSION_DENIED': '维护服务缺少文件访问权限，请检查安装和服务配置。',
    'CORE_DISK_FULL': '维护目录磁盘空间不足，请释放空间后重试。',
    'CORE_OPERATION_FAILED': '核心维护未完成，请检查维护服务状态。',
}


class ReleaseFetchError(ValueError):
    def __init__(self, code, url, http_status=None, retry_at=None):
        super().__init__(code)
        self.stage = 'metadata' if url == API else 'download'
        self.host = urlsplit(url).hostname
        self.http_status = http_status
        self.retry_at = retry_at


def error_details(error):
    # Never expose upstream response bodies, URLs with credentials, or raw exceptions.
    code = str(error) if isinstance(error, ValueError) else ''
    if isinstance(error, PermissionError): code = 'CORE_PERMISSION_DENIED'
    elif isinstance(error, OSError) and error.errno == errno.ENOSPC: code = 'CORE_DISK_FULL'
    if code not in ERROR_MESSAGES: code = 'CORE_OPERATION_FAILED'
    result = {'error': code, 'message': ERROR_MESSAGES[code]}
    if isinstance(error, ReleaseFetchError):
        result.update(stage=error.stage, host=error.host)
        if error.http_status is not None: result['http_status'] = error.http_status
        if type(error.retry_at) in (int, float) and 0 < error.retry_at < 4102444800:
            result['retry_at'] = error.retry_at
            when = datetime.fromtimestamp(error.retry_at, timezone.utc).isoformat(timespec='seconds')
            result['message'] += f' 可在 {when} 后重试；已安装的 Mihomo 可继续使用。'
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = path.with_name(path.name + '.new')
    fd = os.open(pending, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


@contextmanager
def operation_lock(state=STATE):
    from manage_common import Layout, require_idle, operation_lock as shared_lock
    layout = Layout(state.parents[3])
    with shared_lock(layout):
        require_idle(layout)
        yield


def official_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
            and not parsed.password and (parsed.hostname in {'api.github.com', 'github.com'}
            or (parsed.hostname or '').endswith('.githubusercontent.com')))


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        if not official_url(target):
            raise ValueError('UNTRUSTED_RELEASE_REDIRECT')
        return super().redirect_request(request, fp, code, message, headers, target)


def gateway_socket(host, port, timeout):
    """Keep the destination domain intact through the core's private SOCKS ingress."""
    deadline = time.monotonic() + timeout
    stream = socket.create_connection(('127.0.0.1', 1079), min(timeout, 1))

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0: raise TimeoutError()
        return value

    def receive(size):
        result = bytearray()
        while len(result) < size:
            stream.settimeout(remaining())
            data = stream.recv(size - len(result))
            if not data: raise ConnectionError('GATEWAY_SOCKS_CLOSED')
            result.extend(data)
        return bytes(result)

    try:
        name = host.encode('idna')
        if not 1 <= len(name) <= 253: raise ValueError('INVALID_RELEASE_HOST')
        stream.settimeout(remaining())
        stream.sendall(b'\x05\x01\x00')
        if receive(2) != b'\x05\x00': raise ConnectionError('GATEWAY_SOCKS_AUTH')
        stream.sendall(b'\x05\x01\x00\x03' + bytes([len(name)]) + name + port.to_bytes(2, 'big'))
        reply = receive(4)
        if reply[:3] != b'\x05\x00\x00': raise ConnectionError('GATEWAY_SOCKS_CONNECT')
        size = {1: 4, 4: 16}.get(reply[3])
        if reply[3] == 3: size = receive(1)[0]
        if not size: raise ConnectionError('GATEWAY_SOCKS_ADDRESS')
        receive(size + 2)
        stream.settimeout(remaining())
        return stream
    except BaseException:
        stream.close()
        raise


class GatewayHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        stream = gateway_socket(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(stream, server_hostname=self.host)
        except BaseException:
            stream.close()
            raise


class GatewayHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(GatewayHTTPSConnection, request, context=self._context)


def rate_limit_retry_at(headers):
    now = time.time()
    candidates = [now + 60]
    value = headers.get('Retry-After', '')
    if value.isdigit(): candidates.append(now + int(value))
    elif value:
        try: candidates.append(parsedate_to_datetime(value).timestamp())
        except (ValueError, TypeError, OverflowError): pass
    reset = headers.get('X-RateLimit-Reset', '')
    if headers.get('X-RateLimit-Remaining') == '0' and reset.isdigit(): candidates.append(int(reset) + 1)
    return int(min(max(candidates), now + 7 * 86400))


def fetch(url, limit):
    if not official_url(url):
        raise ValueError('UNTRUSTED_RELEASE_URL')
    try:
        return fetch_once(url, limit, gateway=True)
    except ReleaseFetchError as error:
        # Fresh installs may have no core. HTTP/rate-limit/TLS failures are final;
        # only a failed network path is retried directly, with the same checks.
        if str(error) not in {'RELEASE_DNS_FAILED', 'RELEASE_NETWORK_TIMEOUT',
                             'RELEASE_NETWORK_FAILED', 'RELEASE_DOWNLOAD_INTERRUPTED'}:
            raise
    return fetch_once(url, limit, gateway=False)


def fetch_once(url, limit, gateway):
    handlers = [ProxyHandler({}), OfficialRedirect()]
    if gateway: handlers.append(GatewayHTTPSHandler())
    opener = build_opener(*handlers)
    request = Request(url, headers={'User-Agent': 'LovestGateWay-Debian', 'Accept': 'application/vnd.github+json'})
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except HTTPError as error:
        headers = error.headers or {}
        limited = error.code == 429 or (error.code == 403 and (headers.get('X-RateLimit-Remaining') == '0' or headers.get('Retry-After')))
        retry_at = rate_limit_retry_at(headers) if limited else None
        status = error.code
        error.close()
        raise ReleaseFetchError('RELEASE_RATE_LIMITED' if limited else 'RELEASE_HTTP_FAILED', url, status, retry_at) from None
    except (URLError, OSError, http.client.HTTPException) as error:
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, socket.gaierror): code = 'RELEASE_DNS_FAILED'
        elif isinstance(reason, TimeoutError): code = 'RELEASE_NETWORK_TIMEOUT'
        elif isinstance(reason, ssl.SSLError): code = 'RELEASE_TLS_FAILED'
        elif isinstance(reason, http.client.HTTPException): code = 'RELEASE_DOWNLOAD_INTERRUPTED'
        else: code = 'RELEASE_NETWORK_FAILED'
        raise ReleaseFetchError(code, url) from None
    if len(data) > limit:
        raise ValueError('RELEASE_TOO_LARGE')
    return data


def cpu_capabilities(machine=None):
    """glibc checks CPU, OSXSAVE/XCR0 and hypervisor-visible features together.

    Debian 12+ has this loader. Do not infer AVX usability from /proc/cpuinfo
    alone; failure to obtain a positive result deliberately falls back to v1.
    """
    machine = machine or platform.machine()
    if machine not in {'x86_64', 'AMD64', 'amd64'}:
        raise ValueError('UNSUPPORTED_ARCHITECTURE')
    levels = ['v1']
    detected = False
    if platform.system() == 'Linux':
        for loader in (Path('/lib64/ld-linux-x86-64.so.2'), Path('/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2')):
            if not loader.is_file(): continue
            try:
                result = subprocess.run([str(loader), '--help'], capture_output=True, text=True,
                                        check=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
                found = [level for level in ('v2', 'v3') if re.search(
                    rf'^\s*x86-64-{level}\s+\(supported, searched\)\s*$', result.stdout, re.M)]
                if found: levels = ['v1', 'v2', 'v3'][:int(found[-1][1])]
                detected = any('x86-64-' in line for line in result.stdout.splitlines())
                if detected: break
            except (OSError, subprocess.SubprocessError): continue
    return {'cpu_level': levels[-1], 'cpu_detection': 'glibc-hwcaps' if detected else 'baseline-fallback',
            'supported_variants': levels}


def release_asset(release, machine=None, variant=None):
    capabilities = cpu_capabilities(machine)
    variant = variant or capabilities['cpu_level']
    if variant not in ('v1', 'v2', 'v3'): raise ValueError('INVALID_CORE_VARIANT')
    if variant not in capabilities['supported_variants']: raise ValueError('CORE_VARIANT_UNSUPPORTED')
    version = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('INVALID_STABLE_RELEASE')
    # Explicit modern names first. Older releases may only have the v1 alias.
    names = [f'mihomo-linux-amd64-{variant}-{version}.gz']
    if variant == 'v1': names.append(f'mihomo-linux-amd64-compatible-{version}.gz')
    matches = []
    for name in names:
        matches = [asset for asset in release.get('assets', []) if asset.get('name') == name]
        if matches: break
    if len(matches) != 1: raise ValueError('RELEASE_ASSET_MISSING')
    asset = matches[0]
    url = asset.get('browser_download_url', '')
    expected = f'https://github.com/MetaCubeX/mihomo/releases/download/{version}/{name}'
    if url != expected or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
        raise ValueError('RELEASE_DIGEST_MISSING')
    if type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_COMPRESSED:
        raise ValueError('RELEASE_TOO_LARGE')
    return {'version': version, 'asset': name, 'architecture': 'amd64-' + variant,
            'url': url, 'sha256': asset['digest'][7:], 'size': asset['size']}


def stable_version(value):
    if isinstance(value, str) and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', value):
        return tuple(int(part) for part in value[1:].split('.'))
    return None


def version_state(current, exists):
    installed, latest = stable_version(current.get('installed_version')), stable_version(current.get('latest_version'))
    if latest is None: return {}
    return {'update_available': not exists or (installed is not None and installed < latest),
            'variant_change_available': installed == latest and current.get('installed_architecture') is not None
                and current.get('latest_architecture') == current.get('architecture')
                and current['installed_architecture'] != current['architecture'],
            'installed_newer': installed is not None and installed > latest,
            'version_unrecognized': exists and installed is None}


@lru_cache(maxsize=8)
def _binary_variant(path, signature):
    # Go embeds this build setting even in stripped releases. Best effort only;
    # never label an unrecognized binary as the currently selected architecture.
    try:
        with open(path, 'rb') as stream:
            tail = b''
            for _ in range(MAX_BINARY // (1024 * 1024) + 1):
                chunk = stream.read(1024 * 1024)
                if not chunk: break
                match = re.search(rb'\nbuild\tGOAMD64=(v[1-4])\n', tail + chunk)
                if match: return 'amd64-' + match[1].decode('ascii')
                tail = chunk[-64:]
    except OSError: pass
    return None


def binary_variant(path):
    try:
        value = path.stat()
        return _binary_variant(str(path), (value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
    except OSError: return None


def version_of(binary):
    try:
        result = subprocess.run([str(binary), '-v'], capture_output=True, text=True, check=True, timeout=5)
        match = re.search(r'\bMihomo\s+(?:Meta\s+)?(\S+)', result.stdout, re.I)
        return match.group(1) if match else None
    except (OSError, subprocess.SubprocessError):
        return None


def validate_elf(data):
    if (len(data) < 1024 * 1024 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack('<H', data[18:20])[0] != 62):
        raise ValueError('INVALID_MIHOMO_ELF')


class Updater:
    def __init__(self, code=CODE, state=STATE, config=CONFIG):
        self.code, self.state = code, state
        self.config = config
        self.binary = code / 'bin/clash'
        self.previous = code / 'bin/clash.previous'

    def target(self):
        try: config = json.loads(self.config.read_bytes())
        except FileNotFoundError: config = {}
        except (ValueError, TypeError): raise ValueError('INVALID_CORE_VARIANT') from None
        preference = config.get('core_variant', 'auto') if isinstance(config, dict) else None
        if preference not in ('auto', 'v1', 'v2', 'v3'): raise ValueError('INVALID_CORE_VARIANT')
        capabilities = cpu_capabilities()
        variant = capabilities['cpu_level'] if preference == 'auto' else preference
        return {**capabilities, 'selection': preference, 'architecture': 'amd64-' + variant,
                'selection_supported': variant in capabilities['supported_variants']}

    def status(self):
        try:
            status = json.loads((self.state / 'status.json').read_bytes())
        except (OSError, ValueError):
            status = {}
        if not isinstance(status, dict): status = {}
        current = {**status, 'installed_version': version_of(self.binary), 'installed_architecture': binary_variant(self.binary),
                'rollback_available': self.previous.is_file(), 'source': 'MetaCubeX/mihomo', 'channel': 'stable', **self.target()}
        return {**current, **version_state(current, self.binary.exists())}

    def check(self):
        with operation_lock(self.state):
            return self._check()

    def _check(self):
        current = self.status()
        if not current['selection_supported']: raise ValueError('CORE_VARIANT_UNSUPPORTED')
        variant = current['architecture'].removeprefix('amd64-')
        now = time.time()
        retry_at = current.get('retry_at')
        if type(retry_at) in (int, float) and now < retry_at:
            raise ReleaseFetchError('RELEASE_RATE_LIMITED', API, current.get('http_status'), retry_at)
        cached = False
        try:
            cache = json.loads((self.state / 'release-cache.json').read_bytes())
            checked_at = cache['checked_at']
            if type(checked_at) not in (int, float) or not 0 <= now - checked_at < CHECK_CACHE_SECONDS:
                raise ValueError('STALE_RELEASE_CACHE')
            release = release_asset(cache['release'], variant=variant)
            cached = True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            try:
                document = json.loads(fetch(API, 2 * 1024 * 1024))
                release = release_asset(document, variant=variant)
            except Exception as error:
                details = error_details(error)
                status = {**current, 'last_check_result': 'failed', 'last_check_error': details['error'],
                          'last_check_message': details['message'], 'check_attempted_at': now}
                for key in ('retry_at', 'http_status'):
                    if key in details: status[key] = details[key]
                    else: status.pop(key, None)
                atomic_json(self.state / 'status.json', status)
                raise
            checked_at = now
            atomic_json(self.state / 'release-cache.json', {'checked_at': checked_at, 'release': document})
        status = {**current, 'latest_version': release['version'], 'checked_at': checked_at,
                  'latest_asset': release['asset'], 'latest_architecture': release['architecture'], 'latest_sha256': release['sha256'],
                  'last_check_result': 'success', 'metadata_cached': cached}
        status.update(version_state(status, self.binary.exists()))
        for key in ('retry_at', 'http_status', 'last_check_error', 'last_check_message'): status.pop(key, None)
        atomic_json(self.state / 'status.json', status)
        return release

    def validate_config(self, binary):
        result = subprocess.run(['systemctl', 'show', 'paopaogw', '--property=MainPID', '--value'],
                                capture_output=True, text=True, check=True, timeout=5)
        pid = result.stdout.strip()
        if not pid.isdigit() or int(pid) <= 0:
            return
        root = Path('/proc') / pid / 'root'
        if not (root / 'tmp/clash.yaml').is_file():
            return
        subprocess.run(['nsenter', '-t', pid, '-m', '--', str(binary), '-t',
                        '-d', '/etc/config/clash', '-f', '/tmp/clash.yaml'], check=True, timeout=40,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def restart_and_check(self, expected):
        from gateway_api import CoreClient
        client = CoreClient(Path('/var/lib/paopaogw/core-token'))
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'paopaogw']).returncode == 0
        if not active:
            return
        subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=40,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                info = client.request('/sniffbox/info')
                if info.get('mode') not in {'yaml', 'suburl'} or info.get('clash_version') == expected:
                    return
            except Exception:
                pass
            time.sleep(.5)
        raise ValueError('MIHOMO_HEALTH_CHECK_FAILED')

    def update(self, force=False):
        with operation_lock(self.state):
            release = self._check()
            installed = version_of(self.binary)
            previous = stable_version(installed)
            if self.binary.exists() and previous is None: raise ValueError('MIHOMO_INSTALLED_VERSION_UNKNOWN')
            if previous is not None and previous > stable_version(release['version']):
                return {'status': 'unchanged', 'version': installed, 'reason': 'installed_newer'}
            architecture = binary_variant(self.binary)
            variant_change = architecture is not None and architecture != release['architecture']
            if installed == release['version'] and not force and not variant_change:
                return {'status': 'unchanged', 'version': installed, 'reason': 'up_to_date'}
            compressed = fetch(release['url'], MAX_COMPRESSED)
            return self._install(release, compressed)

    def install_local(self, archive, sha256, version):
        archive = Path(archive)
        if (not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version or '')
                or not re.fullmatch(r'[0-9a-f]{64}', sha256 or '')
                or archive.is_symlink() or not archive.is_file()):
            raise ValueError('INVALID_OFFLINE_ASSET')
        with operation_lock(self.state):
            if archive.stat().st_size > MAX_COMPRESSED:
                raise ValueError('RELEASE_TOO_LARGE')
            with archive.open('rb') as stream:
                compressed = stream.read(MAX_COMPRESSED + 1)
            if len(compressed) > MAX_COMPRESSED:
                raise ValueError('RELEASE_TOO_LARGE')
            if hashlib.sha256(compressed).hexdigest() != sha256:
                raise ValueError('MIHOMO_CHECKSUM_MISMATCH')
            release = {'version': version, 'sha256': sha256, 'size': len(compressed)}
            return self._install(release, compressed)

    def _install(self, release, compressed):
        if len(compressed) != release['size'] or hashlib.sha256(compressed).hexdigest() != release['sha256']:
            raise ValueError('MIHOMO_CHECKSUM_MISMATCH')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='mihomo-', dir=self.state) as temporary:
            compressed_path = Path(temporary) / 'asset.gz'
            compressed_path.write_bytes(compressed)
            with gzip.open(compressed_path, 'rb') as stream:
                data = stream.read(MAX_BINARY + 1)
            if len(data) > MAX_BINARY:
                raise ValueError('MIHOMO_BINARY_TOO_LARGE')
            validate_elf(data)
            staged = Path(temporary) / 'clash'
            staged.write_bytes(data)
            staged.chmod(0o755)
            if version_of(staged) != release['version']:
                raise ValueError('MIHOMO_VERSION_MISMATCH')
            # Offline same-version uploads can switch CPU variants. Exact binary
            # equality is the only no-op; no backup or restart for identical data.
            if (self.binary.is_file() and not release.get('asset') and self.binary.stat().st_size == len(data)
                    and self.binary.read_bytes() == data):
                return {'status': 'unchanged', 'version': release['version'], 'reason': 'identical_binary'}
            self.validate_config(staged)
            old_version = version_of(self.binary)
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            replacement = self.binary.with_name('clash.pending')
            shutil.copyfile(staged, replacement)
            replacement.chmod(0o755)
            if self.binary.exists():
                backup = self.previous.with_name('clash.previous.pending')
                shutil.copy2(self.binary, backup)
                backup.replace(self.previous)
            replacement.replace(self.binary)
            try:
                self.restart_and_check(release['version'])
            except Exception:
                if self.previous.exists():
                    rollback = self.binary.with_name('clash.rollback')
                    shutil.copy2(self.previous, rollback)
                    rollback.replace(self.binary)
                    self.restart_and_check(old_version)
                else:
                    self.binary.unlink(missing_ok=True)
                raise ValueError('MIHOMO_UPDATE_ROLLED_BACK') from None
        status = {**self.status(), 'installed_version': release['version'], 'updated_at': time.time(),
                  'update_available': False, 'variant_change_available': False, 'last_update_result': 'success',
                  'installed_asset': release.get('asset'), 'installed_asset_sha256': release['sha256']}
        atomic_json(self.state / 'status.json', status)
        return {'status': 'updated', 'version': release['version']}

    def rollback(self):
        with operation_lock(self.state):
            previous = version_of(self.previous)
            if not previous:
                raise ValueError('MIHOMO_ROLLBACK_UNAVAILABLE')
            self.validate_config(self.previous)
            with tempfile.TemporaryDirectory(dir=self.state) as temporary:
                current = Path(temporary) / 'current'
                shutil.copy2(self.binary, current)
                candidate = self.binary.with_name('clash.rollback')
                shutil.copy2(self.previous, candidate)
                candidate.replace(self.binary)
                try:
                    self.restart_and_check(previous)
                except Exception:
                    shutil.copy2(current, candidate)
                    candidate.replace(self.binary)
                    self.restart_and_check(version_of(self.binary))
                    raise ValueError('MIHOMO_ROLLBACK_FAILED') from None
                shutil.copy2(current, self.previous)
            atomic_json(self.state / 'status.json', {**self.status(), 'last_update_result': 'rolled_back', 'updated_at': time.time(),
                        'installed_asset': None, 'installed_asset_sha256': None})
            return {'status': 'rolled_back', 'version': previous}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'update', 'rollback', 'install', 'install-local'))
    parser.add_argument('--archive', type=Path, help='离线官方 .gz 文件')
    parser.add_argument('--sha256', help='从可信渠道取得的官方压缩文件 SHA256')
    parser.add_argument('--version', help='离线官方版本，例如 v1.19.33')
    args = parser.parse_args()
    if args.action == 'install-local':
        if not all((args.archive, args.sha256, args.version)):
            parser.error('离线安装必须同时提供 --archive、--sha256 和 --version。')
    elif any((args.archive, args.sha256, args.version)):
        parser.error('离线参数仅用于 install-local。')
    updater = Updater()
    try:
        if args.action == 'check':
            updater.check()
            result = updater.status()
        elif args.action == 'rollback':
            result = updater.rollback()
        elif args.action == 'install-local':
            result = updater.install_local(args.archive, args.sha256, args.version)
        else:
            result = updater.update()
        print(json.dumps(result))
    except Exception as error:
        details = error_details(error)
        raise SystemExit(f"{details['message']}（{details['error']}）") from None


if __name__ == '__main__':
    main()
