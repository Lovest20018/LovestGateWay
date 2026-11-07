from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import history_store as history


class HistoryTests(unittest.TestCase):
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
