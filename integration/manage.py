#!/usr/bin/env python3
"""LovestGateWay 中文脚本管理入口。凭据只从私有 JSON/stdin/隐藏输入读取。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import stat
import sys
from manage_common import Layout, ManagementError, operation_lock, require_idle, write_json
from manage_model import SCHEMA, SECRET_KEYS, load_settings, public_settings, settings_revision
from manage_runtime import System
from manage_config import ConfigManager, JOURNAL
from manage_backup import BackupStore
from manage_deploy import Deployer
from manage_common import ChineseParser, error_text, choose_startup, STARTUP_LABELS
from manage_assets import import_asset


def emit(value): print(json.dumps(value, ensure_ascii=False, indent=2))


def payload_file(name):
    if name == '-': raw = sys.stdin.read(2*1024*1024 + 1)
    else:
        path = Path(name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2*1024*1024: raise ManagementError('INVALID_INPUT_FILE')
        raw = path.read_text(encoding='utf-8')
        if os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077: raise ManagementError('INPUT_FILE_MUST_BE_PRIVATE:chmod 600')
    if len(raw.encode()) > 2*1024*1024: raise ManagementError('INPUT_TOO_LARGE')
    try:
        value = json.loads(raw)
        if not isinstance(value, dict): raise ValueError()
        return value
    except ValueError: raise ManagementError('INVALID_JSON_OBJECT') from None


def confirm(args):
    if not args.yes: raise ManagementError('CONFIRMATION_REQUIRED:review config check output, then use --yes')


def restore_backup(layout, system, identifier):
    with operation_lock(layout):
        require_idle(layout)
        store = BackupStore(layout)
        target = store.index(identifier)  # validate before stopping
        before = system.status()
        journal = {'kind': 'restore', 'phase': 'stopping', 'services': before, 'backup': None}
        write_json(layout, JOURNAL, journal)
        try:
            system.stop_all()
            # Include paths absent in the target snapshot in a rescue snapshot.
            rescue = store.create(set(store.paths()) | set(store.index(identifier)['files']), {'services': before, 'kind': 'restore-rescue'})
            original = store.index(rescue)['files']
            expected = {path: [None if record is None else record['sha256']] for path,record in original.items() if not path.startswith('var/lib/')}
            for path, record in target['files'].items():
                if path in expected: expected[path].append(None if record is None else record['sha256'])
            if target.get('full'):
                for path in set(expected) - set(target['files']): expected[path].append(None)
            journal.update(phase='restoring', backup=rescue, expected=expected)
            write_json(layout, JOURNAL, journal)
            if target.get('full'):
                for path in store.paths():
                    if path not in target['files']:
                        layout.path(path).unlink(missing_ok=True)
            store.restore(identifier)
            desired = target.get('metadata', {}).get('services', before) if target.get('full') else before
            system.restore_services(desired)
            if desired.get('paopaogw', {}).get('active'): system.wait(load_settings(layout))
            if desired.get('paopaogw-api', {}).get('active') and hasattr(system, 'wait_api'): system.wait_api(load_settings(layout))
            journal['phase'] = 'complete'; write_json(layout, JOURNAL, journal)
            return {'status': 'restored', 'backup': identifier, 'rescue_backup': rescue}
        except Exception:
            from manage_deploy import Deployer
            Deployer(layout, system).recover()
            raise ManagementError('RESTORE_FAILED_PREVIOUS_STATE_RESTORED') from None


def create_backup(layout, system):
    with operation_lock(layout):
        require_idle(layout)
        states = system.status()
        journal = {'kind': 'backup', 'phase': 'stopping', 'services': states, 'backup': None}
        write_json(layout, JOURNAL, journal)
        try:
            system.stop_all()
            identifier = BackupStore(layout).create(metadata={'services': states, 'kind': 'manual'})
            # No persistent files are modified; recovery only needs to restore service states.
            journal['created_backup'] = identifier
            write_json(layout, JOURNAL, journal)
            system.restore_services(states)
            journal['phase'] = 'complete'; write_json(layout, JOURNAL, journal)
            return {'backup': identifier}
        except Exception:
            from manage_deploy import Deployer
            Deployer(layout, system).recover()
            raise ManagementError('BACKUP_FAILED_SERVICES_RESTORED') from None


def menu(layout, system):
    if not sys.stdin.isatty(): raise ManagementError('INTERACTIVE_TERMINAL_REQUIRED:use --help for batch commands')
    manager = ConfigManager(layout, system)
    while True:
        print('\nLovestGateWay 网关管理\n1 状态诊断  2 查看脱敏配置  3 修改配置\n4 导入资源  5 创建备份  6 列出/恢复备份  7 安装/升级本地包  8 恢复中断事务\n9 重启并验证持久配置/资源  0 退出')
        choice = input('选择: ').strip()
        if choice == '0': return 0
        try:
            if choice == '1': emit(status(layout, system))
            elif choice == '2': emit(manager.show())
            elif choice == '3':
                print('配置分组: ini 网关/模式/DNS/认证；service 网卡/IPv6/监听；api Lovest HomeNet；maintenance 历史/自动更新')
                namespace = input('分组: ').strip()
                if namespace not in SCHEMA: raise ManagementError('UNKNOWN_NAMESPACE')
                print('可设置字段: ' + ', '.join(SCHEMA[namespace]))
                key = input('字段: ').strip()
                if key not in SCHEMA[namespace]: raise ManagementError('UNKNOWN_SETTING')
                spec = SCHEMA[namespace][key]
                if key in SECRET_KEYS: value = getpass.getpass('新值（隐藏输入；空值清除可选字段）: ')
                else:
                    raw = input('新值（布尔 true/false，数值，字符串；null 清除可选字段）: ')
                    if spec[0] in {'bool', 'int', 'optional_int', 'probe_urls'} or raw == 'null': value = json.loads(raw)
                    else: value = raw
                revision = settings_revision(layout); patch = {namespace: {key: value}}
                reset = False
                try: preview = manager.apply(patch, revision, dry_run=True)
                except ManagementError as error:
                    if not str(error).startswith('SOURCE_CONFLICT:'): raise
                    print('更换来源与本地 PPsub/策略冲突；旧内容会保存在事务备份中。')
                    if input('输入 reset 确认移除旧 PPsub/策略并继续预览: ') != 'reset': continue
                    reset = True
                    preview = manager.apply(patch, revision, dry_run=True, reset_source=True)
                emit(preview)
                if input('输入 yes 确认应用: ') == 'yes': emit(manager.apply(patch, revision, reset_source=reset))
            elif choice == '4':
                kind = input('类型 yaml/ppsub/ovpn/cert: ').strip(); source = input('本地源文件: ').strip(); name = input('保存名称: ').strip()
                overwrite = input('允许覆盖同名文件? yes/其他: ') == 'yes'
                emit(import_asset(layout, source, kind, name, overwrite))
            elif choice == '5':
                emit(create_backup(layout, system))
            elif choice == '6':
                emit(BackupStore(layout).list()); identifier = input('恢复备份 ID（空值返回）: ').strip()
                if identifier and input('输入 yes 确认恢复并重启原活动服务: ') == 'yes': emit(restore_backup(layout, system, identifier))
            elif choice == '7':
                archive = input('本地 tar.gz: ').strip(); sha = input('可信 SHA256: ').strip()
                fresh = not any(layout.path('etc/paopaogw/' + name).exists() for name in ('instance-id', 'files/ppgw.ini'))
                interface = input('首次安装服务网卡: ').strip() if fresh else None
                web = (input('Web 监听 [0.0.0.0:80]: ').strip() or '0.0.0.0:80') if fresh else None
                dns = (input('DNS 监听 [0.0.0.0:53]: ').strip() or '0.0.0.0:53') if fresh else None
                startup = choose_startup(fresh)
                print('服务计划: ' + STARTUP_LABELS[startup])
                if input('输入 yes 确认安装/升级: ').strip().lower() in {'yes', '是'}:
                    emit(Deployer(layout, system).deploy(archive, sha, interface, startup, web, dns))
            elif choice == '8':
                emit(manager.journal())
                if input('输入 yes 确认回退中断事务: ') == 'yes': emit(manager.recover())
            elif choice == '9':
                revision = settings_revision(layout)
                emit(manager.apply({}, revision, dry_run=True, restart=True))
                if input('输入 yes 确认重启并验收: ') == 'yes': emit(manager.apply({}, revision, restart=True))
            else: print('请选择菜单中的数字。')
        except ManagementError as error: print('操作失败：' + error_text(error))
        except ValueError: print('操作失败：输入格式不正确，请检查字段类型或 JSON。')


def status(layout, system):
    value = {'installed': layout.path('usr/local/lib/paopaogw/build.json').exists(), 'services': system.status(),
             'configuration': ConfigManager(layout, system).show(), 'transaction': ConfigManager(layout, system).journal()}
    if layout.live:
        try: value['runtime'] = system.runtime()
        except Exception: value['runtime'] = {'status': 'unavailable'}
    return value


def main(argv=None):
    parser = ChineseParser(description=__doc__)
    parser.add_argument('--root', default='/', help='隔离暂存根目录；默认实时系统 /')
    sub = parser.add_subparsers(dest='command', title='管理命令', description='使用 命令 --help 查看详细用法')
    sub.add_parser('status', help='服务、持久配置与实际运行状态')
    config = sub.add_parser('config', help='查看、校验及应用配置').add_subparsers(dest='action', required=True, title='配置操作')
    for action, help_text in [('show', '查看脱敏配置'), ('schema', '查看字段类型和范围')]: config.add_parser(action, help=help_text)
    for action in ('check', 'apply'):
        p = config.add_parser(action, help='仅预览校验配置' if action == 'check' else '应用配置并验收'); p.add_argument('--file', required=True, help='私有 JSON 文件或 - stdin')
        p.add_argument('--reset-source', action='store_true', help='确认备份并清除旧来源策略'); p.add_argument('--yes', action='store_true', help='确认执行操作')
        p.add_argument('--restart', action='store_true', help='显式重启并验证已保存配置/资源，即使补丁无改动')
        if action == 'apply': p.add_argument('--dry-run', action='store_true', help='只预览，不写入')
    backups = sub.add_parser('backup', help='创建或列出备份').add_subparsers(dest='action', required=True, title='备份操作')
    backups.add_parser('list', help='列出备份'); backups.add_parser('create', help='停服务创建一致性备份后恢复服务')
    p = sub.add_parser('restore', help='恢复指定备份'); p.add_argument('id', help='备份编号'); p.add_argument('--yes', action='store_true', help='确认恢复')
    p = sub.add_parser('recover', help='恢复中断事务'); p.add_argument('--yes', action='store_true', help='确认恢复')
    p = sub.add_parser('deploy', help='校验并安装或升级本地发布包')
    p.add_argument('--archive', required=True, help='本地发布包 tar.gz 路径'); p.add_argument('--sha256', required=True, help='可信外部 SHA256 摘要')
    p.add_argument('--interface', help='全新安装的服务网卡；升级不填写')
    p.add_argument('--web-listen', help='全新安装 Web 监听，例如 0.0.0.0:80')
    p.add_argument('--dns-listen', help='全新安装 DNS 监听，例如 192.0.2.10:53')
    p.add_argument('--startup', choices=('auto', 'manual', 'disabled', 'preserve'), help='auto 开机自启；manual 仅本次启动；disabled 只安装；preserve 升级保留原状态')
    p.add_argument('--yes', action='store_true', help='确认执行安装或升级')
    assets = sub.add_parser('assets', help='导入配置资源').add_subparsers(dest='action', required=True, title='资源操作')
    p = assets.add_parser('import', help='导入本地文件'); p.add_argument('--type', choices=('yaml', 'ppsub', 'ovpn', 'cert'), required=True, help='资源类型')
    p.add_argument('--file', required=True, help='本地源文件'); p.add_argument('--name', required=True, help='保存文件名'); p.add_argument('--overwrite', action='store_true', help='备份后覆盖同名资源'); p.add_argument('--yes', action='store_true', help='确认导入')
    args = parser.parse_args(argv)
    try:
        layout = Layout(args.root); system = System(layout); manager = ConfigManager(layout, system)
        if not args.command: return menu(layout, system)
        if args.command == 'status': result = status(layout, system)
        elif args.command == 'config':
            if args.action == 'show': result = manager.show()
            elif args.action == 'schema': result = {ns: {key: {'type': spec[0], 'default': '********' if key in SECRET_KEYS and spec[1] else spec[1], 'constraints': spec[2:]} for key,spec in fields.items()} for ns,fields in SCHEMA.items()}
            else:
                payload = payload_file(args.file)
                if set(payload) - {'base_revision', 'patch'} or not isinstance(payload.get('patch'), dict): raise ManagementError('EXPECTED_PATCH_ENVELOPE')
                dry = args.action == 'check' or args.dry_run
                if not dry: confirm(args)
                result = manager.apply(payload['patch'], payload.get('base_revision'), dry_run=dry, reset_source=args.reset_source, restart=args.restart)
        elif args.command == 'backup':
            if args.action == 'list': result = BackupStore(layout).list()
            else: result = create_backup(layout, system)
        elif args.command == 'restore': confirm(args); result = restore_backup(layout, system, args.id)
        elif args.command == 'recover': confirm(args); result = manager.recover()
        elif args.command == 'deploy': confirm(args); result = Deployer(layout, system).deploy(args.archive, args.sha256, args.interface, args.startup, args.web_listen, args.dns_listen)
        elif args.command == 'assets': confirm(args); result = import_asset(layout, args.file, args.type, args.name, args.overwrite)
        emit(result); return 0
    except ManagementError as error:
        print(error_text(error), file=sys.stderr); return 1
    except (OSError, ValueError, KeyboardInterrupt, EOFError):
        print('操作未完成；使用 status 检查状态，必要时执行 recover。', file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
