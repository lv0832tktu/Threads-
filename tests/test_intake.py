import json
import tempfile
import unittest
from pathlib import Path
from threads_publisher.core import SafeError
from threads_publisher.schema import normalize_post
from threads_publisher.intake import import_batch, approve_record, schedule_record, edit_record


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.posts = self.path/'posts.json'
        self.posts.write_text('{"posts": []}')
        self.source = self.path/'source.json'
    def tearDown(self): self.tmp.cleanup()
    def batch(self):
        records=[]
        for day in range(7):
            records += [{'post_id':f't-{day}','post_type':'text','text':f'今日の仕事 {day}','approved':True},
                        {'id':f'i-{day}','post_type':'image','text':f'画像説明 {day}','image_urls':['https://example.com/image.png'],'rights_confirmed':False},
                        {'id':f'r-{day}','post_type':'thread','text':f'項目{day}-0','thread_items':[f'項目{day}-{i}' for i in range(3)]}]
        self.source.write_text(json.dumps({'posts':records}))
    def load(self): return json.loads(self.posts.read_text())['posts']
    def test_import_pending_planned_only(self):
        self.batch(); ids=import_batch(self.source,self.posts,start_date='2026-10-12')
        self.assertEqual(len(ids),21)
        records=self.load(); self.assertTrue(all(p['approved'] is False and 'scheduled_at' not in p for p in records))
        self.assertEqual(records[0]['planned_at'],'2026-10-12T08:00:00+09:00')
        self.assertEqual(records[1]['planned_at'],'2026-10-12T19:00:00+09:00')
    def test_approval_and_scheduling_separate_edit_revokes(self):
        self.batch(); import_batch(self.source,self.posts,start_date='2026-10-12')
        with self.assertRaises(SafeError): schedule_record(self.posts,'t-0')
        approve_record(self.posts,'t-0'); self.assertNotIn('scheduled_at',self.load()[0])
        schedule_record(self.posts,'t-0'); self.assertIn('scheduled_at',self.load()[0])
        edit_record(self.posts,'t-0',{'text':'編集後の内容'})
        self.assertFalse(self.load()[0]['approved']); self.assertNotIn('scheduled_at',self.load()[0])
    def test_image_rights_required(self):
        self.batch(); import_batch(self.source,self.posts,start_date='2026-10-12')
        with self.assertRaises(SafeError): approve_record(self.posts,'i-0')
        edit_record(self.posts,'i-0',{'rights_confirmed':True,'image_sha256':['0'*64]}); approve_record(self.posts,'i-0')
    def test_duplicate_import_is_atomic(self):
        self.batch(); import_batch(self.source,self.posts,start_date='2026-10-12'); before=self.posts.read_text()
        with self.assertRaises(SafeError): import_batch(self.source,self.posts,start_date='2026-10-12')
        self.assertEqual(before,self.posts.read_text())
    def test_legacy_and_private_urls(self):
        post=normalize_post({'id':'legacy','text':'既存投稿','approved':True})
        self.assertEqual(post['post_type'],'text'); self.assertTrue(post['approved'])
        with self.assertRaises(SafeError): normalize_post({'id':'image','text':'画像','post_type':'image','image_urls':['https://127.0.0.1/a']})
    def test_csv_embedded_arrays(self):
        import csv
        path=self.path/'source.csv'
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['id','text','post_type','thread_items','keywords','approved'])
            writer.writeheader();writer.writerow({'id':'csv-1','text':'一','post_type':'thread','thread_items':json.dumps(['一','二','三']),'keywords':json.dumps(['仕事']),'approved':'true'})
        import_batch(path,self.posts,start_date='2026-10-12',require_21=False)
        self.assertFalse(self.load()[0]['approved']); self.assertEqual(len(self.load()[0]['thread_items']),3)
    def test_approval_digest_detects_out_of_band_change(self):
        self.batch(); import_batch(self.source,self.posts,start_date='2026-10-12'); approve_record(self.posts,'t-0')
        document=json.loads(self.posts.read_text()); document['posts'][0]['text']='承認後に直接変更'
        self.posts.write_text(json.dumps(document))
        with self.assertRaises(SafeError): schedule_record(self.posts,'t-0')
    def test_status_lifecycle(self):
        self.batch(); import_batch(self.source,self.posts,start_date='2026-10-12')
        self.assertEqual(self.load()[0]['publish_status'],'pending_approval')
        approve_record(self.posts,'t-0'); self.assertEqual(self.load()[0]['publish_status'],'approved')
        schedule_record(self.posts,'t-0'); self.assertEqual(self.load()[0]['publish_status'],'scheduled')
        edit_record(self.posts,'t-0',{'text':'承認からやり直す'})
        self.assertEqual(self.load()[0]['publish_status'],'pending_approval')
        self.assertNotIn('approval_digest',self.load()[0])
    def test_padding_and_ambiguous_thread_rejected(self):
        with self.assertRaises(SafeError): normalize_post({'id':'x','text':'内容'+' '*500})
        with self.assertRaises(SafeError): normalize_post({'id':'x','post_type':'thread','text':'別の冒頭','thread_items':['一','二','三']})
    def test_weekly_misaligned_time_rejected(self):
        self.batch(); doc=json.loads(self.source.read_text()); doc['posts'][0]['publish_datetime']='2026-10-12T09:00:00+09:00'; self.source.write_text(json.dumps(doc))
        with self.assertRaises(SafeError): import_batch(self.source,self.posts,start_date='2026-10-12')
        self.assertEqual(self.load(),[])

    def test_conflicting_datetime_aliases_rejected_atomically(self):
        self.batch()
        document=json.loads(self.source.read_text())
        document['posts'][0].update(publish_datetime='2026-10-12T08:00:00+09:00',scheduled_at='2026-10-13T08:00:00+09:00')
        self.source.write_text(json.dumps(document))
        with self.assertRaises(SafeError): import_batch(self.source,self.posts,start_date='2026-10-12')
        self.assertEqual(self.load(),[])
