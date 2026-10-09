import json
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from threads_publisher.career_reports import closed_week, generate_report
from threads_publisher.insights import collect_latest, collect, InsightsClient
from threads_publisher.core import History
from threads_publisher.core import SafeError

class CareerReportTests(unittest.TestCase):
    def history(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE posts(account,post_id,status,remote_id,published_at,text,scheduled_at,job_status,post_type,category,keywords)')
        db.execute('CREATE TABLE post_parts(account,post_id,part_index,status,remote_id,published_at,text)')
        return SimpleNamespace(db=db)

    def test_closed_week_boundary(self):
        _,cut=closed_week(datetime(2026,10,11,12,tzinfo=timezone.utc))
        start,_=closed_week(datetime(2026,10,11,12,tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(),'2026-10-05T00:00:00+09:00')
        self.assertEqual(cut.isoformat(),'2026-10-11T21:00:00+09:00')
        _,earlier=closed_week(datetime(2026,10,11,11,59,tzinfo=timezone.utc))
        self.assertEqual(earlier.day,4)
        _,monday=closed_week(datetime(2026,10,11,15,tzinfo=timezone.utc))
        self.assertEqual(monday,cut)

    def test_retry_repairs_outputs_without_regenerating_week(self):
        with tempfile.TemporaryDirectory() as folder:
            history=self.history()
            output=Path(folder)/'reports'
            first=generate_report(history,Path(folder)/'missing.json',None,output,now=datetime(2026,10,11,12,tzinfo=timezone.utc))
            (output/'latest.md').unlink()
            (output/'weekly/2026-10-11.md').unlink()
            second=generate_report(history,Path(folder)/'missing.json',None,output,now=datetime(2026,10,12,12,tzinfo=timezone.utc))
            self.assertEqual(first,second)
            self.assertTrue((output/'latest.md').exists())
            self.assertTrue((output/'weekly/2026-10-11.md').exists())

    def test_safe_latest_metrics_and_cutoff(self):
        with tempfile.TemporaryDirectory() as folder:
            h=self.history()
            h.db.execute("INSERT INTO posts VALUES('private-account','private-id','published','r','2026-10-11T11:59:59+00:00','SECRET TEXT','2026-10-11T20:00:00+09:00','published','thread','career','[\"転職\"]')")
            h.db.execute("INSERT INTO posts VALUES('private-account','exclude','published','x','2026-10-11T12:00:00+00:00','EXCLUDE',NULL,'published','text','','[]')")
            path=Path(folder)/'insights.json'
            snapshots=[]
            for checkpoint,views,time in [('24h',10,'2026-10-11T12:00:00+00:00'),('daily:date',100,'2026-10-11T14:00:00+00:00')]:
                snapshots.append({'account':'private-account','post_id':'private-id','remote_id':'r','checkpoint':checkpoint,'collected_at':time,'metrics':{'views':views,'likes':10,'replies':0,'reposts':0,'quotes':0,'shares':50}})
            path.write_text(json.dumps({'snapshots':snapshots}))
            market=Path(folder)/'market.json'
            market.write_text(json.dumps({'runs':[{'posts':[{'keyword':'転職','url':'https://www.threads.net/@example/post/abc','observed_at':'2026-10-11T11:59:00+00:00','characters':50,'hook':'質問型','metrics':{'likes':8}}]}]}))
            report=generate_report(h,path,market,Path(folder)/'reports',now=datetime(2026,10,11,15,tzinfo=timezone.utc))
            self.assertEqual(report['sections']['1_summary']['published'],1)
            self.assertEqual(report['sections']['3_metrics']['views']['sum'],100)
            self.assertEqual(report['posts'][0]['reaction_rate'],.1)
            self.assertTrue(report['posts'][0]['post_cutoff_measurement'])
            contents=(Path(folder)/'reports/latest.json').read_text()
            for secret in ('private-account','private-id','SECRET TEXT','EXCLUDE'):
                self.assertNotIn(secret,contents)
            self.assertEqual(len(report['sections']),15)
            comparison=report['sections']['13_market']['keyword_comparisons'][0]
            self.assertEqual(comparison['metrics']['likes']['market_mean'],8)
            self.assertIsNone(comparison['metrics']['views']['market_mean'])
            self.assertEqual(comparison['own_posts'],1)
            self.assertEqual(report['posts'][0]['post_type'],'thread')
            self.assertEqual(report['posts'][0]['characters'],len('SECRET TEXT'))
            self.assertEqual(report['posts'][0]['theme'],'転職')
            self.assertEqual(report['posts'][0]['hook_class'],'説明・体験型')
            self.assertIn('short',report['sections']['8_categories']['characters'])
            self.assertEqual(report['sections']['2_operations']['excluded_after_cutoff'],1)
            self.assertEqual(len(report['sections']['14_next_themes']),10)
            self.assertEqual(len(report['sections']['15_next_prompts']['tree']),7)

    def test_followers_unknown_and_finite(self):
        def transport(request, timeout):
            return io.BytesIO(json.dumps({'data':[{'name':'followers_count','total_value':{'value':12}}]}).encode())
        self.assertEqual(InsightsClient('secret',transport).fetch_followers('a')['followers_count'],12)
        empty=InsightsClient('secret',lambda request,timeout:io.BytesIO(b'{"data":[]}'))
        self.assertIsNone(empty.fetch_followers('a')['followers_count'])

    def test_partial_root_and_pending_stats(self):
        with tempfile.TemporaryDirectory() as folder:
            h=self.history()
            h.db.execute("INSERT INTO posts VALUES('a','p','pending',NULL,NULL,'PRIVATE','2026-10-08T20:00:00+09:00','partial_failed','thread','career','[]')")
            h.db.execute("INSERT INTO post_parts VALUES('a','p',0,'published','r','2026-10-08T11:00:00+00:00','PRIVATE')")
            path=Path(folder)/'i.json'
            path.write_text('{"snapshots":[]}')
            report=generate_report(h,path,None,Path(folder)/'reports',now=datetime(2026,10,11,15,tzinfo=timezone.utc))
            self.assertEqual(report['sections']['2_operations']['counts']['partial_failed'],1)
            self.assertEqual(report['sections']['1_summary']['published'],1)
            self.assertIsNone(report['sections']['3_metrics']['views']['sum'])
            self.assertIsNone(report['posts'][0]['reaction_rate'])

    def test_real_history_posted_and_manual_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            h=History(Path(folder)/'history.db')
            post={'id':'p','text':'SECRET','post_type':'carousel','keywords':['転職'],'category':'career'}
            h.reserve('a','p',post)
            h.finish('a','p','r',post)
            h.db.execute("UPDATE posts SET published_at='2026-10-08T00:00:00+00:00'")
            h.db.commit()
            insights=Path(folder)/'i.json'
            insights.write_text('{"snapshots":[]}')
            market=Path(folder)/'keyword-analysis.json'
            manual=Path(folder)/'manual-public-observations.json'
            manual.write_text(json.dumps({'observations':[{'theme':'転職','url':'https://www.threads.net/@example/post/abc','observed_at':'2026-10-08T00:00:00+00:00','characters':10,'hook':'質問型','provenance':'manual_public','metrics':{'likes':2}}]}))
            report=generate_report(h,insights,market,Path(folder)/'reports',now=datetime(2026,10,11,15,tzinfo=timezone.utc))
            self.assertEqual(report['sections']['2_operations']['success_rate'],1)
            self.assertEqual(report['posts'][0]['post_type'],'image')
            self.assertEqual(report['sections']['13_market']['keyword_comparisons'][0]['manual_samples'],1)
            h.db.execute("INSERT INTO post_parts(account,post_id,part_index,status,remote_id,published_at) VALUES('a','partial',2,'published','carousel','2026-10-08T00:00:00+00:00')")
            h.reserve('a','partial',{'post_type':'carousel'})
            client=SimpleNamespace(fetch=lambda remote:{'metrics':{'views':1},'unavailable':{}})
            collect_latest(h,client,insights,datetime(2026,10,11,15,tzinfo=timezone.utc))
            snapshots=json.loads(insights.read_text())['snapshots']
            self.assertEqual(next(s for s in snapshots if s['remote_id']=='carousel')['component'],'root')

    def test_parts_latest_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            h=self.history()
            h.db.execute("INSERT INTO posts VALUES('a','p','published','r','2026-10-01T00:00:00+00:00','root',NULL,'published','tree','','[]')")
            h.db.execute("INSERT INTO post_parts VALUES('a','p',0,'published','r','2026-10-01T00:00:00+00:00','root')")
            h.db.execute("INSERT INTO post_parts VALUES('a','p',1,'published','reply','2026-10-01T00:01:00+00:00','reply')")
            path=Path(folder)/'i.json'
            calls=[]
            def fetch(remote):
                calls.append(remote)
                return {'metrics':{'views':0},'unavailable':{}}
            client=SimpleNamespace(fetch=fetch)
            now=datetime(2026,10,9,tzinfo=timezone.utc)
            result=collect_latest(h,client,path,now,account='a')
            self.assertEqual(result['collected'],2)
            self.assertEqual(set(calls),{'r','reply'})
            self.assertEqual(collect_latest(h,client,path,now)['collected'],0)
            self.assertEqual(collect(h,client,path,now)['collected'],6)

    def test_latest_bounds_and_rate_limit_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            history=self.history()
            for ident,time in [('old','2026-09-01T00:00:00+00:00'),('one','2026-10-08T00:00:00+00:00'),('two','2026-10-09T00:00:00+00:00')]:
                history.db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?)',('a',ident,'published',ident,time,'private',None,'posted','text','','[]'))
            calls=[]
            client=SimpleNamespace(fetch=lambda remote: calls.append(remote) or {'metrics':{'views':0},'unavailable':{}})
            result=collect_latest(history,client,Path(folder)/'bounded.json',now=datetime(2026,10,10,tzinfo=timezone.utc),max_media=1)
            self.assertEqual(calls,['two']); self.assertTrue(result['limited'])
            calls.clear()
            def limited(remote):
                calls.append(remote)
                raise SafeError('Insights rate limit reached; retry later')
            result=collect_latest(history,SimpleNamespace(fetch=limited),Path(folder)/'limited.json',now=datetime(2026,10,10,tzinfo=timezone.utc))
            self.assertEqual(calls,['two']); self.assertEqual(len(result['errors']),1)

if __name__=='__main__':
    unittest.main()
