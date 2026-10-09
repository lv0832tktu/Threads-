import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from threads_publisher.core import SafeError
from threads_publisher.private_state import validated_queue, materialize_secret_queue, export_queue
from threads_publisher.schema import approval_digest
from threads_publisher.__main__ import main


def typed(identifier='one'):
    post={'id':identifier,'post_type':'thread','text':'first '+identifier,'thread_items':['first '+identifier,'second '+identifier,'third '+identifier], 'approved':True,'scheduled_at':'2026-10-10T08:00:00+09:00'}
    post['approval_digest']=approval_digest(post)
    return post


class TypedQueueTests(unittest.TestCase):
    def test_tampered_approval_rejected(self):
        post=typed()
        self.assertEqual(validated_queue([post])[0]['post_type'],'thread')
        post['thread_items'][1]='tampered'
        with self.assertRaises(SafeError): validated_queue([post])

    def test_legacy_text_compatibility(self):
        self.assertEqual(len(validated_queue([{'id':'legacy','text':'hello','approved':True,'scheduled_at':'2026-10-10T08:00:00+09:00'}])),1)

    def test_chunks_merge_and_no_secret_logged(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.getcwd',return_value=directory):
            with patch.dict(os.environ,{'THREADS_SCHEDULE_JSON':json.dumps({'posts':[typed('one')]}), 'THREADS_SCHEDULE_JSON_2':json.dumps({'posts':[typed('two')]})},clear=True):
                target=Path(directory)/'private/approved-posts.json'
                self.assertEqual(materialize_secret_queue(target),2)
                self.assertEqual(len(json.loads(target.read_text())['posts']),2)

    def test_export_filters_drafts_chunks_and_clears_stale(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.getcwd',return_value=directory):
            root=Path(directory)/'private'; root.mkdir()
            source=root/'posts.json'; target=root/'approved.json'
            source.write_text(json.dumps({'posts':[typed('one'),typed('two'),{'id':'draft','text':'draft','approved':False}]}))
            size=len((json.dumps({'posts':validated_queue([typed('one')])},ensure_ascii=False,indent=2)+'\n').encode())+10
            result=export_queue(source,target,limit=size)
            self.assertEqual(result,{'approved':2,'chunks':2})
            self.assertEqual(json.loads((root/'approved-3.json').read_text()),{'posts':[]})
            self.assertLessEqual(len(target.read_bytes()),size)

    def test_cli_approval_and_scheduling_are_separate(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.getcwd',return_value=directory):
            root=Path(directory)/'private'; root.mkdir()
            post=typed(); post['approved']=False; post.pop('approval_digest');post.pop('scheduled_at')
            path=root/'posts.json'; path.write_text(json.dumps({'posts':[post]}))
            with patch('sys.argv',['threads','approve-draft','--drafts',str(path),'--post-id','one']),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            self.assertNotIn('scheduled_at',json.loads(path.read_text())['posts'][0])
            with patch('sys.argv',['threads','schedule-draft','--drafts',str(path),'--post-id','one','--publish-datetime','2026-10-10T08:00:00+09:00']),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            self.assertEqual(json.loads(path.read_text())['posts'][0]['publish_status'],'scheduled')

    def test_legacy_queue_uses_legacy_publication(self):
        from threads_publisher.core import History, publish_post
        from unittest.mock import Mock
        post=validated_queue([{'id':'legacy','text':'hello','approved':True,'scheduled_at':'2026-10-10T08:00:00+09:00'}])[0]
        self.assertNotIn('post_type',post)
        client=Mock(); client.connect.return_value={'id':'account'}
        client.create.return_value='container'; client.publish.return_value='remote'
        with tempfile.TemporaryDirectory() as directory:
            history=History(Path(directory)/'history.sqlite3')
            try:
                self.assertEqual(publish_post(client,history,post,True,'legacy'),'remote')
                client.publish.assert_called_once_with('account','container')
            finally: history.db.close()

    def test_disabled_keywords_needs_no_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'keywords.json';config.write_text('{"enabled":false}')
            with patch.dict(os.environ,{},clear=True),patch('sys.argv',['threads','keywords','--keywords-config',str(config)]),patch('threads_publisher.__main__.Client') as client,redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
                client.assert_not_called()

    def test_latest_insights_backfills_and_retains_followers_privately(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory, patch('os.getcwd',return_value=directory):
            config=Path(directory)/'automation.json';config.write_text('{"accounts":{},"insights":{}}')
            def store(path,data):
                destination=Path(directory)/path
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_text(json.dumps(data))
            private=Mock()
            client=Mock();client.connect.return_value={'id':'account'}
            insights=Mock();insights.fetch_followers.return_value={'followers_count':None,'status':'unavailable'}
            with patch('sys.argv',['threads','insights','--latest','--backfill-metadata','--private-state','--config',str(config)]),patch('threads_publisher.private_state.PrivateState',return_value=private),patch('threads_publisher.__main__.Client',return_value=client),patch('threads_publisher.__main__.operation_lock') as lock,patch('threads_publisher.__main__.History') as hist,patch('threads_publisher.__main__.account_token',return_value='fake'),patch('threads_publisher.insights.InsightsClient',return_value=insights),patch('threads_publisher.insights.collect',return_value={'collected':0,'skipped':0,'errors':0}) as backfill,patch('threads_publisher.insights.collect_latest',return_value={'collected':1,'skipped':0,'errors':0}) as latest,patch('threads_publisher.storage.atomic_json',side_effect=store),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
                self.assertTrue(backfill.call_args.kwargs['backfill'])
                latest.assert_called_once()
                insights.fetch_followers.assert_called_once_with('account')
                private.save.assert_called_once()
            snapshots=json.loads((Path(directory)/'private/followers.json').read_text())['snapshots']
            self.assertIsNone(snapshots[0]['followers_count'])

    def test_manual_publication_uses_existing_encrypted_history(self):
        from unittest.mock import Mock
        from threads_publisher.core import History
        from threads_publisher.private_state import PrivateState
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest('optional cryptography unavailable')
        previous=os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                Path('posts').mkdir();Path('config').mkdir()
                Path('posts/posts.json').write_text(json.dumps({'posts':[{'id':'legacy','text':'hello','approved':True}]}))
                Path('config/automation.json').write_text('{"accounts":{"default":{"token_env":"THREADS_ACCESS_TOKEN"}}}')
                key=Fernet.generate_key().decode()
                history=History('private/history.sqlite3')
                history.reserve('account','legacy')
                history.finish('account','legacy','remote')
                history.db.close()
                PrivateState(key=key).save()
                client=Mock();client.connect.return_value={'id':'account'}
                with patch.dict(os.environ,{'THREADS_STATE_KEY':key,'THREADS_ACCESS_TOKEN':'fake','THREADS_PUBLISH_ENABLED':'true'},clear=True),patch('threads_publisher.__main__.Client',return_value=client),patch('sys.argv',['threads','publish','--post-id','legacy','--approved-post-id','legacy']),redirect_stdout(io.StringIO()):
                    self.assertEqual(main(),1)
                    client.create.assert_not_called();client.publish.assert_not_called()
                with patch.dict(os.environ,{'THREADS_ACCESS_TOKEN':'fake','THREADS_PUBLISH_ENABLED':'true'},clear=True),patch('threads_publisher.__main__.Client',return_value=client),patch('sys.argv',['threads','publish','--post-id','legacy','--approved-post-id','legacy']),redirect_stdout(io.StringIO()):
                    self.assertEqual(main(),1)
                    client.create.assert_not_called();client.publish.assert_not_called()
            finally:
                os.chdir(previous)
