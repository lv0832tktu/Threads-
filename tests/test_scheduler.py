import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from threads_publisher.core import History, SafeError
from threads_publisher.scheduler import due_posts, parse_time, run_scheduled


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'posts.json'
        self.history = History(Path(self.tmp.name) / 'history.sqlite3')
        self.addCleanup(self.history.db.close)
        self.now = datetime.now(timezone.utc)
        self.post = {'id': 'scheduled', 'text': 'scheduled test', 'approved': True, 'scheduled_at': self.now.isoformat()}
        self.path.write_text(json.dumps({'posts': [self.post]}))
        self.config = {'timezone':'Asia/Tokyo', 'auto_publish_enabled':True, 'schedule':{'max_lateness_hours':24,'max_posts_per_day':3,'max_posts_per_run':3}}
        self.client = Mock()
        self.client.connect.return_value = {'id':'account'}
        self.client.create.return_value = 'container'
        self.client.publish.return_value = 'remote'

    def test_disabled_gates_before_network(self):
        for enabled, auto, config in [(False, True, self.config), (True, False, self.config), (True, True, dict(self.config, auto_publish_enabled=False))]:
            result = run_scheduled(self.client, self.history, self.path, config, enabled, auto, self.now)
            self.assertTrue(result['disabled'])
        self.client.connect.assert_not_called()

    def test_delay_expiry_approval_and_timezone(self):
        posts = [dict(self.post, id='due', scheduled_at='2026-10-09T08:00:00+09:00'),
                 dict(self.post, id='future', scheduled_at='2026-10-09T22:00:00+09:00'),
                 dict(self.post, id='expired', scheduled_at='2026-10-07T08:00:00+09:00'),
                 dict(self.post, id='unapproved', approved=False, scheduled_at='2026-10-09T08:00:00+09:00'),
                 dict(self.post, id='manual', scheduled_at=None)]
        due = due_posts(posts, self.config, datetime.fromisoformat('2026-10-09T19:30:00+09:00'))
        self.assertEqual([p['id'] for p in due], ['due'])
        self.assertEqual(parse_time('2026-10-08T23:00:00Z').hour, 8)
        with self.assertRaises(SafeError):
            parse_time('2026-10-09T08:00:00')

    def test_publish_and_duplicate_skip(self):
        first = run_scheduled(self.client, self.history, self.path, self.config, True, True, self.now)
        second = run_scheduled(self.client, self.history, self.path, self.config, True, True, self.now)
        self.assertEqual(first['published'], 1)
        self.assertEqual(second['skipped'], 1)
        self.client.publish.assert_called_once()
        row = self.history.db.execute('SELECT text, scheduled_at, published_at FROM posts').fetchone()
        self.assertEqual(row[:2], (self.post['text'],self.post['scheduled_at']))
        self.assertTrue(row[2])

    def test_daily_limit_counts_uncertain_reservations(self):
        for i in range(3):
            self.history.reserve('account', str(i))
        result = run_scheduled(self.client, self.history, self.path, self.config, True, True, datetime.now(timezone.utc))
        self.assertEqual(result['published'],0)
        self.client.create.assert_not_called()

    def test_account_filter_and_expected_account(self):
        self.assertFalse(due_posts([dict(self.post, account='other')], self.config, self.now))
        config = dict(self.config, accounts={'default':{'expected_user_id':'different'}})
        with self.assertRaises(SafeError):
            run_scheduled(self.client, self.history, self.path, config, True, True, self.now)
        self.client.create.assert_not_called()

    def test_legacy_migration_preserves_rows(self):
        path = Path(self.tmp.name) / 'legacy.sqlite3'
        db=sqlite3.connect(path)
        db.execute('CREATE TABLE posts (account TEXT, post_id TEXT, status TEXT, remote_id TEXT, PRIMARY KEY(account,post_id))')
        db.execute("INSERT INTO posts VALUES ('a','old','published','r')")
        db.commit(); db.close()
        history=History(path)
        self.addCleanup(history.db.close)
        self.assertEqual(history.db.execute('SELECT account,post_id,status,remote_id,published_at FROM posts').fetchone(),('a','old','published','r',None))
