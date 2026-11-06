"""Durable configuration transactions, safe preview, and conditional recovery."""
import json
from manage_common import ManagementError, operation_lock, atomic_write, read_bytes, write_json, digest
from manage_model import load_settings, public_settings, render_updates, settings_revision
from manage_backup import BackupStore
from manage_runtime import System

JOURNAL = 'var/lib/paopaogw/admin/transaction.json'
SAFE_RELOAD = {'max_rec', 'net_cleanday', 'admin_cidr', 'proxy_cidr',
               'splice_threshold', 'clash_web_password', 'openport_auth'}


class ConfigManager:
    def __init__(self, layout, system=None):
        self.layout = layout; self.system = system or System(layout); self.backups = BackupStore(layout)
        self.progress = lambda stage: None
    def show(self): return {'revision': settings_revision(self.layout), 'settings': public_settings(load_settings(self.layout))}
    def journal(self):
        raw = read_bytes(self.layout, JOURNAL)
        return json.loads(raw) if raw else None
    def plan(self, patch, reset_source=False):
        updates, changes = render_updates(self.layout, patch)
        if set(changes.get('ini', {})) & {'mode', 'suburl', 'yamlfile'}:
            target = load_settings(self.layout)['ini']; target.update(changes.get('ini', {}))
            names = ('clash-policy.json',) if target['mode'] == 'suburl' and target['suburl'].startswith('ppsub@') else ('ppsub.json', 'clash-policy.json')
            conflicts = ['etc/paopaogw/files/' + name for name in names
                         if self.layout.path('etc/paopaogw/files/' + name).exists()]
            if conflicts and not reset_source: raise ManagementError('SOURCE_CONFLICT:use --reset-source to remove local PPsub/policy')
            for path in conflicts: updates[path] = None
        action = 'none'
        if changes.get('ini'): action = 'reload' if set(changes['ini']) <= SAFE_RELOAD else 'restart'
        if set(changes.get('ini', {})) & {'max_rec', 'net_cleanday'} and not load_settings(self.layout)['ini']['net_rec']:
            action = 'restart'
        if changes.get('service'): action = 'restart'
        return updates, changes, action
    def apply(self, patch, base_revision, dry_run=False, reset_source=False, restart=False,
              policy_update=None, verify=None, ppsub_update=None, mihomo_update=None):
        def policy_action():
            if mihomo_update is not None:
                from mihomo_reload import plan
                if (set(mihomo_update) != {'before', 'after'} or policy_update in (None, False)
                        or not plan(mihomo_update['before'], mihomo_update['after'])['eligible']):
                    raise ManagementError('MIHOMO_RELOAD_UNSUPPORTED')
                if not changes and ppsub_update is None and not restart:
                    self.system._mihomo_update = mihomo_update
                    return 'mihomo_reload'
            return 'restart'
        if ppsub_update is not None and (not isinstance(ppsub_update, bytes) or len(ppsub_update) > 1024*1024):
            raise ManagementError('INVALID_PPSUB_UPDATE')
        # Preview does not create a lock, journal or backup.
        updates, changes, action = self.plan(patch, reset_source)
        if policy_update is not None:
            if policy_update is not False and not isinstance(policy_update, bytes): raise ManagementError('INVALID_POLICY_UPDATE')
            updates['etc/paopaogw/files/clash-policy.json'] = None if policy_update is False else policy_update
            action = policy_action()
        if ppsub_update is not None:
            updates['etc/paopaogw/files/ppsub.json'] = ppsub_update
            action = 'restart'
        if restart: action = 'restart'
        if dry_run:
            if settings_revision(self.layout) != base_revision: raise ManagementError('CONFIG_REVISION_CONFLICT')
            settings = load_settings(self.layout)
            for namespace, fields in changes.items(): settings[namespace].update(fields)
            self.system.preflight(settings)
            return {'status': 'preview', 'changes': public_settings(changes), 'action': action, 'revision': settings_revision(self.layout)}
        with operation_lock(self.layout):
            if self.journal() and self.journal()['phase'] not in {'complete', 'rolled_back'}: raise ManagementError('RECOVERY_REQUIRED')
            if settings_revision(self.layout) != base_revision: raise ManagementError('CONFIG_REVISION_CONFLICT')
            updates, changes, action = self.plan(patch, reset_source)
            if policy_update is not None:
                updates['etc/paopaogw/files/clash-policy.json'] = None if policy_update is False else policy_update
                action = policy_action()
            if ppsub_update is not None:
                updates['etc/paopaogw/files/ppsub.json'] = ppsub_update
                action = 'restart'
            if restart: action = 'restart'
            if not updates and not restart: return {'status': 'unchanged', 'revision': base_revision}
            settings = load_settings(self.layout)
            for namespace, fields in changes.items(): settings[namespace].update(fields)
            self.system.preflight(settings)
            before = self.system.status()
            backup = self.backups.create(updates, {'services': before, 'kind': 'config'})
            original = self.backups.index(backup)['files']
            journal = {'kind': 'config', 'phase': 'prepared', 'backup': backup, 'services': before,
                       'expected': {path: [None if raw is None else digest(raw), None if original[path] is None else original[path]['sha256']] for path, raw in updates.items()}}
            if action == 'none' and set(changes) <= {'api', 'maintenance'}:
                affected = set()
                if changes.get('api'): affected.add('paopaogw-api')
                maintenance = changes.get('maintenance', {})
                if 'enabled' in changes.get('api', {}) or set(maintenance) & {'history_enabled', 'history_retention_days'}:
                    affected.add('paopaogw-observe')
                if 'auto_update' in maintenance: affected.add('paopaogw-maintenance.timer')
                journal['restore_services'] = {unit: state for unit, state in before.items() if unit in affected}
            write_json(self.layout, JOURNAL, journal)
            try:
                self.progress('saving')
                if hasattr(self.system, 'prepare'): self.system.prepare(action, before)
                for path, raw in updates.items():
                    if raw is None: self.layout.path(path).unlink(missing_ok=True)
                    else: atomic_write(self.layout, path, raw)
                journal['phase'] = 'applying'; write_json(self.layout, JOURNAL, journal)
                self.progress('applying')
                running = self.system.apply(action, settings, changes, before)
                self.progress('verifying')
                if verify is not None: verify()
                journal['phase'] = 'complete'; write_json(self.layout, JOURNAL, journal)
                from manage_operations import remember_loaded
                if isinstance(running, dict):
                    if running.get('status') == 'running':
                        remember_loaded(self.layout, settings_revision(self.layout), running)
                    elif running.get('reason') == 'core_unchanged':
                        try:
                            self.system.verify_loaded(settings)
                            remember_loaded(self.layout, settings_revision(self.layout), self.system.runtime())
                        except Exception: pass
                return {'status': 'applied', 'revision': settings_revision(self.layout), 'backup': backup, 'action': action, 'runtime': running}
            except Exception:
                self.progress('rolling_back')
                self._rollback(journal)
                raise ManagementError('APPLY_FAILED_ROLLED_BACK') from None
    def _rollback(self, journal):
        try:
            self.backups.restore(journal['backup'], expected=journal['expected'])
        except ManagementError:
            journal['phase'] = 'conflict'; write_json(self.layout, JOURNAL, journal)
            raise ManagementError('EXTERNAL_CONFIG_CONFLICT') from None
        restore = journal.get('restore_services', journal['services'])
        self.system.restore_services(restore)
        if self.layout.live:
            journal['phase'] = 'rollback_verifying'; write_json(self.layout, JOURNAL, journal)
            try:
                self.system.verify_services(restore)
                settings = load_settings(self.layout)
                if restore.get('paopaogw', {}).get('active'):
                    runtime = self.system.wait(settings)
                    from manage_operations import remember_loaded
                    remember_loaded(self.layout, settings_revision(self.layout), runtime)
                if restore.get('paopaogw-api', {}).get('active'):
                    # A historical snapshot may predate explicit API opt-in.
                    # The recorded active service still requires real readiness.
                    settings['api']['enabled'] = True
                    self.system.wait_api(settings)
            except Exception:
                journal['phase'] = 'rollback_unconfirmed'; write_json(self.layout, JOURNAL, journal)
                raise ManagementError('ROLLBACK_UNCONFIRMED') from None
        journal['phase'] = 'rolled_back'; write_json(self.layout, JOURNAL, journal)
    def recover(self):
        with operation_lock(self.layout):
            journal = self.journal()
            if not journal or journal['phase'] in {'complete', 'rolled_back'}: return {'status': 'nothing_to_recover'}
            if journal.get('kind') == 'subscription':
                from manage_subscription import rollback_subscription
                rollback_subscription(self.layout, self.system, journal)
                return {'status': 'rolled_back', 'backup': journal['backup']}
            if journal.get('kind') in {'deploy', 'restore', 'backup'}:
                from manage_deploy import Deployer
                return Deployer(self.layout, self.system).recover()
            self._rollback(journal)
            return {'status': 'rolled_back', 'backup': journal['backup']}
