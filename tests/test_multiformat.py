import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from threads_publisher.core import History, SafeError, publish_post
from threads_publisher.schema import approval_digest
from threads_publisher.publisher import publish_content


class FakeClient:
    media_hosts=['example.com']
    def __init__(self): self.calls=[];self.index=0;self.pending=None;self.fail=None
    def connect(self): return {'id':'account'}
    def create(self, account, **payload):
        self.index+=1;self.calls.append(('create',payload));return str(self.index)
    def container_status(self, container): return 'IN_PROGRESS' if container==self.pending else 'FINISHED'
    def publish(self, account, container):
        self.calls.append(('publish',container))
        if container==self.fail: raise SafeError('uncertain')
        return 'remote-'+container


class MultiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.history=History(str(Path(self.temp.name)/'history.db'));self.addCleanup(self.history.db.close)
        self.client=FakeClient()
        self.post={'id':'tree','post_type':'thread','text':'一','thread_items':['一','二','三'],'approved':True}
        self.post['approval_digest']=approval_digest(self.post)
    def publish(self,resume=False):
        return publish_content(self.client,self.history,self.post,True,self.post['id'],resume)
    def test_thread_order_and_history(self):
        self.assertEqual(self.publish(),'remote-1')
        creates=[payload for action,payload in self.client.calls if action=='create']
        self.assertNotIn('reply_to_id',creates[0]);self.assertEqual(creates[1]['reply_to_id'],'remote-1');self.assertEqual(creates[2]['reply_to_id'],'remote-2')
        row=self.history.db.execute('SELECT status,job_status,remote_ids FROM posts').fetchone()
        self.assertEqual(row,('published','posted','["remote-1", "remote-2", "remote-3"]'))
        with self.assertRaises(SafeError): self.publish()
    def test_pending_created_explicit_resume(self):
        self.client.pending='2'
        with self.assertRaises(SafeError): self.publish()
        self.assertEqual(self.history.db.execute('SELECT job_status FROM posts').fetchone()[0],'partial')
        self.client.pending=None;self.publish(resume=True)
        self.assertEqual(sum(action=='create' for action,_ in self.client.calls),3)
        self.assertEqual(sum(action=='publish' and data=='1' for action,data in self.client.calls),1)
    def test_uncertain_publish_never_resumed(self):
        self.client.fail='2'
        with self.assertRaises(SafeError):self.publish()
        before=len(self.client.calls)
        with self.assertRaises(SafeError):self.publish(resume=True)
        self.assertEqual(before,len(self.client.calls))
    def test_approval_digest_change_rejected(self):
        self.post['thread_items'][2]='変更'
        with self.assertRaises(SafeError): self.publish()
        self.assertEqual(self.client.calls,[])
    def test_image_and_carousel(self):
        for kind,urls in [('image',['https://example.com/1.png']),('carousel',['https://example.com/1.png','https://example.com/2.png'])]:
            post={'id':kind,'post_type':kind,'text':'画像投稿','approved':True,'rights_confirmed':True,'image_urls':urls,'image_sha256':['0'*64 for _ in urls]}
            post['approval_digest']=approval_digest(post)
            client=FakeClient()
            with patch('threads_publisher.media.validate_image_url',return_value={'sha256':'0'*64}) as validate:
                publish_post(client,self.history,post,True,kind)
                self.assertEqual(validate.call_count,len(urls))
            creates=[payload for action,payload in client.calls if action=='create']
            self.assertEqual(creates[-1]['media_type'],'IMAGE' if kind=='image' else 'CAROUSEL')
            if kind=='carousel':
                self.assertTrue(creates[0]['is_carousel_item'])
                self.assertEqual(creates[-1]['children'],'1,2')
                self.assertEqual(sum(action=='publish' for action,_ in client.calls),1)
    def test_reservation_must_persist_before_create(self):
        def fail(): raise SafeError('cannot persist')
        self.history.persist=fail
        with self.assertRaises(SafeError):self.publish()
        self.assertEqual(self.client.calls,[])
    def test_legacy_database_migrates(self):
        import sqlite3
        path=Path(self.temp.name)/'legacy.db'
        db=sqlite3.connect(path);db.execute('CREATE TABLE posts(account TEXT,post_id TEXT,status TEXT,remote_id TEXT,PRIMARY KEY(account,post_id))');db.execute("INSERT INTO posts VALUES('a','old','published','r')");db.commit();db.close()
        history=History(path)
        self.assertTrue(history.recorded('a','old'));self.assertEqual(history.db.execute('SELECT status FROM posts').fetchone()[0],'published');history.db.close()
    def test_confirmed_part_must_persist_before_next_reply(self):
        def persist():
            count=self.history.db.execute("SELECT COUNT(*) FROM post_parts WHERE status='published'").fetchone()[0]
            if count: raise SafeError('confirmation persistence failed')
        self.history.persist=persist
        with self.assertRaises(SafeError):self.publish()
        self.assertEqual(sum(action=='create' for action,_ in self.client.calls),1)
        self.assertEqual(sum(action=='publish' for action,_ in self.client.calls),1)
    def test_missing_digest_and_image_rights_fail_closed(self):
        self.post.pop('approval_digest')
        with self.assertRaises(SafeError):self.publish()
        self.assertEqual(self.client.calls,[])
        image={'id':'bad','post_type':'image','text':'画像','image_urls':['https://example.com/x.png'],'approved':True}
        image['approval_digest']=approval_digest(image)
        with self.assertRaises(SafeError):publish_content(self.client,self.history,image,True,'bad')
        self.assertEqual(self.client.calls,[])
    def test_approved_image_hash_mismatch_stops_before_create(self):
        image={'id':'hash','post_type':'image','text':'画像','image_urls':['https://example.com/x.png'],'approved':True,'rights_confirmed':True,'image_sha256':['a'*64]}
        image['approval_digest']=approval_digest(image)
        with patch('threads_publisher.media.validate_image_url', return_value={'sha256':'b'*64}):
            with self.assertRaises(SafeError):publish_content(self.client,self.history,image,True,'hash')
        self.assertEqual(self.client.calls,[])
    def test_image_hash_is_bound_to_approval(self):
        image={'id':'hash','post_type':'image','text':'画像','image_urls':['https://example.com/x.png'],'approved':True,'rights_confirmed':True,'image_sha256':['a'*64]}
        before=approval_digest(image)
        image['image_sha256']=['b'*64]
        self.assertNotEqual(before,approval_digest(image))
    def test_offline_image_manifest_private_only(self):
        import json
        from scripts import prepare_images
        root=Path(self.temp.name)
        private=root/'private';private.mkdir()
        image=private/'one.png';image.write_bytes(b'test')
        manifest=private/'images.json';manifest.write_text(json.dumps({'image_files':[{'url':'https://example.com/one.png','file':'private/one.png'}]}))
        with patch.object(prepare_images,'ROOT',root), patch.object(prepare_images,'validate_image_file',return_value={'mime':'image/png','bytes':4,'width':320,'height':320,'sha256':'a'*64}):
            self.assertEqual(prepare_images.prepare(manifest,private/'result.json'),1)
            result=json.loads((private/'result.json').read_text())
            self.assertFalse(result['uploaded']);self.assertEqual(result['images'][0]['sha256'],'a'*64)
            with self.assertRaises(SafeError):prepare_images.prepare(manifest,root/'public.json')

    def test_image_hash_required_before_any_api(self):
        image={'id':'nohash','post_type':'image','text':'画像','image_urls':['https://example.com/x.png'],'approved':True,'rights_confirmed':True}
        image['approval_digest']=approval_digest(image)
        with self.assertRaises(SafeError):publish_content(self.client,self.history,image,True,'nohash')
        self.assertEqual(self.client.calls,[])
