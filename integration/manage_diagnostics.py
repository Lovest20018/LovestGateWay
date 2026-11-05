"""Read-only, allowlisted diagnostics and signed recovery impact previews.

Preview tokens bind persistent file contents/permissions, service states and the
transaction journal. Volatile runtime databases/logs are intentionally excluded
from that revision; they remain included in backup checksums and restore scope.
The HTTP layer must authenticate every call and run mutations as durable jobs.
"""
import base64
import datetime
import hmac
import json
import math
import re
import stat
import time

from manage_backup import BackupStore, generated, managed
from manage_common import ManagementError, digest, operation_lock, read_bytes, require_idle, write_json
from manage_config import ConfigManager, JOURNAL
from manage_runtime import SERVICES

HEX = re.compile(r'^[0-9a-f]{64}$')
BACKUP_ID = re.compile(r'^[0-9]{14}-[0-9a-f]{12}$')
KINDS = {'manual', 'config', 'deploy', 'restore-rescue', 'asset', 'policy', 'policy-reset'}
OP_KINDS = {'config', 'policy', 'policy_reset', 'asset', 'backup', 'restore', 'recover',
            'check', 'update', 'rollback', 'install-local', 'scheduled', 'apply'}
STATUSES = {'queued', 'running', 'applying', 'succeeded', 'failed', 'interrupted',
            'applied', 'unchanged', 'complete'}
PHASES = {'queued', 'prepared', 'validating', 'saving', 'applying', 'verifying', 'rolling_back',
          'stopping', 'restoring', 'installing', 'complete', 'rolled_back', 'conflict',
          'rollback_verifying', 'rollback_unconfirmed', 'interrupted'}
CODES = {'OPERATION_FAILED', 'APPLY_FAILED_ROLLED_BACK', 'ROLLBACK_UNCONFIRMED',
         'EXTERNAL_CONFIG_CONFLICT', 'RECOVERY_REQUIRED', 'INTERRUPTED_CHECK_RECOVERY',
         'READINESS_TIMEOUT', 'API_READINESS_TIMEOUT', 'SERVICE_STATE_MISMATCH',
         'CONFIG_REVISION_CONFLICT', 'MANAGEMENT_BUSY', 'BACKUP_CHECKSUM_MISMATCH',
         'INVALID_BACKUP', 'INVALID_TRANSACTION_JOURNAL', 'STATE_SAMPLE_UNAVAILABLE',
         'RESTORE_FAILED_PREVIOUS_STATE_RESTORED', 'BACKUP_FAILED_SERVICES_RESTORED',
         'DEPLOY_FAILED_ROLLED_BACK', 'PREVIEW_EXPIRED', 'STALE_RECOVERY_PREVIEW',
         'INVALID_RECOVERY_PREVIEW', 'INVALID_RECOVERY_REQUEST', 'REVISION_KEY_REQUIRED',
         'SOURCE_CONFLICT', 'CORE_NOT_RUNNING', 'MIHOMO_REQUIRED', 'ASSET_REQUIRED',
         'SUBSCRIPTION_REQUIRED', 'INTERFACE_REQUIRED', 'INTERFACE_IPV4_REQUIRED',
         'NETWORK_RESOURCE_BUSY', 'LISTENER_UNAVAILABLE', 'SYSTEM_OPERATION_FAILED',
         'STALE_POLICY_SNAPSHOT', 'JOB_ID_REUSED'}
SECTIONS = ('configuration', 'assets', 'program', 'services', 'runtime_data')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def public_code(value):
    return value if isinstance(value, str) and value in CODES else 'OPERATION_FAILED'


def timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def service_states(value):
    """Reject unexpected unit names rather than passing them to systemctl."""
    if not isinstance(value, dict) or set(value) - set(SERVICES):
        raise ManagementError('INVALID_BACKUP')
    for state in value.values():
        if (not isinstance(state, dict) or set(state) != {'active', 'enabled'}
                or any(type(item) is not bool for item in state.values())):
            raise ManagementError('INVALID_BACKUP')
    return {unit: dict(value[unit]) for unit in SERVICES if unit in value}


def section(path):
    if path.startswith('usr/') or path.startswith('var/lib/paopaogw/clash/clash-dashboard/ui/'): return 'program'
    if path.startswith('etc/systemd/'): return 'services'
    if path.startswith('var/'): return 'runtime_data'
    if path.startswith('etc/paopaogw/files/') and path != 'etc/paopaogw/files/ppgw.ini': return 'assets'
    return 'configuration'


def volatile_runtime(path):
    """Only known live counters, logs and schedule metadata escape confirmation.

    Persisted provider payloads, dashboard programs, tokens, hosts and unknown
    managed files remain bound even when stored under var/lib.
    """
    return (path.startswith(('var/lib/paopaogw/history/', 'var/lib/paopaogw/logs/'))
            or path in {'var/lib/paopaogw/subscriptions/status.json',
                        'var/lib/paopaogw/subscriptions/schedule.json',
                        'var/lib/paopaogw/clash/cache.db',
                        'var/lib/paopaogw/clash/cache.db-shm',
                        'var/lib/paopaogw/clash/cache.db-wal'})


class Diagnostics:
    token_lifetime = 300

    def __init__(self, layout, system, clock=None):
        self.layout = layout
        self.system = system
        self.store = BackupStore(layout)
        self.manager = ConfigManager(layout, system)
        self.clock = clock or time.time
        self.progress = lambda stage: None

    def _key(self):
        key = read_bytes(self.layout, 'etc/paopaogw/revision-key')
        if not key: raise ManagementError('REVISION_KEY_REQUIRED')
        return key

    def _seal(self, domain, value):
        return hmac.new(self._key(), domain.encode('ascii') + b'\0' + canonical(value), 'sha256').hexdigest()

    def _index(self, identifier):
        try:
            value = self.store.index(identifier)
            if type(value.get('full', False)) is not bool or not isinstance(value.get('metadata', {}), dict):
                raise ValueError()
            for record in value['files'].values():
                if record is None: continue
                if (not isinstance(record, dict) or not isinstance(record.get('sha256'), str)
                        or not HEX.fullmatch(record['sha256'])
                        or any(type(record.get(key)) is not int or record[key] < 0 for key in ('mode', 'uid', 'gid'))
                        or record['mode'] > 0o7777): raise ValueError()
            if 'services' in value.get('metadata', {}): service_states(value['metadata']['services'])
            return value
        except ManagementError as error:
            if str(error) == 'BACKUP_CHECKSUM_MISMATCH': raise
            raise ManagementError('INVALID_BACKUP') from None
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            raise ManagementError('INVALID_BACKUP') from None

    def _journal(self):
        try:
            raw = read_bytes(self.layout, JOURNAL)
            value = json.loads(raw) if raw else None
            if value is not None and (not isinstance(value, dict) or value.get('phase') not in PHASES):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError):
            raise ManagementError('INVALID_TRANSACTION_JOURNAL') from None

    def _current(self, services):
        files = {}
        for name in self.store.paths():
            # Runtime data changes during normal use; never turn a busy gateway
            # into an unrecoverable sequence of instantly stale confirmations.
            if volatile_runtime(name): continue
            path = self.layout.path(name)
            raw = read_bytes(self.layout, name, limit=512 * 1024 * 1024)
            info = path.stat() if raw is not None else None
            files[name] = None if info is None else [digest(raw), stat.S_IMODE(info.st_mode),
                                                    getattr(info, 'st_uid', 0), getattr(info, 'st_gid', 0)]
        raw = read_bytes(self.layout, JOURNAL)
        return self._seal('current', {'files': files, 'services': services,
                                     'journal': digest(raw) if raw is not None else None})

    def backups(self, body=None):
        body = {} if body is None else body
        if (not isinstance(body, dict) or set(body) - {'offset', 'limit'}
                or type(body.get('offset', 0)) is not int or body.get('offset', 0) < 0
                or type(body.get('limit', 10)) is not int or not 1 <= body.get('limit', 10) <= 20):
            raise ManagementError('INVALID_RECOVERY_REQUEST')
        identifiers = self.store.list()
        offset, limit = body.get('offset', 0), body.get('limit', 10)
        rows = []
        for identifier in identifiers[offset:offset + limit]:
            row = {'id': identifier, 'created_at': None, 'kind': 'unknown', 'file_count': None,
                   'full': None, 'integrity': 'invalid'}
            try:
                date = datetime.datetime.strptime(identifier[:14], '%Y%m%d%H%M%S').replace(tzinfo=datetime.timezone.utc)
                row['created_at'] = int(date.timestamp())
                value = self._index(identifier)
                kind = value.get('metadata', {}).get('kind')
                row.update(kind=kind if isinstance(kind, str) and kind in KINDS else 'unknown',
                           file_count=sum(record is not None for record in value['files'].values()),
                           full=value.get('full', False), integrity='verified')
            except (ManagementError, ValueError, OverflowError): pass
            rows.append(row)
        return {'schema': 1, 'backups': rows, 'offset': offset, 'total': len(identifiers),
                'next_offset': offset + limit if offset + limit < len(identifiers) else None}

    def _recent(self):
        # manage_operations.history can reconcile/write abandoned work. A
        # downloadable report deliberately reads without changing any records.
        from manage_operations import DIRECTORY, alive
        directory = self.layout.path(DIRECTORY.rstrip('/'))
        paths = sorted(directory.glob('*.json'), key=lambda path: path.name)[-200:]
        rows = []
        for path in paths:
            try:
                value = json.loads(read_bytes(self.layout, DIRECTORY + path.name))
                if not isinstance(value, dict): continue
                status = value.get('status')
                interrupted = status in {'queued', 'running', 'applying'} and alive(value.get('execution')) is False
                row = {'source': value.get('source') if value.get('source') in {'settings', 'subscription', 'mihomo'} else 'unknown',
                       'kind': value.get('kind') if value.get('kind') in OP_KINDS else 'unknown',
                       'status': 'interrupted' if interrupted else status if status in STATUSES else 'unknown',
                       'phase': 'interrupted' if interrupted else value.get('phase') if value.get('phase') in PHASES else 'unknown',
                       'updated_at': timestamp(value.get('updated_at')),
                       'recovery_required': bool(interrupted or value.get('recovery') == 'check_required')}
                if interrupted or value.get('error'):
                    row['error'] = 'INTERRUPTED_CHECK_RECOVERY' if interrupted else public_code(value['error'])
                rows.append(row)
            except (OSError, ValueError, TypeError, AttributeError): pass
        return sorted(rows, key=lambda row: row['updated_at'] or 0, reverse=True)[:20]

    def report(self):
        value = {'schema': 1, 'generated_at': int(self.clock()), 'services': {},
                 'runtime': {'state': 'unknown'}, 'recovery': {'required': False, 'phase': 'none'},
                 'source': {'kind': 'unknown', 'policy_state': 'unknown', 'overridden_sections': 0},
                 'recent': self._recent(), 'causes': []}
        try: value['services'] = service_states(self.system.status())
        except Exception: value['causes'].append('STATE_SAMPLE_UNAVAILABLE')
        try:
            runtime = self.system.runtime() if self.layout.live else {}
            runtime = runtime.get('data', runtime)
            if (isinstance(runtime.get('boot_id'), str) and re.fullmatch(r'[0-9a-f-]{36}', runtime['boot_id'])
                    and isinstance(runtime.get('config_revision'), str) and HEX.fullmatch(runtime['config_revision'])):
                value['runtime']['state'] = 'running'
            elif not self.layout.live: value['runtime']['state'] = 'staged'
        except Exception: value['causes'].append('STATE_SAMPLE_UNAVAILABLE')
        try:
            journal = self._journal()
            if journal:
                value['recovery'] = {'required': journal['phase'] not in {'complete', 'rolled_back'}, 'phase': journal['phase']}
                if value['recovery']['required']: value['causes'].append('RECOVERY_REQUIRED')
        except ManagementError:
            value['recovery'] = {'required': True, 'phase': 'unknown'}
            value['causes'].append('INVALID_TRANSACTION_JOURNAL')
        try:
            from manage_preview import source_details
            details = source_details(self.layout)
            policy = details.get('local_policy', {})
            value['source'] = {'kind': details['kind'] if details['kind'] in {'none', 'local_yaml', 'remote_yaml', 'native_ppsub'} else 'unknown',
                               'policy_state': policy.get('state') if policy.get('state') in {'none', 'invalid', 'active', 'source_mismatch'} else 'unknown',
                               'overridden_sections': len(policy.get('sections', []))}
        except Exception: value['causes'].append('STATE_SAMPLE_UNAVAILABLE')
        value['causes'] = sorted(set(value['causes'] + [row['error'] for row in value['recent'] if 'error' in row]))
        return value

    def _check_expected(self, journal):
        expected = journal.get('expected')
        if expected is None: return
        if not isinstance(expected, dict): raise ManagementError('INVALID_TRANSACTION_JOURNAL')
        for name, allowed in expected.items():
            if (not isinstance(name, str) or not managed(name) or not isinstance(allowed, list) or not allowed
                    or any(item is not None and (not isinstance(item, str) or not HEX.fullmatch(item)) for item in allowed)):
                raise ManagementError('INVALID_TRANSACTION_JOURNAL')
            if generated(name): continue
            raw = read_bytes(self.layout, name, limit=512 * 1024 * 1024)
            if (digest(raw) if raw is not None else None) not in allowed:
                raise ManagementError('EXTERNAL_CONFIG_CONFLICT')

    def _file_change(self, name, record):
        path = self.layout.path(name)
        raw = read_bytes(self.layout, name, limit=512 * 1024 * 1024)
        if raw is None and record is None: return None
        if raw is None: return 'added'
        if record is None: return 'removed'
        info = path.stat()
        same = (digest(raw) == record['sha256'] and stat.S_IMODE(info.st_mode) == (record['mode'] & 0o777)
                and getattr(info, 'st_uid', 0) == record['uid'] and getattr(info, 'st_gid', 0) == record['gid'])
        return 'unchanged' if same else 'modified'

    def _scope(self, index, full):
        rows = {key: {'added': 0, 'modified': 0, 'removed': 0, 'unchanged': 0} for key in SECTIONS}
        targets = index['files'] if index else {}
        names = set(targets) | (set(self.store.paths()) if full else set())
        for name in names:
            if generated(name): continue
            change = self._file_change(name, targets.get(name))
            if change is None: continue
            rows[section(name)][change] += 1
        return rows

    def _plan(self, action, identifier):
        services = service_states(self.system.status())
        journal = self._journal()
        if action == 'restore':
            require_idle(self.layout)
            index = self._index(identifier)
            full = index.get('full', False)
            desired = service_states(index.get('metadata', {}).get('services', services)) if full else services
        else:
            if not journal or journal['phase'] in {'complete', 'rolled_back'}:
                raise ManagementError('NOTHING_TO_RECOVER')
            if journal.get('kind', 'config') not in {'config', 'policy', 'policy_reset', 'deploy', 'restore', 'backup'}:
                raise ManagementError('INVALID_TRANSACTION_JOURNAL')
            identifier = journal.get('backup')
            if not identifier and journal.get('kind', 'config') not in {'deploy', 'restore', 'backup'}:
                raise ManagementError('INVALID_TRANSACTION_JOURNAL')
            index = self._index(identifier) if identifier else None
            desired = service_states(journal.get('restore_services', journal.get('services')))
            self._check_expected(journal)
            full = False  # journal recovery restores its recorded paths, never deletes unrelated files
        preserved = set(getattr(self.system, 'preserve_units', ())) & set(SERVICES)
        effective = {unit: dict(state) for unit, state in desired.items()}
        for unit in preserved:
            if unit in services: effective[unit] = dict(services[unit])
        scope = self._scope(index, full)
        programs = any(scope['program'][key] for key in ('added', 'modified', 'removed'))
        worker = 'paopaogw-settings'
        worker_unit = 'etc/systemd/system/paopaogw-settings.service'
        targets = index['files'] if index else {}
        worker_unit_changed = (full or worker_unit in targets) and self._file_change(worker_unit, targets.get(worker_unit)) not in {None, 'unchanged'}
        worker_state_preserved = bool(worker in preserved and worker in desired and desired[worker] != services.get(worker))
        claims = {'schema': 1, 'action': action, 'backup': identifier,
                  'current': self._current(services), 'target': self._seal('target', index),
                  'expires': int(self.clock()) + self.token_lifetime}
        encoded = base64.urlsafe_b64encode(canonical(claims)).decode('ascii').rstrip('=')
        token = encoded + '.' + self._seal('token', claims)
        return {'schema': 1, 'action': action, 'backup': identifier, 'preview_token': token,
                'expires_at': claims['expires'], 'scope': scope, 'full': full,
                'target_services': desired, 'effective_services': effective,
                'impact': {'service_restart': sorted(unit for unit, state in effective.items() if state['active'] and unit not in preserved),
                           'connections_may_drop': bool(services.get('paopaogw', {}).get('active') or effective.get('paopaogw', {}).get('active')),
                           'historical_credentials_may_return': bool(index),
                           'program_files_changed': programs, 'version_may_change': programs,
                           'settings_worker_restart_required': bool((programs or worker_unit_changed) and worker in preserved),
                           'settings_worker_state_preserved': worker_state_preserved,
                           'preserved_services': sorted(preserved), 'runtime_data_in_revision': True,
                           'volatile_runtime_excluded': True,
                           'acceptance': ['service_states', 'core_readiness_if_active', 'api_readiness_if_active']}}, claims

    def preview_restore(self, body):
        if (not isinstance(body, dict) or set(body) != {'backup'} or not isinstance(body['backup'], str)
                or not BACKUP_ID.fullmatch(body['backup'])): raise ManagementError('INVALID_RECOVERY_REQUEST')
        return self._plan('restore', body['backup'])[0]

    def preview_recover(self, body):
        if body != {}: raise ManagementError('INVALID_RECOVERY_REQUEST')
        return self._plan('recover', None)[0]

    def validate_execution(self, action, body, with_plan=False):
        """Called under operation_lock immediately before any transaction writes."""
        if (action not in {'restore', 'recover'} or not isinstance(body, dict) or set(body) != {'preview_token'}
                or not isinstance(body['preview_token'], str) or len(body['preview_token']) > 2048):
            raise ManagementError('INVALID_RECOVERY_REQUEST')
        try:
            encoded, signature = body['preview_token'].split('.')
            claims = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
            if (not isinstance(claims, dict) or set(claims) != {'schema', 'action', 'backup', 'current', 'target', 'expires'}
                    or claims['schema'] != 1 or claims['action'] != action
                    or type(claims['expires']) is not int
                    or not hmac.compare_digest(signature, self._seal('token', claims))): raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise ManagementError('INVALID_RECOVERY_PREVIEW') from None
        if self.clock() >= claims['expires']: raise ManagementError('PREVIEW_EXPIRED')
        if claims['expires'] > self.clock() + self.token_lifetime: raise ManagementError('INVALID_RECOVERY_PREVIEW')
        plan, current = self._plan(action, claims['backup'])
        if any(claims[key] != current[key] for key in ('current', 'target', 'backup')):
            raise ManagementError('STALE_RECOVERY_PREVIEW')
        return plan if with_plan else claims['backup']

    def execute_restore(self, body):
        from manage import restore_backup
        with operation_lock(self.layout):
            plan = self.validate_execution('restore', body, with_plan=True)
            result = restore_backup(self.layout, self.system, plan['backup'], progress=self.progress)
            result['settings_worker_restart_required'] = plan['impact']['settings_worker_restart_required']
            result['settings_worker_state_preserved'] = plan['impact']['settings_worker_state_preserved']
            return result

    def execute_recover(self, body):
        # Recovery can replace this module's on-disk dependencies with an older
        # snapshot. Capture the current worker's verifier before restoring files.
        from manage import verify_service_restore
        with operation_lock(self.layout):
            plan = self.validate_execution('recover', body, with_plan=True)
            self.progress('rolling_back')
            result = self.manager.recover()
            self.progress('verifying')
            try:
                result['acceptance'] = verify_service_restore(self.layout, self.system, plan['effective_services'])
            except Exception:
                journal = self._journal()
                if journal:
                    journal['phase'] = 'rollback_unconfirmed'
                    write_json(self.layout, JOURNAL, journal)
                raise ManagementError('ROLLBACK_UNCONFIRMED') from None
            result['settings_worker_restart_required'] = plan['impact']['settings_worker_restart_required']
            result['settings_worker_state_preserved'] = plan['impact']['settings_worker_state_preserved']
            return result

    def execute_backup(self, body):
        if body != {}: raise ManagementError('INVALID_RECOVERY_REQUEST')
        from manage import create_backup
        return create_backup(self.layout, self.system, progress=self.progress)
