#!/usr/bin/env python3
"""Release bootstrap: verify extracted files before importing release modules."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/')
    parser.add_argument('--interface', help='首次安装必填，例如 eth0')
    parser.add_argument('--install-deps', action='store_true', help='显式使用 apt 安装 Debian 运行依赖')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--recover', action='store_true', help='使用已校验包中的独立管理程序恢复中断事务')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    try:
        manifest = json.loads((package / 'manifest.json').read_bytes())
        if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 4096: raise ValueError()
        actual = set()
        for path in package.rglob('*'):
            if path.is_symlink(): raise ValueError()
            if path.is_file(): actual.add(path.relative_to(package).as_posix())
        if actual != set(manifest) | {'manifest.json'}: raise ValueError()
        for name, expected in manifest.items():
            if (not isinstance(name, str) or '\\' in name or ':' in name or PurePosixPath(name).is_absolute()
                    or any(p in {'', '.', '..'} for p in name.split('/'))): raise ValueError()
            path = package / name
            if path.stat().st_size > 128*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest() != expected: raise ValueError()
    except (OSError, ValueError, TypeError):
        parser.exit(1, '安装包清单/校验失败；未执行安装程序。\n')
    if not args.yes:
        if not sys.stdin.isatty(): parser.exit(1, '非交互安装需显式 --yes。\n')
        if input('已校验本地包。输入 yes 确认安装/升级: ') != 'yes': return 1
    if not args.interface and not (Path(args.root) / 'etc/paopaogw/instance-id').exists() and sys.stdin.isatty():
        args.interface = input('网关服务网卡（例如 eth0）: ').strip()
    sys.path.insert(0, str(package / 'lib'))
    from manage_common import Layout, ManagementError
    from manage_deploy import Deployer
    layout = Layout(args.root)
    if args.recover:
        try:
            from manage_config import ConfigManager
            print(json.dumps(ConfigManager(layout).recover(), ensure_ascii=False, indent=2))
            return 0
        except ManagementError as error:
            print(str(error), file=sys.stderr); return 1
    if args.install_deps:
        if not layout.live: parser.exit(1, '--install-deps 仅用于实时系统。\n')
        layout.require_root()
        subprocess.run(['apt-get', 'update'], check=True)
        subprocess.run(['apt-get', 'install', '-y', 'python3', 'python3-yaml', 'nftables', 'iproute2',
                        'procps', 'util-linux', 'openvpn', 'openssl', 'ca-certificates', 'curl'], check=True)
    try:
        with tempfile.TemporaryDirectory(prefix='ppgw-bootstrap-') as temporary:
            archive = Path(temporary) / 'release.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in ['manifest.json', *manifest]: tar.add(package / name, arcname='release/' + name, recursive=False)
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            print(json.dumps(Deployer(layout).deploy(archive, sha, args.interface), ensure_ascii=False, indent=2))
        print('管理入口: sudo ppgw-admin；配置与随机初始密码保存在 /etc/paopaogw/files/ppgw.ini（仅管理员可读）。')
        return 0
    except ManagementError as error:
        print(str(error), file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
