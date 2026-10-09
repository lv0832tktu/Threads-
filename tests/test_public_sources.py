import csv
import io
import json
import os
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from threads_publisher.core import SafeError,History
from threads_publisher.public_sources import import_competitors,collect_trends,parse_feed,fetch_feed
from threads_publisher.career_reports import generate_report
from threads_publisher.__main__ import main
from cryptography.fernet import Fernet
from contextlib import redirect_stdout
import base64

NOW=datetime(2026,10,8,tzinfo=timezone.utc)
RSS=b'<rss><channel><item><title>ChatGPT work</title><link>https://official.example/article</link><pubDate>Thu, 08 Oct 2026 00:00:00 +0000</pubDate></item></channel></rss>'
class Response(io.BytesIO):
    status=200
    headers={}

class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        old=Path.cwd();self.addCleanup(os.chdir,old);os.chdir(self.temp.name)
        self.config={'enabled':True,'daily_request_limit':1,'allowed_hosts':['official.example'],'sources':[{'url':'https://official.example/feed','kind':'rss','permission_confirmed':True,'official_source':True,'permission_reference':'https://official.example/terms'}]}
        self.path=Path('config.json');self.path.write_text(json.dumps(self.config))
        self.resolver=lambda *a,**k:[(None,None,None,None,('8.8.8.8',443))]
    def collect(self,**kw):
        return collect_trends(self.path,now=NOW,resolver=self.resolver,**kw)
    def test_url_only_never_fetches_and_unknown_is_not_zero(self):
        Path('urls.txt').write_text('https://www.threads.net/@public/post/abc\n')
        with patch('socket.getaddrinfo') as network:
            result=import_competitors('urls.txt',now=NOW)
        network.assert_not_called();self.assertEqual(result['network_requests'],0)
        row=json.loads(Path('private/manual-public-observations.json').read_text())['observations'][0]
        self.assertIsNone(row['metrics']['likes']);self.assertIsNone(row['characters'])
        self.assertEqual(import_competitors('urls.txt',now=NOW)['imported'],0)
    def test_csv_whitelist_and_atomic_invalid_batch(self):
        Path('rows.csv').write_text('url,theme,likes,characters,text\nhttps://threads.com/@a/post/abc,ChatGPT,0,120,PRIVATE BODY\n')
        import_competitors('rows.csv',now=NOW)
        target=Path('private/manual-public-observations.json');before=target.read_text()
        self.assertNotIn('PRIVATE BODY',before)
        self.assertEqual(json.loads(before)['observations'][0]['metrics']['likes'],0)
        Path('bad.csv').write_text('url,likes\nhttps://threads.com/@a/post/def,-1\n')
        with self.assertRaises(SafeError):import_competitors('bad.csv',now=NOW)
        self.assertEqual(target.read_text(),before)
    def test_disabled_and_unconfirmed_do_not_request(self):
        transport=Mock();self.config['enabled']=False;self.path.write_text(json.dumps(self.config))
        self.assertTrue(self.collect(transport=transport)['disabled']);transport.assert_not_called()
        self.config['enabled']=True;self.config['sources'][0]['permission_confirmed']=False;self.path.write_text(json.dumps(self.config))
        with self.assertRaises(SafeError):self.collect(transport=transport)
        transport.assert_not_called()
    def test_durable_budget_before_request_and_no_repeat(self):
        events=[]
        def persist():events.append('persist')
        def transport(*a,**k):
            self.assertEqual(events,['persist']);events.append('get');return Response(RSS)
        self.assertEqual(self.collect(persist=persist,transport=transport)['requests'],1)
        transport2=Mock();self.assertEqual(self.collect(transport=transport2)['requests'],0);transport2.assert_not_called()
        self.assertEqual(len(json.loads(Path('private/trend-data.json').read_text())['items']),1)
    def test_persist_failure_stops_network(self):
        transport=Mock()
        with self.assertRaises(SafeError):self.collect(transport=transport,persist=Mock(side_effect=SafeError('persist failed')))
        transport.assert_not_called()
    def test_failure_consumes_budget_and_safe_counts(self):
        transport=Mock(side_effect=OSError('SECRET-TOKEN'))
        result=self.collect(transport=transport)
        self.assertEqual(result['errors'],1);self.assertNotIn('SECRET-TOKEN',json.dumps(result))
        self.assertEqual(self.collect(transport=transport)['requests'],0)
    def test_ssrf_redirect_and_oversize_fail_closed(self):
        with self.assertRaises(SafeError):fetch_feed('https://official.example/feed',['official.example'],Mock(),lambda *a,**k:[(None,None,None,None,('127.0.0.1',443))])
        redirected=Response(RSS);redirected.status=302
        with self.assertRaises(SafeError):fetch_feed('https://official.example/feed',['official.example'],lambda *a,**k:redirected,self.resolver)
        with self.assertRaises(SafeError):fetch_feed('https://official.example/feed',['official.example'],lambda *a,**k:Response(b'a'*(1024*1024+1)),self.resolver)
    def test_feed_formats_and_entities_html_rejected(self):
        self.assertEqual(len(parse_feed(RSS,'rss')),1)
        self.assertEqual(len(parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI</title><link href="https://official.example/x"/></entry></feed>','atom')),1)
        self.assertEqual(len(parse_feed(b'{"items":[{"title":"AI","url":"https://official.example/x"}]}','json')),1)
        for data in (b'<!DOCTYPE rss [<!ENTITY x "private">]><rss/>',b'<html><body>no scraping</body></html>'):
            with self.assertRaises(SafeError):parse_feed(data,'rss')
    def test_report_csv_prompt_and_manual_and_trend_integration(self):
        self.collect(transport=lambda *a,**k:Response(RSS))
        Path('urls.txt').write_text('https://threads.com/@public/post/abc\n');import_competitors('urls.txt',now=NOW)
        history=History('private/history.sqlite3');self.addCleanup(history.db.close)
        report=generate_report(history,'private/insights.json','private/keyword-analysis.json',now=datetime(2026,10,11,12,tzinfo=timezone.utc))
        self.assertEqual(report['sections']['13_market']['permitted_trends'][0]['keyword'],'ChatGPT')
        self.assertEqual(report['sections']['13_market']['keyword_comparisons'][0]['keyword'],'未分類')
        self.assertIsNone(report['sections']['13_market']['keyword_comparisons'][0]['metrics']['likes']['market_mean'])
        self.assertTrue(Path('reports/latest.csv').exists());self.assertTrue(Path('reports/latest-prompt.md').exists())
        self.assertIn('21本',Path('reports/latest-prompt.md').read_text())

    def test_private_cli_import_restores_and_preserves_prior_observations(self):
        Path('private').mkdir()
        key=Fernet.generate_key().decode()
        with patch.dict(os.environ,{'THREADS_STATE_KEY':key}),patch('threads_publisher.__main__.Client') as client,redirect_stdout(io.StringIO()):
            for ident in ('abc','def'):
                Path('private/urls.txt').write_text('https://threads.com/@public/post/'+ident+'\n')
                with patch('sys.argv',['publisher','import-competitors','--input','private/urls.txt','--private-state']):
                    self.assertEqual(main(),0)
        client.assert_not_called()
        encrypted=Path('state/private-state.enc').read_bytes()
        self.assertNotIn(b'@public',encrypted)
        bundle=json.loads(Fernet(key.encode()).decrypt(encrypted))
        rows=json.loads(base64.b64decode(bundle['files']['manual-public-observations.json']))['observations']
        self.assertEqual(len(rows),2)

    def test_disabled_trends_cli_requires_no_secret_or_network(self):
        self.config['enabled']=False;self.path.write_text(json.dumps(self.config))
        with patch('sys.argv',['publisher','trends','--trend-config',str(self.path)]),patch('threads_publisher.public_sources.fetch_feed') as fetch,redirect_stdout(io.StringIO()):
            self.assertEqual(main(),0)
        fetch.assert_not_called()
