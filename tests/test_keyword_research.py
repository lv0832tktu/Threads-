import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threads_publisher.core import SafeError
from threads_publisher.keyword_research import analyze_keywords, keyword_catalog, import_public_observations


class Client:
    def __init__(self, fail=False, rows=None):
        self.calls = []
        self.fail = fail
        self.rows = rows or []
    def get(self, endpoint, params):
        self.calls.append((endpoint, params))
        if self.fail:
            raise SafeError('Public endpoint unavailable')
        return {'data': self.rows}


class KeywordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads(Path('config/keywords.json').read_text())
        self.config['enabled'] = True
        self.now = datetime(2026, 10, 9, 1, tzinfo=timezone.utc)
    def run_sample(self, client, persist=lambda: None, now=None):
        path = self.root / 'config.json'
        path.write_text(json.dumps(self.config))
        return analyze_keywords(client, path, self.root / 'history.json', self.root / 'output.json',
                                now=now or self.now, persist=persist)
    def test_catalog_preserved_and_new_groups_exact_counts(self):
        old = json.loads(Path('config/search-keywords.json').read_text())
        self.assertEqual(self.config['groups'][:14], old['groups'])
        self.assertEqual([len(g['keywords']) for g in self.config['groups'][14:]], [15, 13, 15])
        career = next(row for row in keyword_catalog(self.config) if row['keyword'] == '転職')
        self.assertEqual(len(career['categories']), 2)
    def test_disabled_no_api(self):
        self.config['enabled'] = False
        client = Client()
        self.assertTrue(self.run_sample(client)['disabled'])
        self.assertEqual(client.calls, [])
    def test_daily_durable_budget_rotation_zero_success(self):
        client = Client()
        first = self.run_sample(client)
        self.assertEqual((first['requests'], first['successful_queries'], first['posts']), (3, 3, 0))
        self.assertEqual(self.run_sample(client)['requests'], 0)
        self.assertEqual(self.run_sample(client, now=self.now + timedelta(days=1))['requests'], 3)
        self.assertNotEqual(client.calls[0][1]['q'], client.calls[3][1]['q'])
    def test_failed_allowance_consumed_and_distinct(self):
        result = self.run_sample(Client(fail=True))
        self.assertEqual(result['successful_queries'], 0)
        self.assertEqual(result['errors'], 3)
        self.assertEqual(self.run_sample(Client())['requests'], 0)
    def test_persist_failure_before_network(self):
        client = Client()
        def fail():
            raise SafeError('Persistence failed')
        with self.assertRaises(SafeError):
            self.run_sample(client, persist=fail)
        self.assertEqual(client.calls, [])
    def test_weekly_ceiling(self):
        self.config['weekly_request_limit'] = 4
        client = Client()
        self.assertEqual(self.run_sample(client)['requests'], 3)
        self.assertEqual(self.run_sample(client, now=self.now + timedelta(days=1))['requests'], 1)
    def test_no_body_or_username_retained_unknown_metrics(self):
        row = {'id': '1', 'text': '秘密の本文', 'username': 'person', 'permalink': 'https://www.threads.net/@person/post/ABC'}
        result = self.run_sample(Client(rows=[row]))
        self.assertEqual(result['posts'], 3)
        output = json.loads((self.root / 'output.json').read_text())
        post = output['runs'][0]['posts'][0]
        self.assertNotIn('text', post)
        self.assertNotIn('username', post)
        self.assertIsNone(post['metrics']['likes'])
    def test_manual_public_whitelist_and_append(self):
        source, target = self.root / 'source.json', self.root / 'manual.json'
        source.write_text(json.dumps({'observations': [{'url': 'https://threads.net/@person/post/ABC',
            'observed_at': self.now.isoformat(), 'characters': 40, 'hook': '質問型', 'theme': '就活',
            'text': 'コピーしない', 'username': 'person', 'metrics': {'views': 123, 'likes': 3}}]}))
        self.assertEqual(import_public_observations(source, target)['imported'], 1)
        self.assertEqual(import_public_observations(source, target)['imported'], 0)
        post = json.loads(target.read_text())['observations'][0]
        self.assertNotIn('text', post)
        self.assertNotIn('views', post['metrics'])

    def test_pagination_bounded_and_never_follow_next_url(self):
        class Paged(Client):
            def get(self, endpoint, params):
                self.calls.append((endpoint, params))
                return {'data': [], 'paging': {'next': 'https://evil.invalid/?token=secret',
                                              'cursors': {'after': str(len(self.calls))}}}
        client = Paged()
        result = self.run_sample(client)
        self.assertEqual(result['requests'], 3)
        self.assertEqual(client.calls[1][1]['after'], '1')
        self.assertNotEqual(client.calls[0][1]['q'], client.calls[2][1]['q'])
        self.assertTrue(all(endpoint == 'keyword_search' for endpoint, _ in client.calls))
