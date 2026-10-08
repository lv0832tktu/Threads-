import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from threads_publisher.management import (ManagementError, read_history,
                                         require_loopback, set_approval,
                                         set_auto_publish, set_schedule)


class ManagementTests(unittest.TestCase):
    def test_approval_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'posts.json'
            document = {'metadata': {'version': 1}, 'posts': [
                {'id': 'one', 'text': '本文', 'approved': False, 'account_id': 'a'},
                {'id': 'two', 'text': 'unchanged', 'approved': False}]}
            path.write_text(json.dumps(document), encoding='utf-8')
            set_approval(path, 'one', True)
            document['posts'][0]['approved'] = True
            self.assertEqual(json.loads(path.read_text()), document)
            set_approval(path, 'one', False)
            self.assertFalse(json.loads(path.read_text())['posts'][0]['approved'])

    def test_invalid_mutation_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'posts.json'
            path.write_text('{"posts": [{"id": "one", "approved": false}]}')
            original = path.read_bytes()
            with self.assertRaises(ManagementError):
                set_approval(path, 'missing', True)
            with self.assertRaises(ManagementError):
                set_approval(path, 'one', 'true')
            self.assertEqual(path.read_bytes(), original)

    def test_duplicate_id_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'posts.json'
            path.write_text('{"posts": [{"id": "one"}, {"id": "one"}]}')
            original = path.read_bytes()
            with self.assertRaises(ManagementError):
                set_approval(path, 'one', True)
            self.assertEqual(path.read_bytes(), original)

    def test_switch_preserves_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"timezone": "Asia/Tokyo", "auto_publish_enabled": false}')
            set_auto_publish(path, True)
            self.assertEqual(json.loads(path.read_text()), {'timezone': 'Asia/Tokyo', 'auto_publish_enabled': True})

    def test_public_binding_rejected(self):
        for address in (None, '', '0.0.0.0', '::', 'example.com'):
            with self.assertRaises(ManagementError):
                require_loopback(address)
        for address in ('127.0.0.1', '::1', 'localhost'):
            require_loopback(address)

    def test_history_read_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.sqlite3'
            self.assertEqual(read_history(path), [])
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE posts (post_id TEXT, status TEXT)')
                db.execute('INSERT INTO posts VALUES (?, ?)', ('one', 'published'))
            original = path.read_bytes()
            self.assertEqual(read_history(path), [{'post_id': 'one', 'status': 'published'}])
            self.assertEqual(path.read_bytes(), original)

    def test_schedule_normalizes_timezone_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'posts.json'
            document = {'meta': 'keep', 'posts': [{'id': 'one', 'text': 'keep', 'approved': False, 'account': 'other'}]}
            path.write_text(json.dumps(document))
            set_schedule(path, 'one', '2026-10-09T00:00:00+00:00')
            document['posts'][0]['scheduled_at'] = '2026-10-09T09:00:00+09:00'
            self.assertEqual(json.loads(path.read_text()), document)
            original = path.read_bytes()
            for invalid in ('2026-10-09T08:00:00', 'invalid', None):
                with self.assertRaises(ManagementError):
                    set_schedule(path, 'one', invalid)
                self.assertEqual(path.read_bytes(), original)
