#!/usr/bin/env python3
"""安装已校验的本地发布包。此低层入口只写文件，不启动服务；通常请使用 install.sh。"""
import argparse
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid


class ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._positionals.title = '位置参数'
        self._optionals.title = '选项'
        for action in self._actions:
            if action.dest == 'help': action.help = '显示帮助并退出'
    def format_help(self):
        return super().format_help().replace('usage:', '用法:')
    def format_usage(self):
        return super().format_usage().replace('usage:', '用法:')
    def error(self, message):
        for old, new in [('the following arguments are required:', '缺少必填参数:'),
                         ('unrecognized arguments:', '无法识别的参数:'), ('invalid choice:', '无效选项:'),
                         ('choose from', '可选值为'), ('expected one argument', '需要一个参数值'),
                         ('not allowed with argument', '不能与以下参数同时使用'), ('argument ', '参数 ')]:
            message = message.replace(old, new)
        if not any('\u4e00' <= c <= '\u9fff' for c in message): message = '参数不正确，请参照帮助检查命令。'
        self.print_usage(sys.stderr)
        self.exit(2, '参数错误: ' + message + '\n')


def write_new(path, value, mode):
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'w') as f:
            f.write(value)


def main():
    parser = ChineseParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='隔离暂存根目录；默认使用正式系统 /')
    parser.add_argument('--enable-test-features', action='store_true', help='显式重新应用私网认证测试配置（普通安装请勿使用）')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    target = args.root.resolve()
    live = target == Path('/')
    manifest = json.loads((package / 'manifest.json').read_text())
    for name, expected in manifest.items():
        item = (package / name).resolve()
        if not item.is_relative_to(package) or not item.is_file() or item.is_symlink():
            parser.exit(1, '安装包路径无效。\n')
        if hashlib.sha256(item.read_bytes()).hexdigest() != expected:
            parser.exit(1, '安装包文件摘要校验失败。\n')
    sys.path.insert(0, str(package / 'lib'))
    from manage_common import Layout, atomic_write
    layout = Layout(args.root)
    def install_file(source, destination):
        relative = destination.relative_to(target).as_posix()
        layout.path(relative)  # refuse existing target/parent symlinks before writing
        mode = source.stat().st_mode & 0o777
        atomic_write(layout, relative, source.read_bytes(), mode)
        destination.chmod(mode)
    if live:
        if os.geteuid() != 0:
            parser.exit(1, '请使用 sudo 或 root 权限运行。\n')
        if importlib.util.find_spec('yaml') is None:
            parser.exit(1, '请先安装 python3-yaml，以读取和检查订阅配置。\n')
        release = Path('/etc/os-release').read_text()
        if 'ID=debian' not in release:
            parser.exit(1, '此安装程序仅支持 Debian。\n')
        for executable in ('nft', 'ip', 'python3', 'systemctl', 'pgrep', 'pidof', 'nsenter', 'openvpn', 'openssl'):
            if shutil.which(executable) is None:
                parser.exit(1, f'缺少运行依赖: {executable}\n')
        # A running release is never overwritten beneath its processes.
        for service in ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-settings', 'paopaogw-observe',
                        'paopaogw-maintenance', 'paopaogw-core-action@check',
                        'paopaogw-core-action@update', 'paopaogw-core-action@rollback', 'paopaogw-core-action@install-local'):
            state = subprocess.run(['systemctl', 'is-active', '--quiet', service], check=False)
            if state.returncode == 0:
                parser.exit(1, '请先停止网关、API、订阅、设置、采集和维护服务；建议使用 install.sh 自动处理升级。\n')
        if subprocess.run(['getent', 'group', 'paopaogw-api'], capture_output=True).returncode:
            subprocess.run(['groupadd', '--system', 'paopaogw-api'], check=True)
        if subprocess.run(['id', '-u', 'paopaogw-api'], capture_output=True).returncode:
            subprocess.run(['useradd', '--system', '--gid', 'paopaogw-api', '--no-create-home',
                            '--shell', '/usr/sbin/nologin', 'paopaogw-api'], check=True)
    os.umask(0o027)
    config = target / 'etc/paopaogw'
    state = target / 'var/lib/paopaogw'
    code = target / 'usr/local/lib/paopaogw'
    existing_install = (config / 'files/ppgw.ini').exists() or (config / 'instance-id').exists()
    for directory in (config, config / 'files', state, state / 'history', state / 'logs', state / 'clash', state / 'clash/clash-dashboard', code):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    code.chmod(0o755)
    backups = target / 'var/backups/paopaogw'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backups.chmod(0o700)
    for source in (package / 'lib').rglob('*'):
        if source.is_file() and source.relative_to(package).as_posix() in manifest:
            dest = code / source.relative_to(package / 'lib')
            dest.parent.mkdir(parents=True, exist_ok=True)
            install_file(source, dest)
    launcher = target / 'usr/local/bin/ppgw-admin'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    install_file(package / 'lib/ppgw-admin', launcher)
    launcher.chmod(0o755)
    unit_dir = target / 'etc/systemd/system'
    unit_dir.mkdir(parents=True, exist_ok=True)
    for source in (package / 'units').iterdir():
        if source.suffix not in {'.service', '.timer'}:
            continue
        install_file(source, unit_dir / source.name)
    # Copy dashboard only; generated runtime data survives every upgrade.
    for source in (package / 'ui').rglob('*'):
        if source.is_file():
            dest = state / 'clash/clash-dashboard/ui' / source.relative_to(package / 'ui')
            install_file(source, dest)
    write_new(config / 'instance-id', str(uuid.uuid4()) + '\n', 0o640)
    write_new(config / 'read-token', secrets.token_hex(32) + '\n', 0o640)
    write_new(config / 'revision-key', secrets.token_hex(32) + '\n', 0o600)
    write_new(config / 'api.json', json.dumps({'listen': '127.0.0.1', 'port': 8788, 'probe_urls': []}, indent=2) + '\n', 0o640)
    write_new(config / 'service.env', 'PPGW_INTERFACE=\nPPGW_IPV6=no\n', 0o640)
    write_new(config / 'network', '# Debian: IPv6 disabled unless configured explicitly\n', 0o640)
    write_new(config / 'maintenance.json', json.dumps({'auto_update': False, 'history_enabled': False,
              'history_retention_days': 30, 'timezone': 'Asia/Shanghai', 'test_defaults': False}, indent=2) + '\n', 0o640)
    write_new(config / 'files/ppgw.ini', '#paopao-gateway\nmode=free\nfake_cidr=7.0.0.0/8\n'
              'dns_ip=223.5.5.5\ndns_port=53\nclash_web_password="' + secrets.token_urlsafe(6) + '"\n'
              'openport=no\nudp_enable=no\nnet_rec=no\n'
              'admin_cidr="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10"\n'
              'proxy_cidr="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10"\n', 0o600)
    profile_marker = config / 'test-profile.initialized'
    if args.enable_test_features:
        spec = importlib.util.spec_from_file_location('test_profile', code / 'test_profile.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.enable(target)
        write_new(profile_marker, 'private-authenticated-v1\n', 0o600)
    elif not profile_marker.exists():
        write_new(profile_marker, 'existing-install-preserved\n' if existing_install else 'minimal-web-setup\n', 0o600)
    write_new(state / 'hosts', '127.0.0.1 localhost\n::1 localhost\n', 0o640)
    if live:
        import grp
        group = grp.getgrnam('paopaogw-api').gr_gid
        for path in (config, state):
            os.chown(path, 0, group)
        os.chown(state / 'history', -1, group)
        (state / 'history').chmod(0o770)
        for name in ('maintenance.json',):
            os.chown(config / name, 0, group)
        for name in ('instance-id', 'read-token', 'api.json', 'service.env'):
            os.chown(config / name, 0, group)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('文件安装完成；已有实例身份、凭据、配置和运行数据已保留。')
    print('此低层入口未启动服务；请设置网卡与配置后手动启用服务，或使用 install.sh 完成部署。')


if __name__ == '__main__':
    main()
