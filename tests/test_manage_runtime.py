from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from manage_common import Layout
from manage_model import load_settings
from manage_runtime import System


class LiveFixture(Layout):
    @property
    def live(self): return True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.layout = LiveFixture(self.temp.name)
        self.system = System(self.layout, preserve_units={'paopaogw-settings'})
        self.commands = []
        def command(*args, **kw):
            self.commands.append(args)
            return SimpleNamespace(returncode=0, stdout='loaded')
        self.system.command = command

    def test_rollback_keeps_the_job_worker_alive_and_restores_other_units(self):
        states = {unit: {'active': True, 'enabled': True} for unit in ('paopaogw', 'paopaogw-settings', 'paopaogw-api')}
        self.system.stop_all(); self.system.restore_services(states)
        self.assertFalse(any('paopaogw-settings' in command for command in self.commands))
        self.assertIn(('systemctl', 'restart', 'paopaogw'), self.commands)
        self.assertIn(('systemctl', 'restart', 'paopaogw-api'), self.commands)

    def test_auxiliary_changes_do_not_restart_the_forwarder(self):
        settings = load_settings(self.layout)
        self.system.wait_api = lambda settings: None
        result = self.system.apply('none', settings, {'api': {'name': 'Fixture'}},
                                  {'paopaogw': {'active': True}, 'paopaogw-api': {'active': True}})
        self.assertEqual(result['reason'], 'core_unchanged')
        self.assertEqual(self.commands, [('systemctl', 'restart', 'paopaogw-api')])

    def test_reload_uses_core_only_endpoint_and_falls_back_when_old_state_unknown(self):
        requests = []
        self.system.client = lambda: SimpleNamespace(request=lambda *args: requests.append(args))
        self.system.wait = lambda *args: {'status': 'running'}
        settings = load_settings(self.layout); before = {'paopaogw': {'active': True}}
        self.system._old_runtime = {'boot_id': 'fixture'}
        self.system.apply('reload', settings, {}, before)
        self.assertEqual(requests, [('/sniffbox/reload/core', {})])
        self.assertEqual(self.commands, [])
        self.system._old_runtime = None
        self.system.apply('reload', settings, {}, before)
        self.assertEqual(self.commands, [('systemctl', 'restart', 'paopaogw')])

    def test_password_preflight_accepts_eight_and_rejects_seven_characters(self):
        from manage_common import ManagementError
        settings = load_settings(self.layout)
        settings['service']['PPGW_INTERFACE'] = 'ens18'
        settings['ini']['clash_web_password'] = 'Fx8_pass'
        self.system.command = lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='[{"addr_info":[{"family":"inet","scope":"global"}]}]')
        self.system.preflight(settings)
        settings['ini']['clash_web_password'] = 'seven77'
        with self.assertRaisesRegex(ManagementError, 'DASHBOARD_PASSWORD_TOO_SHORT'):
            self.system.preflight(settings)

    def test_first_install_rejects_occupied_listener_before_commands(self):
        import socket
        from manage_common import ManagementError
        settings = load_settings(self.layout)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            settings['service']['PPGW_WEB_LISTEN'] = '127.0.0.1:' + str(listener.getsockname()[1])
            with self.assertRaisesRegex(ManagementError, 'LISTENER_UNAVAILABLE'):
                self.system.preflight_listeners(settings)
        self.assertEqual(self.commands, [])

    def test_boot_enable_failure_is_reported(self):
        from manage_common import ManagementError
        def failed(*args, **kwargs):
            if args[1] == 'enable':
                self.assertTrue(kwargs.get('check', True))
                raise ManagementError('SYSTEM_OPERATION_FAILED:systemctl')
            return SimpleNamespace(returncode=0, stdout='loaded')
        self.system.command = failed
        with self.assertRaisesRegex(ManagementError, 'SYSTEM_OPERATION_FAILED'):
            self.system.restore_services({'paopaogw': {'active': True, 'enabled': True}})

    def test_boot_state_mismatch_is_not_success(self):
        from manage_common import ManagementError
        self.system.status = lambda: {'paopaogw': {'active': True, 'enabled': False}}
        with self.assertRaisesRegex(ManagementError, 'SERVICE_STATE_MISMATCH'):
            self.system.verify_services({'paopaogw': {'active': True, 'enabled': True}})

    def test_dns_preflight_allows_tcp_only_occupancy_but_rejects_udp_occupancy(self):
        import socket
        from unittest.mock import patch
        from manage_common import ManagementError
        settings = load_settings(self.layout)
        settings['service']['PPGW_DNS_LISTEN'] = '127.0.0.1:1053'
        self.system.command = lambda *args, **kw: SimpleNamespace(returncode=1 if args[0] == 'nft' else 0, stdout='[]')
        for occupied in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            class Listener:
                def __init__(self, family, kind): self.kind = kind
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def setsockopt(self, *args): pass
                def listen(self, *args): pass
                def bind(self, address):
                    if address == ('127.0.0.1', 1053) and self.kind == occupied: raise OSError('occupied')
            with self.subTest(occupied=occupied), patch('manage_runtime.socket.socket', Listener):
                if occupied == socket.SOCK_DGRAM:
                    with self.assertRaisesRegex(ManagementError, 'LISTENER_UNAVAILABLE'): self.system.preflight_listeners(settings)
                else: self.system.preflight_listeners(settings)

    def test_missing_fib_table_is_empty_but_route_query_errors_are_not_ignored(self):
        from unittest.mock import patch
        from manage_common import ManagementError
        settings = load_settings(self.layout)
        for stderr, allowed in [('Error: ipv4: FIB table does not exist.', True), ('Operation not permitted', False)]:
            def command(*args, **kwargs):
                if args[0] == 'nft': return SimpleNamespace(returncode=1, stdout='', stderr='No such file or directory')
                if 'route' in args: return SimpleNamespace(returncode=2, stdout='[', stderr=stderr)
                return SimpleNamespace(returncode=0, stdout='[]', stderr='')
            self.system.command = command
            with self.subTest(stderr=stderr), patch('manage_runtime.socket.socket'):
                if allowed: self.system.preflight_listeners(settings)
                else:
                    with self.assertRaisesRegex(ManagementError, 'SYSTEM_OPERATION_FAILED'): self.system.preflight_listeners(settings)


if __name__ == '__main__': unittest.main()
