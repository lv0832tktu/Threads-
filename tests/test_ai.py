import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from threads_publisher.ai import generate_drafts
from threads_publisher.core import SafeError
from threads_publisher.editor import edit_draft


class FakeWriter:
    def __init__(self):
        self.calls = 0
    def generate(self, context):
        self.calls += 1
        return {'hook': '今日の小さな発見', 'body': '机の上を整えると作業を始めやすく感じました。', 'closing': '皆さんはどうしていますか？'}


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = json.loads(Path('config/automation.json').read_text())
        config['ai'].update(enabled=True, theme='生活', audience='一般', daily_drafts=1)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(config))
        self.posts = self.root / 'posts.json'
        self.posts.write_text('{"posts": []}')
        self.usage = self.root / 'usage.sqlite3'
        self.now = datetime(2026, 10, 8, 8, tzinfo=ZoneInfo('Asia/Tokyo'))
        self.writer = FakeWriter()
    def tearDown(self):
        self.temp.cleanup()
    def run_generation(self, **kwargs):
        return generate_drafts(self.config, self.posts, self.usage, writer=self.writer, now=self.now, **kwargs)
    def test_draft_unapproved_and_daily_cap_persists(self):
        self.assertEqual(self.run_generation()['generated'], 1)
        post = json.loads(self.posts.read_text())['posts'][0]
        self.assertIs(post['approved'], False)
        self.assertEqual(post['research']['verification'], 'human_review_required')
        self.assertEqual(self.run_generation()['generated'], 0)
        self.assertEqual(self.writer.calls, 1)
    def test_reservation_persist_failure_stops_paid_request(self):
        def fail(): raise SafeError('storage failed')
        with self.assertRaises(SafeError): self.run_generation(persist=fail)
        self.assertEqual(self.writer.calls, 0)
        self.assertEqual(self.run_generation()['generated'], 0)
    def test_disabled_requires_no_key(self):
        config = json.loads(self.config.read_text()); config['ai']['enabled'] = False
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.run_generation()['status'], 'disabled')
        self.assertEqual(self.writer.calls, 0)
    def test_duplicates_rejected(self):
        candidate = self.writer.generate({})
        existing = [{'text': edit_draft(candidate, [])}]
        with self.assertRaises(SafeError): edit_draft(candidate, existing)
    def test_missing_theme_rejected_before_api(self):
        config = json.loads(self.config.read_text()); config['ai']['theme'] = ''
        self.config.write_text(json.dumps(config))
        with self.assertRaises(SafeError): self.run_generation()
        self.assertEqual(self.writer.calls, 0)
    def test_monthly_cap_counts_failed_and_reserved_requests(self):
        import sqlite3
        self.run_generation()
        config = json.loads(self.config.read_text()); config['ai']['monthly_request_limit'] = 1
        self.config.write_text(json.dumps(config))
        self.now = datetime(2026, 10, 9, 8, tzinfo=ZoneInfo('Asia/Tokyo'))
        self.assertEqual(self.run_generation()['generated'], 0)
        self.assertEqual(self.writer.calls, 1)
    def test_provider_error_sanitized(self):
        from threads_publisher.writer import Writer
        config = json.loads(self.config.read_text())['ai']
        def fail(request, timeout):
            raise OSError('sensitive-secret-value')
        writer = Writer(config, 'sensitive-secret-value', transport=fail)
        with self.assertRaises(SafeError) as caught:
            writer.generate({})
        self.assertNotIn('sensitive-secret-value', str(caught.exception))
    def test_future_slot_and_timezone_metadata(self):
        self.run_generation(improvement={'experiment_id': 'hook-v2'})
        post = json.loads(self.posts.read_text())['posts'][0]
        self.assertEqual(post['scheduled_at'], '2026-10-08T19:00:00+09:00')
        self.assertEqual(post['account'], 'default')
        self.assertEqual(post['topic'], '生活')
        self.assertEqual(post['variant'], 'hook-v2')
        self.assertIs(post['approved'], False)
        self.assertIs(json.loads(self.config.read_text())['auto_publish_enabled'], False)
    def test_occupied_slots_roll_over(self):
        self.posts.write_text(json.dumps({'posts': [
            {'id':'occupied-1', 'text':'予約内容A', 'scheduled_at':'2026-10-08T19:00:00+09:00'},
            {'id':'occupied-2', 'text':'予約内容B', 'scheduled_at':'2026-10-08T22:00:00+09:00'}]}))
        self.run_generation()
        post = json.loads(self.posts.read_text())['posts'][-1]
        self.assertEqual(post['scheduled_at'], '2026-10-09T08:00:00+09:00')
    def test_input_budget_blocks_network(self):
        from threads_publisher.writer import Writer
        calls = []
        def transport(*args, **kwargs): calls.append(1)
        writer = Writer(json.loads(self.config.read_text())['ai'], 'secret', transport=transport)
        with self.assertRaises(SafeError): writer.generate({'oversized': 'x' * 20001})
        self.assertEqual(calls, [])
    def test_large_archive_is_reduced_to_guidance(self):
        from unittest.mock import Mock
        writer = Mock(wraps=self.writer)
        result = generate_drafts(self.config, self.posts, self.usage, writer=writer, now=self.now,
                                 improvement={'experiment_id':'experiment-one', 'analysis':{'archive':'x'*100000},
                                              'suggestions':['x'*1000]*20})
        self.assertEqual(result['generated'],1)
        context=writer.generate.call_args.args[0]
        self.assertNotIn('analysis', context['improvement'])
        self.assertLess(len(json.dumps(context['improvement'])),5000)
