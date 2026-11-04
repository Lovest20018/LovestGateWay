"""Small filesystem and locking primitives shared by all administrator entry points."""
from contextlib import contextmanager
import argparse
import sys
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


# 中文命令行输出；管理接口继续保留稳定错误码。
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

MESSAGES = {'ROOT_REQUIRED': '请使用 sudo 或 root 权限运行。',
 'FIRST_INSTALL_INTERFACE_REQUIRED': '全新安装必须指定服务网卡，可使用 --interface 或按交互提示选择。',
 'INTERFACE_REQUIRED': '请设置网关服务网卡。',
 'INTERFACE_IPV4_REQUIRED': '所选服务网卡没有可用 IPv4 地址，请先在 Debian 中配置网络。',
 'DASHBOARD_PASSWORD_TOO_SHORT': '管理密码至少需要 8 个字符。',
 'EXPLICIT_PROXY_AUTH_REQUIRED': '启用显式代理前必须设置用户名和密码。',
 'MIHOMO_REQUIRED': '请先在 Web 的“维护与 Lovest HomeNet”中安装 Mihomo。',
 'SUBSCRIPTION_REQUIRED': '请先填写订阅地址或导入 PPsub。',
 'OPENVPN_TUN_REQUIRED': '请检查 OpenVPN 依赖及 /dev/net/tun 设备。',
 'INVALID_UDP_BUDGET': 'UDP 保留额度必须小于会话上限。',
 'SOCKS5_IP_REQUIRED': '请填写 SOCKS5 上游地址。',
 'UNSAFE_OPENVPN_CONFIG': 'OpenVPN 文件包含不支持的配置，请使用受限内联证书格式。',
 'ASSET_REQUIRED': '未找到配置资源，请先导入文件。',
 'INVALID_STARTUP': '启动方式无效，请选择 auto、manual、disabled 或 preserve。',
 'FRESH_STARTUP_REQUIRED': '全新安装不能沿用服务状态，请选择 auto、manual 或 disabled。',
 'FRESH_OPTIONS_ON_UPGRADE': '升级保留已有网卡和监听配置，请在 Web 设置中修改这些参数。',
 'DEBIAN_REQUIRED': '安装仅支持 Debian 12 或更新版本。',
 'DEPENDENCY_REQUIRED': '缺少运行依赖，请使用 --install-deps 或先手动安装依赖。',
 'LISTENER_UNAVAILABLE': '监听地址无法使用，请检查端口占用和本机地址，并修改 Web/DNS 监听。',
 'LISTENER_COLLISION': 'Web、DNS 或内部管理接口的监听地址存在冲突，请修改监听设置。',
 'NETWORK_RESOURCE_BUSY': '网关保留的 nft 表或策略路由已被占用，请先处理冲突。',
 'SERVICE_STATE_MISMATCH': '服务的运行或开机自启状态与所选计划不一致，请查看 systemctl 状态。',
 'SYSTEM_OPERATION_FAILED': '系统命令执行失败，请检查服务日志和运行环境。',
 'INSTALLER_FAILED': '底层安装失败，原状态已进入恢复流程，请检查安装依赖与日志。',
 'INSTALLER_PREFLIGHT_FAILED': '安装预检失败，尚未停止服务或写入正式配置。',
 'INSTALLED_FILE_MISMATCH': '安装后文件摘要不一致，正在恢复原版本。',
 'DEPLOY_FAILED_ROLLED_BACK': '安装或启动验收失败，已恢复安装前状态。',
 'READINESS_TIMEOUT': '网关启动验收超时，请检查网卡、监听端口及服务日志。',
 'API_READINESS_TIMEOUT': 'Lovest HomeNet API 启动验收超时，请检查监听及服务日志。',
 'RECOVERY_REQUIRED': '上次操作尚未完成，请先运行 sudo ppgw-admin recover --yes 恢复。',
 'EXTERNAL_CONFIG_CONFLICT': '检测到外部配置修改，已保留这些修改，请检查 status 后处理恢复冲突。',
 'MANAGEMENT_BUSY': '另一项管理操作正在进行，请稍后重试。',
 'MANAGEMENT_LOCK_REQUIRED': '此操作需要管理锁，请从安装或管理入口执行。',
 'CONFIRMATION_REQUIRED': '请先检查操作内容，再使用 --yes 确认。',
 'INTERACTIVE_TERMINAL_REQUIRED': '此操作需要交互终端，批处理请使用 --help 中的子命令。',
 'CONFIG_REVISION_CONFLICT': '配置已被其他操作修改，请重新读取后提交。',
 'SOURCE_CONFLICT': '新来源与已有本地策略或 PPsub 冲突，请预览后明确确认清除。',
 'STALE_POLICY_SNAPSHOT': '编辑快照已失效，请重新读取当前配置。',
 'APPLY_FAILED_ROLLED_BACK': '配置加载验收失败，已恢复原配置。',
 'BACKUP_FAILED_SERVICES_RESTORED': '备份未完成，已恢复原服务状态。',
 'RESTORE_FAILED_PREVIOUS_STATE_RESTORED': '备份恢复失败，已恢复之前状态。',
 'INPUT_FILE_MUST_BE_PRIVATE': '输入文件权限必须为仅管理员可读，请执行 chmod 600。',
 'INVALID_JSON_OBJECT': '输入必须是有效的 JSON 对象。',
 'EXPECTED_PATCH_ENVELOPE': '配置补丁必须包含 patch 和当前 base_revision。',
 'UNKNOWN_NAMESPACE': '未知配置分组，请使用 config schema 查看可设置字段。',
 'UNKNOWN_SETTING': '未知配置字段，请使用 config schema 查看可设置字段。',
 'ASSET_EXISTS': '同名资源已存在，确认覆盖时请使用 --overwrite。',
 'ASSET_EXTENSION_MISMATCH': '文件扩展名与资源类型不一致。',
 'INVALID_ASSET_CONTENT': '资源文件内容无效或包含不支持的指令。',
 'INVALID_INPUT_FILE': '输入文件不存在、类型错误或超出大小限制。',
 'INPUT_TOO_LARGE': '输入内容超出允许大小。',
 'INVALID_RELEASE_INPUT': '发布包路径或 SHA256 格式无效。',
 'RELEASE_SHA256_MISMATCH': '发布包 SHA256 与提供的可信摘要不一致。',
 'RELEASE_MANIFEST_MISMATCH': '包内文件与清单不一致。',
 'RELEASE_ARCHITECTURE_MISMATCH': '发布包架构不匹配，当前部署支持 amd64。',
 'RELEASE_TOO_LARGE': '发布包超出允许大小。',
 'UNSAFE_RELEASE_ENTRY': '发布包含不安全路径、链接或特殊文件。',
 'UNEXPECTED_RELEASE_FILE': '发布包含额外、重复或不支持的文件。',
 'MULTIPLE_RELEASE_ROOTS': '发布包必须只有一个顶层目录。',
 'TRUNCATED_RELEASE': '发布包不完整，请重新取得并校验。',
 'INSTALLER_MISSING': '发布包缺少安装程序。',
 'INVALID_RELEASE': '发布包格式或内容无效。',
 'BACKUP_CHECKSUM_MISMATCH': '备份摘要校验失败。',
 'INVALID_BACKUP_ID': '备份编号格式无效。',
 'INVALID_BACKUP': '备份文件不完整或格式无效。',
 'CORE_NOT_RUNNING': '网关核心未运行，请先检查服务状态。',
 'TIMEZONE_CONFLICT': '调度时区配置不一致。',
 'SYMLINK_REFUSED': '目标路径含符号链接，已拒绝写入。',
 'UNSAFE_ROOT': '安装根目录不能是符号链接。',
 'UNSAFE_PATH': '路径不安全，已拒绝访问。',
 'OUTSIDE_MANAGED_ROOT': '路径超出本项目管理范围。',
 'UNMANAGED_BACKUP_PATH': '备份包含本项目管理范围外的路径。',
 'INVALID_TRANSACTION_JOURNAL': '事务记录损坏，请检查备份和 status 后处理。',
 'OPERATION_CANCELLED': '已取消操作。'}


def error_text(error):
    code = str(error).split(':', 1)[0]
    text = MESSAGES.get(code)
    if text is None:
        text = '配置或操作无效，请检查参数、资源和 status 输出。' if code.startswith(('INVALID_', 'UNSUPPORTED_', 'UNKNOWN_')) else '操作未完成，请检查 status，必要时执行 recover。'
    detail = str(error).partition(':')[2]
    if code == 'DEPENDENCY_REQUIRED' and detail:
        known = {'nft', 'ip', 'python3', 'systemctl', 'pgrep', 'pidof', 'nsenter', 'openvpn', 'openssl', 'python3-yaml'}
        names = [name for name in detail.split(',') if name in known]
        if names: text += ' 缺少: ' + '、'.join(names)
    if code == 'LISTENER_UNAVAILABLE' and detail:
        import re
        if re.fullmatch(r'[0-9a-fA-F:.\[\]]{3,80}', detail): text += ' 地址: ' + detail
    # Retain stable codes for diagnostics without echoing submitted values or credentials.
    return text + ' [' + code + ']'


STARTUP_LABELS = {'auto': '立即启动，并设为开机自启', 'manual': '立即启动，本次运行；不设开机自启',
                  'disabled': '只安装，暂不启动且不开机自启', 'preserve': '保留原有运行及开机自启状态'}


def choose_startup(fresh):
    options = ['auto', 'manual', 'disabled'] if fresh else ['preserve', 'auto', 'manual', 'disabled']
    print('服务启动方式：')
    for i, key in enumerate(options, 1): print(f'  {i}. {STARTUP_LABELS[key]}' + ('（默认）' if i == 1 else ''))
    while True:
        answer = input('请选择启动方式 [1]: ').strip() or '1'
        if answer in options: return answer
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(options): return options[int(answer)-1]
        print('请选择列表中的编号。')
