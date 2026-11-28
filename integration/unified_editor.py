"""One transaction for the subscription editor and its update schedule."""
from copy import deepcopy
import json
import re
import threading
from manage_common import ManagementError, operation_lock, require_idle
from manage_config import ConfigManager
from manage_model import load_settings, settings_revision, validate
from manage_runtime import MihomoPolicyError, System
from clash_ppsub import (feature_options, DEFAULT_RULE_BEHAVIOR, BUILTIN_TARGETS, EditorIssue,
                         provider_view, check_http_rule_provider, rule_fields, rule_provider_spans, strings)
from manage_preview import native_summary, complete_preview, source_details


EDITOR_VALIDATION_CODES = {
    'INVALID_EDITOR_REQUEST', 'INVALID_EDITOR_CONFIG', 'INVALID_EDITOR_LIST', 'INVALID_EDITOR_GROUP',
    'INVALID_EDITOR_RULE', 'INVALID_EDITOR_ID', 'INVALID_GROUP_NAME', 'DUPLICATE_GROUP_NAME',
    'UNKNOWN_PROXY_PROVIDER', 'INVALID_TEST_INTERVAL', 'CLASH_YAML_EDITING_UNSUPPORTED',
    'INVALID_URL', 'INVALID_PRE_PROXY',
    'INVALID_RESTART_HOUR', 'INVALID_SS_CONFIG', 'RESERVED_PROVIDER_NAME',
}


def editor_error_details(error):
    code = str(error).split(':', 1)[0]
    if isinstance(error, EditorIssue):
        return {'error': code, 'issue': error.issue}
    if isinstance(error, MihomoPolicyError):
        return {'error': code, **({'detail': error.detail} if error.detail else {})}
    return {'error': code} if code in EDITOR_VALIDATION_CODES else None


def check_ppsub_rows(value, section, maximum):
    rows = value.get(section, [])
    if not isinstance(rows, list) or len(rows) > maximum:
        raise ManagementError('INVALID_PPSUB')
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise EditorIssue('INVALID_PPSUB_ROW', section, index, '')
    return rows


def check_ppsub_names(rows, section):
    names = set()
    for index, row in enumerate(rows):
        name = row.get('name')
        if (not isinstance(name, str) or not name or name != name.strip() or len(name) > 256
                or any(c in name for c in '\r\n') or (section == 'node-groups' and ',' in name)):
            raise EditorIssue('INVALID_PPSUB_NAME', section, index, 'name')
        if name in names:
            raise EditorIssue('DUPLICATE_PPSUB_NAME', section, index, 'name')
        names.add(name)
    return names


def check_ppsub_url(row, section, index, field='url'):
    try:
        validate('ini', 'test_node_url', row.get(field))
    except ManagementError:
        raise EditorIssue('INVALID_URL', section, index, field) from None


def validate_ppsub(value):
    if not isinstance(value, dict) or not value.get('exported_at'):
        raise ManagementError('INVALID_PPSUB')
    subscriptions = check_ppsub_rows(value, 'subs', 500)
    if not subscriptions:
        raise ManagementError('INVALID_PPSUB')
    check_ppsub_names(subscriptions, 'subs')
    from manage_subscription_schedule import validate_plan
    for index, row in enumerate(subscriptions):
        try:
            validate_plan(row)
        except ManagementError:
            raise EditorIssue('INVALID_SUBSCRIPTION_SCHEDULE', 'subs', index, 'update_mode') from None
        check_ppsub_url(row, 'subs', index)

    groups = check_ppsub_rows(value, 'node-groups', 5000)
    targets = check_ppsub_names(groups, 'node-groups') | BUILTIN_TARGETS
    for index, row in enumerate(groups):
        for field in ('subs', 'include', 'keywords', 'exclude_keywords'):
            try:
                if row.get(field) is not None:
                    strings(row[field])
            except ValueError:
                raise EditorIssue('INVALID_EDITOR_LIST', 'node-groups', index, field) from None
        if row.get('speedtest_url') not in (None, ''):
            check_ppsub_url(row, 'node-groups', index, 'speedtest_url')
        if row.get('failover'):
            if row.get('monitor_url') not in (None, ''):
                check_ppsub_url(row, 'node-groups', index, 'monitor_url')
            retries = row.get('retries')
            if retries is not None and (type(retries) is not int or not 0 <= retries <= 100):
                raise EditorIssue('INVALID_EDITOR_GROUP', 'node-groups', index, 'retries')

    rows = check_ppsub_rows(value, 'rules', 5000)
    providers = set()
    manual_rows = []
    for index, row in enumerate(rows):
        kind = '' if row.get('type') is None else row['type']
        if not isinstance(kind, str) or kind not in {'', 'manual', 'rules', 'url', 'rule-set'}:
            raise EditorIssue('INVALID_RULE_KIND', 'rules', index, 'type')
        priority = 0 if row.get('priority') is None else row['priority']
        if type(priority) is not int or not -(2**63) <= priority < 2**63:
            raise EditorIssue('INVALID_RULE_PRIORITY', 'rules', index, 'priority')
        if kind == 'rule-set':
            provider = provider_view(row)
            if row.get('format') in (None, ''):
                provider['format'] = 'auto'
            if row.get('behavior') in (None, ''):
                provider['behavior'] = DEFAULT_RULE_BEHAVIOR
            check_http_rule_provider(provider, index, native=True)
            name = provider['name']
            if (not isinstance(name, str) or not name or name != name.strip() or len(name) > 200
                    or any(c in name for c in '\r\n,')):
                raise EditorIssue('INVALID_RULE_PROVIDER_NAME', 'rules', index, 'name')
            if name in providers:
                raise EditorIssue('DUPLICATE_RULE_PROVIDER', 'rules', index, 'name')
            providers.add(name)
        elif kind == 'url' or (kind == '' and row.get('url') not in (None, '')):
            check_ppsub_url(row, 'rules', index)
        else:
            manual_rows.append((index, row.get('fixrule')))

    for index, field in manual_rows:
        try:
            lines = strings([] if field is None else field)
        except ValueError:
            raise EditorIssue('INVALID_EDITOR_LIST', 'rules', index, 'fixrule') from None
        for number, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            try:
                rule_kind, payload, (start, end) = rule_fields(line)
                if rule_kind == 'SUB-RULE':
                    raise ValueError('INVALID_RULE_LINE')
                spans = rule_provider_spans(line, rule_kind, payload)
            except ValueError:
                raise EditorIssue('INVALID_RULE_LINE', 'rules', index, 'fixrule', number) from None
            if line[start:end] not in targets:
                raise EditorIssue('UNKNOWN_RULE_TARGET', 'rules', index, 'fixrule', number)
            if any(line[start:end] not in providers for start, end in spans):
                raise EditorIssue('UNKNOWN_RULE_PROVIDER', 'rules', index, 'fixrule', number)

    feature_options(value)
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def candidate(admin, body, preview=None):
    import subscription_admin as source
    if body.get('edit_kind') == 'clash_patch':
        raise ManagementError('CLASH_YAML_EDITING_UNSUPPORTED')
    allowed = {'kind', 'edit_kind', 'value', 'base_revision', 'settings_revision', 'settings_patch',
               'operation_id', 'dry_run'}
    if (set(body) - allowed or body.get('edit_kind') != 'ppsub'
            or not isinstance(body.get('value'), str) or len(body['value'].encode()) > 1024*1024
            or type(body.get('dry_run', False)) is not bool
            or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(body.get('operation_id', '')))):
        raise ManagementError('INVALID_EDITOR_REQUEST')
    patch = body.get('settings_patch', {})
    if (not isinstance(patch, dict) or set(patch) - {'ini'} or not isinstance(patch.get('ini', {}), dict)
            or set(patch.get('ini', {})) - {'subtime', 'subcron'}):
        raise ManagementError('INVALID_EDITOR_REQUEST')
    layout = source.management_layout()
    if settings_revision(layout) != body.get('settings_revision') or source.revision(source.CONFIG.read_bytes()) != body.get('base_revision'):
        raise ManagementError('CONFIG_REVISION_CONFLICT')
    try:
        submitted = json.loads(body['value'])
    except json.JSONDecodeError:
        raise ManagementError('INVALID_EDITOR_CONFIG') from None
    if not source.native_ppsub_source(source.config_values()):
        raise ManagementError('SOURCE_CHANGE_REQUIRES_SWITCH')
    raw = validate_ppsub(submitted)
    previous = source.FILES / 'ppsub.json'
    old = json.loads(previous.read_bytes()) if previous.exists() else None

    def stable(value):
        value = deepcopy(value)
        if isinstance(value, dict):
            value.pop('exported_at', None)
        return value

    options = {'ppsub_update': raw if stable(old) != stable(submitted) else None}
    if preview is not None:
        runtime = layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json')
        comparison = old if old is not None else json.loads(runtime.read_bytes()) if runtime.is_file() else None
        preview['summary'] = native_summary(comparison, submitted)
        preview['source'] = source_details(layout, load_settings(layout)['ini'], native=True)
        preview['source']['materializes_native'] = old is None
    # Saving a remote PPsub materializes it as the local authoritative source.
    patch = deepcopy(patch)
    patch.setdefault('ini', {}).update(mode='suburl', suburl='ppsub@http://127.0.0.1/ppsub.json', fast_node='no')

    def verify():
        if not admin.core.request('/sniffbox/info').get('ppsub'):
            raise ManagementError('PPSUB_NOT_LOADED')
        if layout.live:
            actual = json.loads(layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json').read_bytes())
            if stable(actual) != stable(submitted):
                raise ManagementError('PPSUB_NOT_LOADED')

    system = System(layout, preserve_units={'paopaogw-subscription'})
    return layout, system, patch, options, verify


def handle(admin, body):
    if body.get('edit_kind') == 'ppsub' and not body.get('dry_run'):
        with admin.lock:
            if admin.operation and admin.operation.get('id') == body.get('operation_id'):
                return dict(admin.operation)
    preview = {} if body.get('dry_run') else None
    layout, system, patch, options, verify = candidate(admin, body, preview)
    if body.get('dry_run'):
        result = ConfigManager(layout, system).apply(patch, body['settings_revision'], dry_run=True, **options)
        result['editor_changed'] = options['ppsub_update'] is not None
        result['save_target'] = 'ppsub'
        return complete_preview(result, layout, system, preview['summary'], preview['source'])
    with admin.lock:
        if admin.operation and admin.operation.get('id') == body['operation_id']:
            return dict(admin.operation)
        if admin.operation and admin.operation.get('status') == 'applying':
            raise ManagementError('MANAGEMENT_BUSY')
        admin.operation = {'id': body['operation_id'], 'status': 'applying', 'message': '正在统一保存订阅、策略与更新计划…'}
        operation = admin.operation
    thread = threading.Timer(.5, worker, args=(admin, deepcopy(body), operation))
    thread.daemon = True
    thread.start()
    return dict(operation)


def worker(admin, body, operation):
    import subscription_admin as source
    try:
        layout = source.management_layout()
        with operation_lock(layout):
            require_idle(layout)
            layout, system, patch, options, verify = candidate(admin, body)
            manager = ConfigManager(layout, system)
            def progress(stage):
                with admin.lock: operation.update(stage=stage)
            manager.progress = progress
            result = manager.apply(patch, body['settings_revision'], verify=verify, **options)
        with admin.lock:
            operation.update(status='unchanged' if result['status'] == 'unchanged' else 'applied',
                             message='订阅、策略与更新计划已保存并验证。' if result['status'] != 'unchanged' else '配置没有变化，无需应用。',
                             backup=result.get('backup'), outcome=(result.get('runtime') or {}).get('reason') or result['status'])
    except Exception as error:
        code = str(error).split(':', 1)[0]
        message = {'APPLY_FAILED_ROLLED_BACK': '加载失败，已恢复此次保存前的配置和更新计划。',
                   'ROLLBACK_UNCONFIRMED': '加载失败，原保存文件已还原，但运行恢复尚未通过验证；请检查系统服务和恢复提示。',
                   'EXTERNAL_CONFIG_CONFLICT': '检测到外部修改，请检查运行状态并通过管理脚本恢复。',
                   'CONFIG_REVISION_CONFLICT': '配置已变化，未写入，请重新读取后再保存。',
                   'MANAGEMENT_BUSY': '其他管理任务正在进行，请稍后重试。'}.get(code, '校验或应用失败，配置未保存；请检查订阅与规则。')
        details = editor_error_details(error) or {'error': code if re.fullmatch(r'[A-Z_]{3,64}', code) else 'OPERATION_FAILED'}
        with admin.lock:
            operation.update(status='failed', message=message, **details)
