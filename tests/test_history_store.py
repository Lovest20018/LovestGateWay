from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import history_store as history


class HistoryTests(unittest.TestCase):
    def test_outbound_restart_and_large_counters_do_not_create_false_traffic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'metrics.sqlite3';base=2**60
            with history.HistoryWriter(path) as writer:
                writer.store('boot',[],now=600,metrics={'download':base,'upload':0,'outbound_uptime':100})
                writer.store('boot',[],now=601,metrics={'download':base+10,'upload':1,'outbound_uptime':101})
                writer.store('boot',[],now=602,metrics={'download':base+100,'upload':20,'outbound_uptime':1})
            result=history.metrics_summary(path,now=602)
            self.assertEqual(result['download_bytes'],10)
            self.assertEqual(result['upload_bytes'],1)
            self.assertEqual(result['coverage_seconds'],1)
            self.assertEqual(result['points'][0]['gaps'],1)

    def test_metrics_persist_deltas_cursors_and_results_without_double_counting(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.sqlite3'
            rows = [{'id': 'ok', 'ended_at': 90, 'decision': {'connect_result': 'connected'}},
                    {'id': 'bad', 'ended_at': 91, 'decision': {'connect_result': 'failed'}},
                    {'id': 'unknown', 'ended_at': 92, 'decision': {'connect_result': 'unknown'}}]
            sample = {'download': 100, 'upload': 10, 'cpu': 20, 'memory': 2048, 'active': 3,
                      'latency_ms': 15, 'latency_checked': True}
            with history.HistoryWriter(path) as writer:
                writer.store('boot', [('sniffbox_lifecycle', rows)], now=600, metrics=sample, cursor=3)
                writer.store('boot', [('sniffbox_lifecycle', rows)], now=601,
                             metrics={**sample, 'download': 250, 'upload': 30}, cursor=3)
            with history.HistoryWriter(path) as writer:
                self.assertEqual(writer.cursor('boot'), 3)
                self.assertEqual(writer.cursor('other-boot'), 0)
                writer.store('boot', [], now=602, metrics={**sample, 'download': 350, 'upload': 40})
            value = history.metrics_summary(path, now=602)
            self.assertEqual((value['download_bytes'], value['upload_bytes'], value['coverage_seconds']), (250, 30, 2))
            self.assertEqual((value['connected'], value['failed'], value['failure_percent']), (1, 1, 50))
            self.assertEqual(value['points'][0]['cpu_percent'], 20)
            self.assertEqual(value['points'][0]['latency_ms'], 15)
            self.assertNotIn('decision', str(value))

    def test_metrics_counter_resets_gaps_and_missing_values_never_invent_zeros(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.sqlite3'
            with history.HistoryWriter(path) as writer:
                writer.store('one', [], now=600, metrics={'download': 100, 'upload': 20})
                writer.store('one', [], now=601, metrics={'download': 2, 'upload': 1})
                writer.store('two', [], now=602, metrics={'download': 400, 'upload': 200})
                writer.store('two', [], now=630, metrics={'download': 800, 'upload': 400})
            value = history.metrics_summary(path, now=630)
            self.assertEqual(value['coverage_seconds'], 0)
            self.assertEqual(value['download_bytes'], 0)
            point = value['points'][0]
            for key in ('download_rate', 'cpu_percent', 'memory_bytes', 'active_connections', 'latency_ms', 'failure_percent'):
                self.assertIsNone(point[key])
            self.assertEqual(point['gaps'], 3)

    def test_metric_retention_is_independent_of_connection_capacity(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(history, 'MAX_ROWS', 1):
            path = Path(temporary) / 'events.sqlite3'
            with history.HistoryWriter(path) as writer:
                for now in (600, 901):
                    writer.store('boot', [('sniffbox_lifecycle', [{'id': str(now)}])], now=now,
                                 metrics={'download': now, 'upload': 1})
                self.assertEqual(history.metrics_summary(path, now=901)['connection_rows'], 1)
                self.assertEqual(len(history.metrics_summary(path, now=901)['points']), 2)
                writer.store('boot', [], retention_days=1, now=100000, metrics={})
            self.assertEqual(len(history.metrics_summary(path, now=100000)['points']), 1)

    def test_readonly_paths_escape_uri_characters(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / '记录 #100%.sqlite3'
            history.store('boot', 'mihomo_snapshot', [{'id': 'one'}], path, now=100)
            self.assertEqual(len(history.query(path=path)['items']), 1)
            with history.connect(path, True) as db:
                with self.assertRaises(history.sqlite3.OperationalError):
                    db.execute('DELETE FROM observations')

    def test_upserts_survive_restart_and_filter_without_conflating_boots(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.sqlite3'
            history.store('boot1', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.com', 'sourceIP': '192.0.2.1'}, 'download': 5}], path, now=100)
            history.store('boot1', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.com', 'sourceIP': '192.0.2.1'}, 'download': 10}], path, now=101)
            history.store('boot2', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.org'}}], path, now=102)
            result = history.query(domain='example.com', path=path)
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['items'][0]['record']['download'], 10)
            self.assertEqual(result['items'][0]['first_seen_at'], 100)
            self.assertFalse(result['complete_history'])
            self.assertEqual(len(history.query(path=path)['items']), 2)
            history.store('boot3', 'sniffbox_lifecycle', [], path, retention_days=1, now=100000)
            self.assertEqual(history.query(path=path)['items'], [])

    def test_shared_writer_keeps_sources_and_times_and_uses_one_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.sqlite3'
            with history.HistoryWriter(path) as writer:
                sql = []
                writer.db.set_trace_callback(sql.append)
                writer.store('boot1', [('sniffbox_lifecycle', [{'id': 'same', 'download': 5}]), ('mihomo_snapshot', [{'id': 'same', 'download': 10}])], now=100)
                self.assertEqual(sum(row == 'COMMIT' for row in sql), 1)
                self.assertFalse(any('CREATE ' in row or 'OFFSET ' in row for row in sql))
                writer.store('boot1', [('mihomo_snapshot', [{'id': 'same', 'download': 20}])], now=101)
                rows = history.query(path=path)['items']
                self.assertEqual(len(rows), 2)
                item = next(row for row in rows if row['source'] == 'mihomo_snapshot')
                self.assertEqual((item['first_seen_at'], item['last_seen_at'], item['record']['download']), (100, 101, 20))
            with history.connect(path, True) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

    def test_capacity_expiry_and_preparation_failure_remain_atomic(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(history, 'MAX_ROWS', 3):
            path = Path(temporary) / 'events.sqlite3'
            with history.HistoryWriter(path) as writer:
                writer.store('boot', [('mihomo_snapshot', [{'id': str(i)} for i in range(3)])], now=100)
                writer.store('boot', [('mihomo_snapshot', [{'id': 'new'}])], now=101)
                self.assertEqual(len(history.query(path=path)['items']), 3)
                self.assertIn('new', [row['id'] for row in history.query(path=path)['items']])
                before = history.query(path=path)
                with self.assertRaises(TypeError):
                    writer.store('boot', [('sniffbox_lifecycle', [{'id': 'good'}]), ('mihomo_snapshot', [{'id': 'bad', 'invalid': object()}])], now=102)
                self.assertEqual(history.query(path=path), before)
                writer.store('boot', [('mihomo_snapshot', [])], retention_days=1, now=100000)
                self.assertEqual(history.query(path=path)['items'], [])
