import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from threads_publisher.insights import InsightsClient, collect, read_snapshots
from threads_publisher.analyst import analyze, write_improvement
from threads_publisher.core import SafeError


class InsightsTests(unittest.TestCase):
    def test_supported_and_missing_metrics(self):
        def transport(request, timeout):
            metric = request.full_url.split('=')[-1]
            if metric == 'quotes':
                raise urllib.error.HTTPError(request.full_url, 403, 'secret', {}, io.BytesIO(b'{}'))
            return io.BytesIO(json.dumps({'data': [{'name': metric, 'values': [{'value': 10}]}]}).encode())
        result = InsightsClient('secret', transport).fetch('123')
        self.assertEqual(result['metrics']['views'], 10)
        self.assertNotIn('quotes', result['metrics'])
        self.assertIn('quotes', result['unavailable'])

    def test_expired_and_rate_limited_are_safe(self):
        for code, api_code in ((401, 190), (429, 4)):
            def transport(request, timeout):
                raise urllib.error.HTTPError(request.full_url, code, 'TOKEN', {}, io.BytesIO(json.dumps({'error': {'code': api_code, 'message': 'TOKEN'}}).encode()))
            with self.assertRaises(SafeError) as caught:
                InsightsClient('TOKEN', transport).fetch('123')
            self.assertNotIn('TOKEN', str(caught.exception))

    def test_checkpoints_deduplicated_and_legacy_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            db = sqlite3.connect(':memory:')
            db.execute('CREATE TABLE posts(account,post_id,status,remote_id,published_at,text,topic,variant,scheduled_at)')
            db.execute("INSERT INTO posts VALUES('a','p','published','r','2026-01-01T00:00:00+00:00','hello','topic','baseline',NULL)")
            db.execute("INSERT INTO posts VALUES('a','old','published','old',NULL,'','','',NULL)")
            path = Path(folder)/'insights.json'
            client = SimpleNamespace(fetch=lambda media: {'metrics': {'views': 100, 'likes': 10, 'replies': 0, 'reposts': 0, 'quotes': 0}, 'unavailable': {}})
            now = datetime(2026,1,5,tzinfo=timezone.utc)
            result = collect(SimpleNamespace(db=db), client, path, now)
            self.assertEqual(result['collected'], 2)
            self.assertEqual(result['skipped'], 1)
            self.assertEqual(collect(SimpleNamespace(db=db),client,path,now)['collected'],0)
            self.assertTrue(read_snapshots(path)['snapshots'][0]['late'])
            report = analyze(path)
            self.assertEqual(report['posts'][0]['reaction_rate'], .1)
            self.assertTrue(all(c['evidence']=='insufficient' for c in report['comparisons']))
            improvement = write_improvement(path,Path(folder)/'improvement.json')
            self.assertTrue(improvement['advisory_only'])
            self.assertTrue(any('insufficient' in s for s in improvement['suggestions']))

    def test_account_filter_and_data_derived_improvement(self):
        with tempfile.TemporaryDirectory() as folder:
            db = sqlite3.connect(':memory:')
            db.execute('CREATE TABLE posts(account,post_id,status,remote_id,published_at)')
            db.execute("INSERT INTO posts VALUES('other','p','published','r','2026-01-01T00:00:00+00:00')")
            def forbidden(media):
                self.fail('Other account must not be queried')
            collect(SimpleNamespace(db=db), SimpleNamespace(fetch=forbidden), Path(folder)/'empty.json', datetime(2026,1,5,tzinfo=timezone.utc), account='mine')
            path = Path(folder)/'insights.json'
            snapshots = []
            for i in range(6):
                snapshots.append({'account':'mine', 'post_id':str(i), 'checkpoint':'24h', 'published_at':'2026-01-01T00:00:00+00:00', 'text':'x'*(20+i*20), 'variant':'improved' if i>2 else 'baseline', 'metrics':{'views':100,'likes':i,'replies':0,'reposts':0,'quotes':0}})
            path.write_text(json.dumps({'snapshots':snapshots}))
            report = analyze(path, min_samples=3)
            self.assertEqual(len(report['high_low']),1)
            variants = [c for c in report['comparisons'] if c['dimension']=='variant']
            self.assertEqual(len(variants),2)
            self.assertTrue(all(c['evidence']=='observational_only' for c in variants))
            report2 = write_improvement(path, Path(folder)/'advice.json')
            self.assertTrue(any('association' in s for s in report2['suggestions']))

    def test_incomplete_metrics_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'insights.json'
            path.write_text(json.dumps({'snapshots':[{'post_id':'p','account':'a','checkpoint':'24h','published_at':'2026-01-01T00:00:00+00:00','metrics':{'views':10,'likes':2}}]}))
            row = analyze(path)['posts'][0]
            self.assertIsNone(row['reaction_rate'])
            self.assertEqual(row['known_reaction_rate_lower_bound'],.2)

if __name__ == '__main__':
    unittest.main()

class LegacyInsightsTests(unittest.TestCase):
    def test_metadata_backfill_uses_api_timestamp_and_text(self):
        from threads_publisher.core import History
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            history=History(Path(folder)/'history.sqlite3')
            self.addCleanup(history.db.close)
            history.db.execute("INSERT INTO posts(account,post_id,status,remote_id) VALUES ('mine','old','published','remote')")
            history.db.commit()
            client=Mock()
            client.metadata.return_value={'published_at':'2026-10-01T08:00:00+09:00','text':'existing published text'}
            client.fetch.return_value={'metrics':{'views':10},'unavailable':{}}
            result=collect(history,client,Path(folder)/'insights.json',datetime(2026,10,8,tzinfo=timezone.utc),account='mine',backfill=True)
            self.assertEqual(result['collected'],3)
            self.assertEqual(history.db.execute('SELECT published_at,text FROM posts').fetchone(),('2026-10-01T08:00:00+09:00','existing published text'))
            self.assertEqual(history.db.execute('SELECT status FROM posts').fetchone()[0],'published')

    def test_metadata_parser_and_configured_metric_names(self):
        def transport(request,timeout):
            return io.BytesIO(b'{"id":"r","timestamp":"2026-10-01T00:00:00+0000","text":"hello"}')
        self.assertEqual(InsightsClient('secret',transport).metadata('r')['text'],'hello')
        with self.assertRaises(SafeError):
            InsightsClient('secret',metrics=['views&access_token=bad'])
