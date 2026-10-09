import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from threads_publisher.operator import Operator
from threads_publisher.simple_posting import approve_text_batch,schedule_text_batch,review_digest,weekly_prompt
from threads_publisher.core import SafeError
class SimplePostingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  (self.root/'config').mkdir();(self.root/'config/posting_schedule.json').write_text(json.dumps({'slots':{'text':'08:00','image':'19:00','thread':'22:00'},'timezone':'Asia/Tokyo'}))
  self.op=Operator(self.root);self.path=self.root/'private/posts.json'
  self.op.import_drafts(json.dumps({'posts':[{'id':str(i),'text':'投稿の確認 '+str(i)} for i in range(21)]}).encode(),'2026-10-12',True)
  self.ids=[str(i) for i in range(21)];self.now=datetime(2026,10,9,tzinfo=timezone.utc)
 def rows(self):return json.loads(self.path.read_text())['posts']
 def approve(self):approve_text_batch(self.path,self.ids,review_digest(self.rows()))
 def schedule(self):schedule_text_batch(self.path,self.ids,['08:00','19:00','22:00'],self.now)
 def test_without_key_review_then_schedule_and_export(self):
  self.approve();self.schedule()
  self.assertTrue(all(p['approved'] and p['publish_status']=='scheduled' for p in self.rows()))
  self.assertFalse((self.root/'state/private-state.enc').exists())
  from threads_publisher.private_state import export_queue
  previous=Path.cwd()
  try:
   os.chdir(self.root)
   self.assertEqual(export_queue(self.path,self.root/'private/approved-posts.json')['approved'],21)
  finally:os.chdir(previous)
 def test_unapproved_schedule_atomic(self):
  before=self.path.read_bytes()
  with self.assertRaises(SafeError):self.schedule()
  self.assertEqual(before,self.path.read_bytes())
 def test_stale_review_blocks_all(self):
  digest=review_digest(self.rows());self.op.edit('0',{'text':'編集済み'})
  with self.assertRaises(SafeError):approve_text_batch(self.path,self.ids,digest)
  self.assertFalse(any(p['approved'] for p in self.rows()))
 def test_edit_revokes_and_schedule_blocks_all(self):
  self.approve();self.op.edit('0',{'text':'再確認が必要'})
  before=self.path.read_bytes()
  with self.assertRaises(SafeError):self.schedule()
  self.assertEqual(before,self.path.read_bytes())
 def test_past_and_wrong_slot_blocked(self):
  self.approve()
  with self.assertRaises(SafeError):schedule_text_batch(self.path,self.ids,['12:00'],self.now)
  with self.assertRaises(SafeError):schedule_text_batch(self.path,self.ids,['08:00','19:00','22:00'],datetime(2026,11,1,tzinfo=timezone.utc))
 def test_duplicate_ids_blocked(self):
  with self.assertRaises(SafeError):approve_text_batch(self.path,['0','0'],review_digest(self.rows()[:1]))
 def test_prompt_has_no_api_and_no_preapproval(self):
  prompt=weekly_prompt('2026-10-12')
  for text in ['就活・転職','AI','仕事術','お金','節約','21','08:00','19:00','22:00','"approved":false']:self.assertIn(text,prompt)
