#!/usr/bin/env python3
"""LovestGateWay Debian 安装与升级：校验、首次配置、服务启动计划及失败恢复。"""
import argparse
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile


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



def verify_tree(package):
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
    return manifest


def check_platform(layout):
    if not layout.live: return
    layout.require_root()
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'debian' or int(release.get('VERSION_ID', '0').strip('"').split('.')[0]) < 12:
        from manage_common import ManagementError
        raise ManagementError('DEBIAN_REQUIRED')


def missing_dependencies():
    missing = [name for name in ('nft', 'ip', 'python3', 'systemctl', 'pgrep', 'pidof', 'nsenter', 'openvpn', 'openssl') if shutil.which(name) is None]
    if importlib.util.find_spec('yaml') is None: missing.append('python3-yaml')
    return missing


def interfaces():
    try:
        result = subprocess.run(['ip', '-j', '-4', 'addr', 'show'], capture_output=True, text=True, timeout=5, check=True)
        return [(item['ifname'], ', '.join(a['local'] for a in item.get('addr_info', []) if a.get('family') == 'inet'))
                for item in json.loads(result.stdout) if item.get('ifname') != 'lo']
    except (OSError, ValueError, subprocess.SubprocessError):
        directory = Path('/sys/class/net')
        return [(path.name, '') for path in sorted(directory.iterdir()) if path.name != 'lo'] if directory.exists() else []


def choose_interface():
    items = interfaces()
    if items:
        print('本机网卡：')
        for i, (name, address) in enumerate(items, 1): print(f'  {i}. {name}  {address or "未检测到 IPv4 地址"}')
    while True:
        value = input('请选择网卡编号或输入网卡名（例如 ens18）: ').strip()
        if value.isascii() and value.isdigit() and 1 <= int(value) <= len(items): return items[int(value)-1][0]
        if value and not value.isdigit(): return value
        print('请选择有效编号或输入实际网卡名。')



def choose_password():
    from manage_common import ManagementError
    from manage_model import validate
    print('首次管理密码：输入至少 8 个字符；直接回车则生成 8 位随机密码。')
    while True:
        password = getpass.getpass('设置管理密码（输入不显示）: ')
        if not password: return None
        try:
            validate('ini', 'clash_web_password', password)
            if len(password) < 8: raise ManagementError('DASHBOARD_PASSWORD_TOO_SHORT')
        except ManagementError:
            print('密码需至少 8 个字符，且不能包含控制字符，请重新输入。')
            continue
        if password != getpass.getpass('再次输入管理密码: '):
            print('两次输入的密码不一致，请重新设置。')
            continue
        return password


def completion(layout, result):
    from manage_model import load_settings
    settings = load_settings(layout)
    labels = {'installed': '全新安装完成', 'upgraded': '升级完成', 'rolled_back': '已恢复安装前状态', 'nothing_to_recover': '没有需要恢复的事务'}
    print(labels.get(result['status'], '操作完成') + '。')
    if 'version' not in result: return
    print('版本: ' + result['version'] + '；备份编号: ' + result['backup'])
    if not layout.live: print('当前是隔离暂存安装，未操作宿主服务。')
    else:
        from manage_common import STARTUP_LABELS
        print('服务计划: ' + STARTUP_LABELS[result['startup']])
        if not result['services'].get('paopaogw', {}).get('active'):
            print('网关尚未启动；需要时运行 sudo systemctl start paopaogw paopaogw-api paopaogw-subscription paopaogw-settings。')
        host, port = settings['service']['PPGW_WEB_LISTEN'].rsplit(':', 1)
        if host in {'0.0.0.0', '[::]'}:
            found = dict(interfaces()).get(settings['service']['PPGW_INTERFACE'], '').split(', ')[0]
            host = found or '<网关IP>'
        print(f'Web 管理地址: http://{host}:{port}/ui/（服务运行且网络允许访问后可用）')
    print('管理菜单: sudo ppgw-admin')
    print('管理密码保存在 /etc/paopaogw/files/ppgw.ini 的 clash_web_password（仅管理员可读）；升级沿用原密码。')
    print('修改密码：Web 网关设置 → 访问与统计 → 修改管理密码。')
    print('首次使用请在 Web 的“维护与 Lovest HomeNet”安装 Mihomo，再到“订阅与策略”配置来源。')
    print('全新安装采用直连模式；显式代理、UDP、统计、IPv6、历史采集和自动更新默认关闭，可在 Web 按需开启。')


def main(argv=None):
    parser = ChineseParser(description=__doc__)
    parser.add_argument('--root', default='/', help='隔离暂存根目录；默认使用正式系统 /')
    parser.add_argument('--interface', help='全新安装的服务网卡，例如 ens18；升级沿用原配置')
    parser.add_argument('--web-listen', help='全新安装 Web 监听，默认 0.0.0.0:80')
    parser.add_argument('--dns-listen', help='全新安装 DNS 监听，默认 0.0.0.0:53；可指定网关 LAN 地址')
    parser.add_argument('--startup', choices=('auto', 'manual', 'disabled', 'preserve'),
                        help='auto 开机自启并立即启动；manual 仅本次启动；disabled 只安装；preserve 升级保留原状态')
    parser.add_argument('--install-deps', action='store_true', default=None, help='确认使用 apt 安装系统依赖')
    parser.add_argument('--yes', action='store_true', help='按参数及默认值执行，跳过交互确认')
    parser.add_argument('--recover', action='store_true', help='恢复上次中断事务，不执行安装')
    args = parser.parse_args(argv)
    package = Path(__file__).resolve().parent
    try: manifest = verify_tree(package)
    except (OSError, ValueError, TypeError): parser.exit(1, '安装包清单或摘要校验失败；未执行安装程序。\n')
    sys.path.insert(0, str(package / 'lib'))
    from manage_common import Layout, ManagementError
    from manage_deploy import Deployer
    from manage_model import load_settings, validate
    from manage_common import error_text, choose_startup, STARTUP_LABELS
    try:
        layout = Layout(args.root)
        check_platform(layout)
        interactive = sys.stdin.isatty() and not args.yes
        if not interactive and not args.yes: parser.exit(1, '非交互操作需要显式提供 --yes。\n')
        if args.recover:
            if interactive and input('输入 yes 确认恢复上次中断事务: ').strip().lower() not in {'yes', '是'}: return 1
            from manage_config import ConfigManager
            completion(layout, ConfigManager(layout).recover())
            return 0
        fresh = not any(layout.path('etc/paopaogw/' + name).exists() for name in ('instance-id', 'files/ppgw.ini'))
        if not fresh and any(value is not None for value in (args.interface, args.web_listen, args.dns_listen)):
            raise ManagementError('FRESH_OPTIONS_ON_UPGRADE')
        initial_password = None
        if fresh:
            if not args.interface and interactive: args.interface = choose_interface()
            if not args.interface: raise ManagementError('FIRST_INSTALL_INTERFACE_REQUIRED')
            validate('service', 'PPGW_INTERFACE', args.interface)
            if interactive:
                if args.web_listen is None: args.web_listen = input('Web 监听 [0.0.0.0:80]: ').strip() or '0.0.0.0:80'
                if args.dns_listen is None: args.dns_listen = input('DNS 监听 [0.0.0.0:53]（共用宿主可指定 LAN 地址）: ').strip() or '0.0.0.0:53'
            for key, value in [('PPGW_WEB_LISTEN', args.web_listen), ('PPGW_DNS_LISTEN', args.dns_listen)]:
                if value is not None: validate('service', key, value)
        if fresh and interactive: initial_password = choose_password()
        if args.startup is None: args.startup = choose_startup(fresh) if interactive else ('auto' if fresh else 'preserve')
        if fresh and args.startup == 'preserve': raise ManagementError('FRESH_STARTUP_REQUIRED')
        missing = missing_dependencies() if layout.live else []
        if interactive and missing and args.install_deps is None:
            print('缺少依赖: ' + '、'.join(missing))
            args.install_deps = input('使用 apt 安装运行依赖？[是/否，默认是]: ').strip().lower() in {'', 'yes', 'y', '是'}
        settings = load_settings(layout)
        print('\n安装计划：' + ('全新安装' if fresh else '升级；保留已有配置、密码及数据'))
        print('服务网卡: ' + (args.interface if fresh else settings['service']['PPGW_INTERFACE']))
        print('Web 监听: ' + (args.web_listen or ('0.0.0.0:80' if fresh else settings['service']['PPGW_WEB_LISTEN'])))
        print('DNS 监听: ' + (args.dns_listen or ('0.0.0.0:53' if fresh else settings['service']['PPGW_DNS_LISTEN'])))
        print('服务启动: ' + STARTUP_LABELS[args.startup])
        print('管理密码: ' + ('沿用原密码' if not fresh else '手动设置（不显示内容）' if initial_password is not None else '自动生成随机密码'))
        print('系统依赖: ' + ('通过 apt 更新索引并安装' if args.install_deps else '使用已安装依赖'))
        if fresh: print('初始功能: 直连；代理、UDP、统计、IPv6、历史和自动更新关闭；HNM API 仅监听本机。')
        if interactive and input('以上为待执行内容，输入 yes 或“是”确认安装: ').strip().lower() not in {'yes', '是'}:
            print('已取消安装。'); return 1
        if args.install_deps:
            if not layout.live: parser.exit(1, '--install-deps 只可用于正式 Debian 系统。\n')
            print('正在安装系统依赖…', flush=True)
            subprocess.run(['apt-get', 'update'], check=True)
            subprocess.run(['apt-get', 'install', '-y', 'python3', 'python3-yaml', 'nftables', 'iproute2',
                            'procps', 'util-linux', 'openvpn', 'openssl', 'ca-certificates', 'curl'], check=True)
        remaining = missing_dependencies() if layout.live else []
        if remaining: raise ManagementError('DEPENDENCY_REQUIRED:' + ','.join(remaining))
        print('正在生成并预检安装候选，随后备份、安装并验收服务…', flush=True)
        with tempfile.TemporaryDirectory(prefix='ppgw-bootstrap-') as temporary:
            archive = Path(temporary) / 'release.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in ['manifest.json', *manifest]: tar.add(package / name, arcname='release/' + name, recursive=False)
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            result = Deployer(layout).deploy(archive, sha, args.interface, args.startup, args.web_listen, args.dns_listen, initial_password=initial_password)
        completion(layout, result)
        return 0
    except ManagementError as error:
        print(error_text(error), file=sys.stderr); return 1
    except (OSError, ValueError, subprocess.SubprocessError):
        print('安装未完成，请检查系统依赖、磁盘空间、服务日志及 sudo ppgw-admin status。', file=sys.stderr); return 1
    except (KeyboardInterrupt, EOFError):
        print('操作已中断；如已开始安装，请检查 sudo ppgw-admin status，必要时执行 recover。', file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
