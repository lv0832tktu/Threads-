import copy
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from threads_publisher.market import MarketClient,collect_market,market_report,public_url,load_market
from threads_publisher.core import SafeError


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=json.loads(Path('config/market.json').read_text())
        self.config.update(enabled=True,competitors=['public_account'],keywords=['作業'],themes=[{'name':'作業の工夫','terms':['作業']}])
        self.now=datetime(2026,10,8,tzinfo=timezone.utc)
        self.row={'id':'p','text':'作業を始める3つの工夫\nCOPIED-COMPETITOR-TEXT','timestamp':'2026-10-07T00:00:00+00:00','permalink':'https://www.threads.net/@public_account/post/ABC?utm=x','likes_count':0}
        self.path=self.root/'market.json'

    def transport(self,request,timeout):
        self.assertNotIn('TOKEN',request.full_url)
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.get_header('Authorization'),'Bearer TOKEN')
        if '/profile_lookup?' in request.full_url:
            return io.BytesIO(b'{"likes_count":50,"views_count":1000}')
        return io.BytesIO(json.dumps({'data':[self.row]}).encode())

    def test_public_collection_dedup_features_only_unknown_counts(self):
        result=collect_market(MarketClient('TOKEN',transport=self.transport),self.config,self.path,self.now)
        self.assertEqual(result['posts'],1);self.assertEqual(result['requests'],3)
        data=json.loads(self.path.read_text())['runs'][0]
        self.assertNotIn('COPIED-COMPETITOR-TEXT',self.path.read_text())
        self.assertEqual(len(data['posts'][0]['sources']),2)
        self.assertEqual(data['posts'][0]['metrics']['likes'],0)
        self.assertIsNone(data['posts'][0]['metrics']['replies'])
        self.assertEqual(data['profiles'][0]['scope'],'profile_total_at_collection')
        self.assertEqual(data['posts'][0]['url'],'https://www.threads.net/@public_account/post/ABC')

    def test_weekly_idempotence_and_disabled_no_requests(self):
        client=MarketClient('TOKEN',transport=self.transport)
        collect_market(client,self.config,self.path,self.now)
        result=collect_market(client,self.config,self.path,self.now)
        self.assertTrue(result['skipped']);self.assertEqual(client.calls,3)
        disabled=dict(self.config,enabled=False)
        self.assertTrue(collect_market(client,disabled,self.path,self.now)['disabled'])
        self.assertEqual(client.calls,3)

    def test_partial_denial_retains_other_results_no_raw_error(self):
        def transport(request,timeout):
            if '/keyword_search?' in request.full_url:
                raise urllib.error.HTTPError(request.full_url,403,'SECRET',{},io.BytesIO(b'{"error":{"message":"SECRET"}}'))
            return self.transport(request,timeout)
        result=collect_market(MarketClient('TOKEN',transport=transport),self.config,self.path,self.now)
        self.assertEqual(result['errors'],1);self.assertEqual(result['posts'],1)
        self.assertNotIn('SECRET',self.path.read_text())
        self.assertFalse(json.loads(self.path.read_text())['runs'][0]['complete'])

    def test_pagination_never_follows_next_url_budget(self):
        urls=[]
        def transport(request,timeout):
            urls.append(request.full_url)
            return io.BytesIO(json.dumps({'data':[self.row],'paging':{'next':'https://evil.invalid/?access_token=TOKEN','cursors':{'after':'safe-cursor'}}}).encode())
        config=dict(self.config,max_pages=5)
        client=MarketClient('TOKEN',limit=1,transport=transport)
        with self.assertRaises(SafeError):client.posts('keyword_search',{'q':'query'},config)
        self.assertEqual(len(urls),1)
        self.assertNotIn('evil.invalid',urls[0])
        with self.assertRaises(SafeError):client.get('p/insights',{})

    def test_no_scraping_bad_urls_and_old_posts_dropped(self):
        for url in ('http://threads.net/@a/post/b','https://evil.invalid/@a/post/b','https://secret@threads.net/@a/post/b','https://threads.net:bad/@a/post/b','https://threads.net/@a'):
            self.assertIsNone(public_url(url))
        self.row['timestamp']='2020-01-01T00:00:00+00:00'
        result=collect_market(MarketClient('TOKEN',transport=self.transport),self.config,self.path,self.now)
        self.assertEqual(result['posts'],0)

    def test_report_evidence_themes_and_own_comparison_no_copy(self):
        collect_market(MarketClient('TOKEN',transport=self.transport),self.config,self.path,self.now)
        own=self.root/'insights.json'
        own.write_text(json.dumps({'snapshots':[{'post_id':'mine','account':'PRIVATE-ID','checkpoint':'24h','published_at':'2026-10-07T00:00:00+00:00','text':'MY-TEXT','metrics':{'views':100,'likes':4,'replies':1,'reposts':0,'quotes':0}}]}))
        output=self.root/'report.md'
        result=market_report(self.path,own,output,self.now)
        report=output.read_text()
        self.assertEqual(result['own_groups'],1)
        self.assertIn('5.00%',report);self.assertIn('作業の工夫',report)
        self.assertIn('https://www.threads.net/@public_account/post/ABC',report)
        self.assertIn('取得不可',report);self.assertIn('推奨構成',report)
        self.assertIn('21本',report);self.assertNotIn('COPIED-COMPETITOR-TEXT',report)
        self.assertNotIn('MY-TEXT',report);self.assertNotIn('PRIVATE-ID',report)
        self.assertIn('プロフィール全体',report)

    def test_configuration_and_http_expiry_safe(self):
        path=self.root/'config.json'
        path.write_text(json.dumps(dict(self.config,competitors=['https://example.com'])))
        with self.assertRaises(SafeError):load_market(path)
        def fail(request,timeout):raise urllib.error.HTTPError(request.full_url,400,'TOKEN',{},io.BytesIO(b'{"error":{"code":190}}'))
        with self.assertRaises(SafeError) as caught:MarketClient('TOKEN',transport=fail).get('profile_posts',{})
        self.assertNotIn('TOKEN',str(caught.exception))

    def test_market_command_initially_disabled_without_token_or_key(self):
        from threads_publisher.__main__ import main
        with patch('sys.argv',['publisher','market']),patch('threads_publisher.market.MarketClient') as client:
            self.assertEqual(main(),0);client.assert_not_called()
