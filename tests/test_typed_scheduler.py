import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from threads_publisher.core import History
from threads_publisher.schema import normalize_post, approval_digest
from threads_publisher.scheduler import run_scheduled, due_posts


class TypedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.history = History(self.root / 'history.sqlite3')
        self.addCleanup(self.history.db.close)
        self.config = {'timezone':'Asia/Tokyo', 'auto_publish_enabled':True,
                       'schedule':{'max_posts_per_day':3,'max_posts_per_run':3,'max_lateness_hours':24}}
        self.now = datetime.fromisoformat('2026-10-09T20:30:00+09:00')
        self.client = Mock()
        self.client.connect.return_value = {'id':'account'}
    def post(self, identifier, kind='text', scheduled=None):
        row = {'id':identifier,'post_type':kind,'text':'テスト', 'approved':True, 'publish_status':'scheduled',
               'scheduled_at':(scheduled or self.now - timedelta(hours=1)).isoformat()}
        if kind in ('image','carousel'):
            row.update(image_urls=['https://example.com/1.jpg'] * (2 if kind=='carousel' else 1), rights_confirmed=True)
        if kind=='thread':
            row['thread_items'] = [{'text':'テスト'},{'text':'二番目'},{'text':'三番目'}]
        row = normalize_post(row)
        row['approval_digest'] = approval_digest(row)
        return row
    def run_posts(self, posts):
        path=self.root/'posts.json'
        path.write_text(json.dumps({'posts':posts}))
        def publish(client, history, post, **kwargs):
            history.reserve('account',post['id'],post)
            history.finish('account',post['id'],'remote-'+post['id'])
        with patch('threads_publisher.scheduler.publish_post', side_effect=publish):
            return run_scheduled(self.client,self.history,path,self.config,True,True,self.now)
    def test_changed_or_missing_digest_never_due(self):
        good=self.post('good')
        changed=dict(self.post('changed'),text='変更した本文')
        missing=self.post('missing');missing.pop('approval_digest')
        self.assertEqual([p['id'] for p in due_posts([good,changed,missing],self.config,self.now)],['good'])
    def test_one_per_format_with_carousel_image_shared(self):
        result=self.run_posts([self.post('t1'),self.post('t2'),self.post('i','image'),self.post('c','carousel'),self.post('th','thread')])
        self.assertEqual(result['published'],3)
        self.assertEqual(result['skipped'],2)
    def test_pending_counts_requested_day_even_when_reserved_different_day(self):
        earlier=self.post('uncertain', scheduled=self.now-timedelta(hours=20))
        self.history.reserve('account','uncertain',earlier)
        self.history.db.execute("UPDATE posts SET created_at='2026-10-08T01:00:00+09:00' WHERE post_id='uncertain'")
        self.history.db.commit()
        result=self.run_posts([self.post('new')])
        self.assertEqual(result['published'],0)
        self.assertEqual(result['skipped'],1)
    def test_expired_future_and_unapproved_stay_out(self):
        future=self.post('future',scheduled=self.now+timedelta(hours=1))
        expired=self.post('old',scheduled=self.now-timedelta(hours=25))
        unapproved=dict(self.post('draft'),approved=False)
        self.assertEqual(due_posts([future,expired,unapproved],self.config,self.now),[])
    def test_dedicated_workflows_gated_shared_group(self):
        root=Path('.github/workflows')
        for name,gate in [('scheduled-posts.yml','THREADS_SCHEDULED_POSTS_ENABLED'),('daily-analytics.yml','THREADS_ANALYTICS_ENABLED'),('weekly-report.yml','THREADS_WEEKLY_REPORT_ENABLED')]:
            text=(root/name).read_text()
            self.assertIn("if: vars."+gate+" == 'true'",text)
            self.assertIn('group: threads-publication',text)
            self.assertIn('cancel-in-progress: false',text)
        self.assertNotIn('  schedule:',(root/'automation.yml').read_text())

    def test_typed_requires_scheduled_status(self):
        for status in ('draft','approved','posted','failed','partial'):
            row=dict(self.post(status),publish_status=status)
            self.assertEqual(due_posts([row],self.config,self.now),[])
    def test_slots_and_secondary_enable_gate(self):
        self.config['posting_schedule']={'slots':{'text':'08:00','image':'12:00','thread':'20:00'},'auto_publish_enabled':False}
        correct=self.post('correct',scheduled=self.now.replace(hour=8,minute=0,second=0,microsecond=0))
        wrong=self.post('wrong',scheduled=self.now.replace(hour=9,minute=0,second=0,microsecond=0))
        self.assertEqual([p['id'] for p in due_posts([correct,wrong],self.config,self.now)],['correct'])
        self.assertTrue(self.run_posts([correct])['disabled'])
        self.client.connect.assert_not_called()
