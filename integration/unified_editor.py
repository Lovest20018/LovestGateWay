"""One transaction for the subscription editor and its update schedule."""
from copy import deepcopy
import json
import re
import threading
import time
from manage_common import ManagementError, operation_lock, require_idle
from manage_config import ConfigManager
from manage_model import load_settings, settings_revision, validate
from manage_runtime import System
from clash_ppsub import (merge_editor, feature_options, apply_features, fingerprint,
                         source_identity, logical_document)
from clash_policy import build_policy


def validate_ppsub(value):
    if not isinstance(value, dict) or not isinstance(value.get('subs'), list) or not value['subs']:
        raise ManagementError('INVALID_PPSUB')
    if not value.get('exported_at') or len(value['subs']) > 500:
        raise ManagementError('INVALID_PPSUB')
    names = set()
    for row in value['subs']:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip() or row['name'] in names:
            raise ManagementError('INVALID_PPSUB')
        from manage_subscription_schedule import validate_plan
        validate_plan(row)
        names.add(row['name'])
        validate('ini', 'test_node_url', row.get('url'))
    for key in ('node-groups', 'rules'):
        if not isinstance(value.get(key, []), list) or len(value.get(key, [])) > 5000:
            raise ManagementError('INVALID_PPSUB')
    feature_options(value)
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def candidate(admin, body):
    import subscription_admin as source
    allowed = {'kind', 'edit_kind', 'value', 'base_revision', 'settings_revision', 'settings_patch',
               'operation_id', 'snapshot_id', 'dry_run'}
    if (set(body) - allowed or body.get('edit_kind') not in {'ppsub', 'clash_patch'}
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
    submitted = json.loads(body['value'])
    options = {'policy_update': None, 'ppsub_update': None}
    verify = None
    settings = load_settings(layout)
    if settings['ini']['mode'] == 'yaml' and patch.get('ini'):
        raise ManagementError('INVALID_EDITOR_REQUEST')
    if body['edit_kind'] == 'clash_patch':
        with admin.lock:
            snapshot = deepcopy(admin.snapshots.get(body.get('snapshot_id')))
        if (not snapshot or time.monotonic() - snapshot['created'] > 1800
                or snapshot['revision'] != body['base_revision']
                or fingerprint(source.running_document()) != fingerprint(snapshot['document'])
                or (source.POLICY.read_bytes() if source.POLICY.exists() else None) != snapshot['policy']):
            raise ManagementError('CONFIG_CHANGED')
        merged, source_url, renames = merge_editor(snapshot['document'], snapshot['baseline'], submitted)
        if source_url: raise ManagementError('SOURCE_CHANGE_REQUIRES_SWITCH')
        features = feature_options(submitted)
        if merged != snapshot['document'] or features != feature_options(snapshot['baseline']):
            existing = json.loads(snapshot['policy']) if snapshot['policy'] else None
            policy = build_policy(snapshot['document'], merged, existing, source_identity(settings['ini']), renames)
            policy['features'] = features
            effective = apply_features(merged, features)
            options['policy_update'] = (json.dumps(policy, ensure_ascii=False, indent=2) + '\n').encode()
            def verify():
                actual = source.running_document()
                expected = logical_document(effective)
                if any(actual.get(key) != expected.get(key) for key in policy['sections']):
                    raise ManagementError('POLICY_NOT_LOADED')
                if layout.live:
                    from clash_policy import protected_revision
                    status = json.loads(layout.path('var/lib/paopaogw/admin/policy-status.json').read_bytes())
                    key = layout.path('etc/paopaogw/revision-key').read_bytes().strip()
                    if status.get('status') != 'loaded' or status.get('policy_revision') != protected_revision(policy, key):
                        raise ManagementError('POLICY_NOT_LOADED')
        else: effective = None
    else:
        if not source.native_ppsub_source(source.config_values()):
            raise ManagementError('SOURCE_CHANGE_REQUIRES_SWITCH')
        raw = validate_ppsub(submitted)
        previous = source.FILES / 'ppsub.json'
        old = json.loads(previous.read_bytes()) if previous.exists() else None
        def stable(value):
            value = deepcopy(value)
            if isinstance(value, dict): value.pop('exported_at', None)
            return value
        if stable(old) != stable(submitted): options['ppsub_update'] = raw
        # Saving a remote PPsub materializes it as the local authoritative source.
        patch = deepcopy(patch)
        patch.setdefault('ini', {}).update(mode='suburl', suburl='ppsub@http://127.0.0.1/ppsub.json', fast_node='no')
        effective = None
        def verify():
            if not admin.core.request('/sniffbox/info').get('ppsub'):
                raise ManagementError('PPSUB_NOT_LOADED')
            if layout.live:
                actual = json.loads(layout.path('var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json').read_bytes())
                if stable(actual) != stable(submitted): raise ManagementError('PPSUB_NOT_LOADED')
    system = System(layout, preserve_units={'paopaogw-subscription'})
    if effective is not None: system.validate_policy(effective)
    return layout, system, patch, options, verify


def handle(admin, body):
    import subscription_admin as source
    if body['kind'] == 'export_yaml':
        if set(body) != {'kind', 'snapshot_id', 'base_revision'}:
            raise ManagementError('INVALID_EDITOR_REQUEST')
        with admin.lock: snapshot = deepcopy(admin.snapshots.get(body['snapshot_id']))
        if (not snapshot or time.monotonic() - snapshot['created'] > 1800
                or body['base_revision'] != source.revision(source.CONFIG.read_bytes())
                or snapshot['revision'] != body['base_revision']
                or fingerprint(source.running_document()) != fingerprint(snapshot['document'])):
            raise ManagementError('CONFIG_CHANGED')
        import yaml
        return {'filename': 'gateway-running.yaml', 'content': yaml.safe_dump(
            apply_features(snapshot['document'], feature_options(snapshot['baseline'])), allow_unicode=True, sort_keys=False)}
    if not body.get('dry_run'):
        with admin.lock:
            if admin.operation and admin.operation.get('id') == body.get('operation_id'):
                return dict(admin.operation)
    layout, system, patch, options, verify = candidate(admin, body)
    if body.get('dry_run'):
        result = ConfigManager(layout, system).apply(patch, body['settings_revision'], dry_run=True, **options)
        result['editor_changed'] = any(raw is not None for raw in options.values())
        result['save_target'] = 'local_policy' if body['edit_kind'] == 'clash_patch' else 'ppsub'
        return result
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
            admin.snapshots.pop(body.get('snapshot_id'), None)
    except Exception as error:
        code = str(error).split(':', 1)[0]
        message = {'APPLY_FAILED_ROLLED_BACK': '加载失败，已恢复此次保存前的配置和更新计划。',
                   'ROLLBACK_UNCONFIRMED': '加载失败，原保存文件已还原，但运行恢复尚未通过验证；请检查系统服务和恢复提示。',
                   'EXTERNAL_CONFIG_CONFLICT': '检测到外部修改，请检查运行状态并通过管理脚本恢复。',
                   'CONFIG_REVISION_CONFLICT': '配置已变化，未写入，请重新读取后再保存。',
                   'CONFIG_CHANGED': '运行配置或快照已变化，未写入，请重新读取后再保存。',
                   'MANAGEMENT_BUSY': '其他管理任务正在进行，请稍后重试。'}.get(code, '校验或应用失败，配置未保存；请检查订阅与规则。')
        with admin.lock: operation.update(status='failed', message=message, error=code if re.fullmatch(r'[A-Z_]{3,64}',code) else 'OPERATION_FAILED')
