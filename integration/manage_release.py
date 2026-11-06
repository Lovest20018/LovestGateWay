import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

RELEASES = 'https://github.com/Lovest20018/LovestGateWay/releases'
VERSION = re.compile(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9._-]{0,60})?')
SHA256 = re.compile(r'[0-9a-f]{64}')


def download(url, destination, limit):
    # The private SOCKS ingress carries the hostname, avoiding host FakeIP DNS.
    # It is also available when the public explicit proxy is disabled.
    for proxy in ('socks5h://127.0.0.1:1079', None):
        command = ['curl', '--fail', '--silent', '--show-error', '--location',
                   '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '5',
                   '--connect-timeout', '8', '--max-time', '180', '--max-filesize', str(limit)]
        command += ['--noproxy', '', '--proxy', proxy] if proxy else []
        with destination.open('wb') as output, subprocess.Popen(command + [url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            size = 0
            while block := process.stdout.read(65536):
                size += len(block)
                if size > limit:
                    process.kill()
                    raise ValueError('下载文件超过大小限制。')
                output.write(block)
            result = process.wait()
        if result == 0:
            return destination.read_bytes()
        # A reachable server's HTTP error is final, not a reason to bypass rate limits.
        if result == 22: break
    raise ValueError('无法从 GitHub 下载发行包。请检查网关/宿主网络，或使用离线安装包。')


def identity(manifest):
    version = manifest.get('version', '')
    commit = manifest.get('commit', '')
    if (manifest.get('schema') != 1 or not VERSION.fullmatch(version)
            or manifest.get('tag') != 'v' + version or manifest.get('architecture') != 'amd64'
            or not re.fullmatch(r'[0-9a-f]{40}', commit)):
        raise ValueError('发行清单中的版本或架构无效。')
    name = f'lovestgateway-{version}-debian-amd64.tar.gz'
    asset = manifest.get('assets', {}).get(name, {})
    if (not SHA256.fullmatch(asset.get('sha256', '')) or type(asset.get('bytes')) is not int
            or not 0 < asset['bytes'] <= 256 * 1024 * 1024):
        raise ValueError('发行清单缺少有效的安装包摘要。')
    return version, commit, name, asset


def unpack(raw, destination, manifest):
    version, commit, name, asset = identity(manifest)
    if len(raw) != asset['bytes'] or hashlib.sha256(raw).hexdigest() != asset['sha256']:
        raise ValueError('安装包 SHA256 或大小不匹配，已停止安装。')
    prefix = f'lovestgateway-{version}-debian-amd64'
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            parts = member.name.rstrip('/').split('/')
            if (not parts or parts[0] != prefix or any(p in {'', '.', '..'} for p in parts)
                    or '\\' in member.name or ':' in member.name
                    or not (member.isdir() or member.isfile()) or member.mode & 0o7000):
                raise ValueError('安装包包含不安全的路径或文件。')
            if member.isdir(): continue
            name = '/'.join(parts[1:])
            total += member.size
            if (not name or name in files or len(files) >= 4096
                    or member.size > 128 * 1024 * 1024 or total > 512 * 1024 * 1024):
                raise ValueError('安装包文件重复或超过大小限制。')
            files[name] = (archive.extractfile(member).read(), member.mode & 0o777)
    required = {'manifest.json', 'install.sh', 'install.py', 'deploy.py', 'lib/build.json', 'lib/bin/sniffbox'}
    if not required <= files.keys(): raise ValueError('安装包缺少必要文件。')
    checksums = json.loads(files['manifest.json'][0])
    actual = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in files.items() if name != 'manifest.json'}
    if checksums != actual: raise ValueError('安装包内部文件摘要不匹配。')
    build = json.loads(files['lib/build.json'][0])
    if build.get('version') != version or build.get('build_commit') != commit or build.get('architecture') != 'amd64':
        raise ValueError('安装包身份与发行清单不一致。')
    header = files['lib/bin/sniffbox'][0][:20]
    if header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
        raise ValueError('安装包核心不是 Linux amd64 程序。')
    # Write only after the entire archive and its identity have been verified.
    for name, (data, mode) in files.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def main():
    stage = Path(os.environ['LOVEST_INSTALL_STAGE'])
    manifest = json.loads(download(RELEASES + '/latest/download/release-manifest.json', stage / 'release.json', 1024 * 1024))
    version, _, name, asset = identity(manifest)
    print(f'正在下载 LovestGateWay v{version}…', flush=True)
    raw = download(RELEASES + '/download/' + manifest['tag'] + '/' + name, stage / name, asset['bytes'])
    unpack(raw, stage / 'package', manifest)
    print('发行包及内部文件校验通过，进入安装向导。', flush=True)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        print('在线安装准备失败；未执行安装程序。请检查下载、磁盘空间，或使用离线发行包。', file=sys.stderr)
        raise SystemExit(1)
