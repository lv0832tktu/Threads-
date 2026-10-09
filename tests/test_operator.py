import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import Mock,patch
from cryptography.fernet import Fernet
from threads_publisher.operator import Operator
from threads_publisher.core import SafeError,History
from threads_publisher.schema import approval_digest
from threads_publisher.scheduler import due_posts,run_scheduled
from threads_publisher.career_reports import generate_report

class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir()
        self.policy={'timezone':'Asia/Tokyo','slots':{'text':'08:00','image':'19:00','thread':'22:00'},'format_mode':'flexible','auto_publish_enabled':False}
        (self.root/'config/posting_schedule.json').write_text(json.dumps(self.policy))
        (self.root/'config/trend_sources.json').write_text('{"enabled":false,"sources":[],"allowed_hosts":[],"daily_request_limit":3}')
        self.operator=Operator(self.root)
        self.keypatch=patch.dict(os.environ,{'THREADS_STATE_KEY':Fernet.generate_key().decode()});self.keypatch.start();self.addCleanup(self.keypatch.stop)
    def batch(self):return json.dumps({'posts':[{'id':f'p{i}','text':f'AIの確認方法 {i}','approved':True} for i in range(21)]}).encode()
    def posts(self):return json.loads((self.root/'private/posts.json').read_text())['posts']
    def test_text_week_is_three_slots_pending_and_duplicates_atomic(self):
        self.operator.import_drafts(self.batch(),'2026-10-12',True)
        rows=self.posts()
        self.assertEqual([p['planned_at'][11:16] for p in rows[:3]],['08:00','19:00','22:00'])
        self.assertEqual(rows[-1]['planned_at'],'2026-10-18T22:00:00+09:00')
        self.assertTrue(all(not p['approved'] and 'scheduled_at' not in p for p in rows))
        before=(self.root/'private/posts.json').read_bytes()
        with self.assertRaises(SafeError):self.operator.import_drafts(self.batch(),'2026-10-12',True)
        self.assertEqual(before,(self.root/'private/posts.json').read_bytes())
    def test_human_approval_schedule_and_edit_revoke(self):
        self.operator.import_drafts(self.batch(),'2026-10-12',True)
        with self.assertRaises(SafeError):self.operator.schedule('p0')
        self.operator.approve('p0',True);self.operator.schedule('p0')
        self.assertEqual(self.posts()[0]['publish_status'],'scheduled')
        self.operator.edit('p0',{'text':'編集した内容','theme':'AI'})
        row=self.posts()[0]
        self.assertFalse(row['approved']);self.assertNotIn('scheduled_at',row);self.assertEqual(row['topic'],'AI')
    def test_unconfirmed_sources_blocked_and_confirmed_sources_stay_disabled(self):
        before=(self.root/'config/trend_sources.json').read_bytes()
        with self.assertRaises(SafeError):self.operator.source('https://official.example/feed','rss','https://official.example/terms',False)
        self.assertEqual(before,(self.root/'config/trend_sources.json').read_bytes())
        with patch('socket.getaddrinfo') as network:self.operator.source('https://official.example/feed','rss','https://official.example/terms',True)
        network.assert_not_called()
        self.assertFalse(json.loads((self.root/'config/trend_sources.json').read_text())['enabled'])
        with self.assertRaises(SafeError):self.operator.source('https://official.example/feed','rss','https://official.example/terms',True)
        self.operator.remove_source('https://official.example/feed')
        self.assertEqual(json.loads((self.root/'config/trend_sources.json').read_text())['sources'],[])
    def test_competitor_duplicate_import_persists_and_never_calls_api(self):
        data=json.dumps({'observations':[{'url':'https://threads.com/@a/post/abc','theme':'AI','observed_at':'2026-10-08T00:00:00+00:00','likes':0}]}).encode()
        with patch('socket.getaddrinfo') as network:
            self.assertEqual(self.operator.competitors(data,'.json')['imported'],1)
            self.assertEqual(self.operator.competitors(data,'.json')['imported'],0)
        network.assert_not_called()
        self.assertNotIn(b'@a', (self.root/'state/private-state.enc').read_bytes())
    def test_flexible_quota_three_text_posts_and_duplicate_slot_blocked(self):
        policy={**self.policy,'auto_publish_enabled':True}
        config={'timezone':'Asia/Tokyo','auto_publish_enabled':True,'posting_schedule':policy}
        posts=[]
        for i,time in enumerate(('08:00','19:00','22:00','22:00')):
            post={'id':str(i),'post_type':'text','text':str(i),'planned_at':f'2026-10-08T{time}:00+09:00','scheduled_at':f'2026-10-08T{time}:00+09:00','approved':True,'publish_status':'scheduled'}
            post['approval_digest']=approval_digest(post);posts.append(post)
        path=self.root/'queue.json';path.write_text(json.dumps({'posts':posts}))
        history=History(self.root/'h.db');self.addCleanup(history.db.close)
        client=Mock();client.connect.return_value={'id':'a'}
        def publish(client,history,post,**kw):history.reserve('a',post['id'],post);history.finish('a',post['id'],'r'+post['id'],post)
        now=datetime(2026,10,8,14,tzinfo=timezone.utc)
        self.assertEqual(len(due_posts(posts,config,now)),4)
        with patch('threads_publisher.scheduler.publish_post',side_effect=publish):
            self.assertEqual(run_scheduled(client,history,path,config,True,True,now)['published'],3)
            self.assertEqual(run_scheduled(client,history,path,config,True,True,now)['published'],0)
    def test_previous_sunday_22_is_carried_into_next_report(self):
        history=History(self.root/'h.db');self.addCleanup(history.db.close)
        history.reserve('a','p',{'text':'not public','post_type':'text'});history.finish('a','p','r',{'text':'not public','post_type':'text'})
        history.db.execute("UPDATE posts SET published_at='2026-10-04T13:00:00+00:00'");history.db.commit()
        report=generate_report(history,self.root/'missing.json',None,self.root/'reports',now=datetime(2026,10,11,12,tzinfo=timezone.utc))
        self.assertEqual(report['sections']['2_operations']['carried_over_previous_sunday'],1)
        self.assertTrue(report['posts'][0]['carried_over'])
        self.assertNotIn('not public',json.dumps(report))
    def test_trend_previous_week_comparison_and_unknown_baseline(self):
        from threads_publisher.career_reports import trend_summary
        path=self.root/'trend-data.json'
        items=[{'url':'https://official.example/'+name,'title':'AIの情報','collected_at':date,'provenance':'permitted_official_feed'} for name,date in [('old','2026-10-01T00:00:00+00:00'),('one','2026-10-08T00:00:00+00:00'),('two','2026-10-09T00:00:00+00:00')]]
        path.write_text(json.dumps({'items':items}))
        start=datetime(2026,10,5,tzinfo=timezone.utc);end=datetime(2026,10,11,12,tzinfo=timezone.utc)
        row=trend_summary(path,{'AI'},start,end,end)[0]
        self.assertEqual(row['samples'],2);self.assertEqual(row['previous_samples'],1);self.assertEqual(row['sample_change'],1)
        path.write_text(json.dumps({'items':items[1:]}))
        self.assertIsNone(trend_summary(path,{'AI'},start,end,end)[0]['sample_change'])
