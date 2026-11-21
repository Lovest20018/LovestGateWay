from pathlib import Path
import datetime
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import observe
import history_store
from gateway_api import ApiError, mihomo_connections


class Core:
    def __init__(self, mode='suburl', rows=None):
        self.mode, self.rows, self.calls = mode, rows, []
        self.boot = 'fixture-boot'

    def request(self, path, body=None):
        self.calls.append(path)
        if path in {'/sniffbox/info', '/sniffbox/info/dynamic'}:
            return {'mode': self.mode, 'integration': {'boot_id': 'fixture-boot'}}
        if path == '/sniffbox/evidence':
            return {'boot_id': self.boot, 'data': {'items': [{'id': 1, 'domain': 'example.test'}]}}
        if path == '/connections':
            if isinstance(self.rows, Exception): raise self.rows
            return {'connections': self.rows}
        if path == '/rules': return {'rules': []}
        raise AssertionError(path)


class ObserveTests(unittest.TestCase):
    def test_restart_after_native_pages_never_commits_old_boot_snapshots(self):
        class RestartingCore(Core):
            def request(self,path,body=None):
                if path=='/sniffbox/info/dynamic': return {'integration':{'boot_id':'new-boot'}}
                return super().request(path,body)
        writer=Mock();writer.cursor.return_value=0
        with self.assertRaises(ApiError):observe.collect_once(RestartingCore(),writer,30,Mock())
        writer.store.assert_not_called()

    def test_pages_are_drained_and_cursor_survives_collector_restart(self):
        class PagedCore(Core):
            def request(self, path, body=None):
                if path != '/sniffbox/evidence': return super().request(path, body)
                after = body['after']; end = min(after+200, 450)
                return {'boot_id': self.boot, 'data': {'items': [{'id': str(i), 'ended_at': 1,
                        'decision': {'connect_result': 'failed'}} for i in range(after+1,end+1)],
                        'next_sequence': end, 'truncated': end<450}}
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'history.sqlite3';core=PagedCore();report=Mock()
            with history_store.HistoryWriter(path) as writer: observe.collect_once(core,writer,30,report)
            with history_store.HistoryWriter(path) as writer:
                self.assertEqual(writer.cursor(core.boot),450)
                observe.collect_once(core,writer,30,report)
            result=history_store.metrics_summary(path)
            self.assertEqual(result['connection_rows'],450)
            self.assertEqual(result['failed'],450)
            report.assert_not_called()

    def test_latency_uses_only_existing_fresh_default_outbound_checks(self):
        client=Mock();now=1700000000
        def request(path):
            if path=='/rules':return {'rules':[{'type':'Match','proxy':'Main'}]}
            return {'proxies':{'Main':{'all':['N1'],'now':'N1'},'N1':{'history':[
                {'time':datetime.datetime.fromtimestamp(now-5,datetime.timezone.utc).isoformat(),'delay':25}]}}}
        client.request.side_effect=request
        self.assertEqual(observe.selected_latency(client,now),25)
        self.assertIsNone(observe.selected_latency(client,now+120))
        self.assertTrue(all(call.args[0] in ['/rules','/proxies'] for call in client.request.call_args_list))

    def test_latency_retains_checks_on_selected_nested_groups(self):
        now=1700000000;checked=datetime.datetime.fromtimestamp(now-5,datetime.timezone.utc).isoformat()
        client=Mock()
        proxies={'Main':{'all':['Region'],'now':'Region'},'Region':{'all':['N1'],'now':'N1',
                 'history':[{'time':checked,'delay':32}]},'N1':{'history':[]}}
        client.request.side_effect=lambda path: {'rules':[{'type':'Match','proxy':'Main'},{'type':'Match','proxy':'REJECT'}]} if path=='/rules' else {'proxies':proxies}
        self.assertEqual(observe.selected_latency(client,now),32)
        proxies['Region']['history'][-1]['delay']=0
        self.assertIsNone(observe.selected_latency(client,now))

    def test_both_modes_normalize_empty_then_active_then_empty_and_commit_once(self):
        for mode in ('yaml', 'suburl'):
            core, writer, report = Core(mode), Mock(), Mock()
            writer.cursor.return_value = 0
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
                writer.cursor.return_value = 0
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

    def test_step_recovery_distinguishes_brief_startup_from_a_persistent_failure(self):
        clock, emit, recover = Mock(return_value=0), Mock(), Mock()
        report = observe.ErrorReporter(clock, emit, recover)
        unavailable = ApiError(503, 'CORE_UNAVAILABLE')
        report('sample', unavailable)
        clock.return_value = 12
        report.recovered('sample')
        emit.assert_not_called()
        self.assertEqual(recover.call_args.args[1:], ('sample', 'CORE_UNAVAILABLE', 12))
        report('sample', unavailable)
        clock.return_value = 32
        report('sample', unavailable)
        self.assertEqual(emit.call_args.args[1:], ('sample', 'CORE_UNAVAILABLE', 20))
        clock.return_value = 36
        report('sample', unavailable)
        emit.assert_called_once()
        report.recovered('sample')
        self.assertEqual(recover.call_args.args[1:], ('sample', 'CORE_UNAVAILABLE', 24))

    def test_collector_projects_the_internal_traffic_generation(self):
        class TrafficCore(Core):
            def request(self, path, body=None):
                result = super().request(path, body)
                if path == '/sniffbox/info':
                    result['traffic'] = {'downloadTotal': 24, 'uploadTotal': 8, 'resetGeneration': 2}
                return result
        writer = Mock()
        observe.collect_once(TrafficCore('free'), writer, 30, Mock())
        self.assertEqual(writer.store.call_args.kwargs['metrics']['reset_generation'], 2)

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
