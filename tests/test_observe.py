from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import observe
from gateway_api import ApiError, mihomo_connections


class Core:
    def __init__(self, mode='suburl', rows=None):
        self.mode, self.rows, self.calls = mode, rows, []
        self.boot = 'fixture-boot'

    def request(self, path, body=None):
        self.calls.append(path)
        if path == '/sniffbox/info':
            return {'mode': self.mode, 'integration': {'boot_id': 'fixture-boot'}}
        if path == '/sniffbox/evidence':
            return {'boot_id': self.boot, 'data': {'items': [{'id': 1, 'domain': 'example.test'}]}}
        if path == '/connections':
            if isinstance(self.rows, Exception): raise self.rows
            return {'connections': self.rows}
        raise AssertionError(path)


class ObserveTests(unittest.TestCase):
    def test_both_modes_normalize_empty_then_active_then_empty_and_commit_once(self):
        for mode in ('yaml', 'suburl'):
            core, writer, report = Core(mode), Mock(), Mock()
            for rows in (None, [], [{'id': 'm1', 'chains': ['Node'], 'metadata': {'host': 'example.test', 'password': 'private'}, 'password': 'private'}], None):
                core.rows = rows
                observe.collect_once(core, writer, 30, report)
                boot, batches, retention = writer.store.call_args.args
                self.assertEqual(boot, 'fixture-boot')
                self.assertEqual(retention, 30)
                self.assertEqual([source for source, _ in batches], ['sniffbox_lifecycle', 'mihomo_snapshot'])
                self.assertEqual(len(batches[1][1]), 1 if rows else 0)
                self.assertNotIn('private', str(batches))
            self.assertEqual(writer.store.call_count, 4)
            report.assert_not_called()

    def test_bad_mihomo_values_and_network_failures_preserve_native_sample(self):
        for rows in (False, 0, '', {}, [None], ApiError(503, 'CORE_UNAVAILABLE')):
            with self.subTest(rows=rows):
                core, writer, report = Core(rows=rows), Mock(), Mock()
                observe.collect_once(core, writer, 30, report)
                self.assertEqual([source for source, _ in writer.store.call_args.args[1]], ['sniffbox_lifecycle'])
                report.assert_called_once()
                self.assertEqual(report.call_args.args[0], 'mihomo')

    def test_non_mihomo_mode_and_boot_change(self):
        core, writer, report = Core('free'), Mock(), Mock()
        observe.collect_once(core, writer, 30, report)
        self.assertNotIn('/connections', core.calls)
        self.assertEqual(len(writer.store.call_args.args[1]), 1)
        core.boot = 'other-boot'
        with self.assertRaises(ApiError): observe.collect_once(core, writer, 30, report)
        self.assertEqual(writer.store.call_count, 1)

    def test_invalid_snapshot_envelopes_are_not_empty_snapshots(self):
        for response in (None, [], {}, {'other': []}, {'connections': ''}):
            with self.subTest(response=response):
                with self.assertRaises(ApiError): mihomo_connections(response)

    def test_diagnostics_are_rate_limited_and_never_log_exception_content(self):
        clock, emit = Mock(return_value=0), Mock()
        report = observe.ErrorReporter(clock, emit)
        report('sample', ValueError('private credential or URL'))
        clock.return_value = 1
        report('sample', ValueError('private credential or URL'))
        self.assertEqual(emit.call_count, 1)
        clock.return_value = 60
        report('sample', ValueError('private credential or URL'))
        self.assertEqual(emit.call_count, 2)
        self.assertNotIn('private', str(emit.call_args_list))

    def test_failed_identity_sample_sleeps_instead_of_spinning(self):
        class StopLoop(Exception): pass
        core, writer = Core(), Mock()
        core.boot = 'other-boot'
        def config(path):
            return b'{"enabled":true}' if path.name == 'api.json' else b'{"history_enabled":true}'
        with patch.object(observe.Path, 'read_bytes', config), patch.object(observe, 'CoreClient', return_value=core), patch.object(observe, 'HistoryWriter', return_value=writer), patch.object(observe, 'ErrorReporter', return_value=Mock()), patch.object(observe.time, 'sleep', side_effect=StopLoop) as sleep:
            with self.assertRaises(StopLoop): observe.main()
        sleep.assert_called_once_with(1)
        writer.store.assert_not_called()
