import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from threads_publisher.core import History, SafeError, publish_post
from threads_publisher.weekly import import_week, edit_text, export_approved
from threads_publisher.management import set_approval
from threads_publisher.private_state import PrivateState, materialize_secret_queue
from threads_publisher.report import weekly_report
from threads_publisher.writer import Writer


class PlusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous=Path.cwd()
        self.addCleanup(os.chdir,previous)
        os.chdir(self.tmp.name)
        Path('private').mkdir()
        self.rows={'posts':[{'id':f'w-{i}','text':f'投稿番号{i}の内容です。'} for i in range(21)]}
        Path('private/week.json').write_text(json.dumps(self.rows,ensure_ascii=False))

    def import_posts(self):
        return import_week('private/week.json','private/posts.json','2026-10-12')

    def test_import_21_schedule_and_idempotence(self):
        self.assertEqual(self.import_posts()['added'],21)
        posts=json.loads(Path('private/posts.json').read_text())['posts']
        self.assertEqual(posts[0]['scheduled_at'],'2026-10-12T08:00:00+09:00')
        self.assertEqual(posts[-1]['scheduled_at'],'2026-10-18T22:00:00+09:00')
        self.assertTrue(all(p['approved'] is False for p in posts))
        set_approval('private/posts.json','w-0',True)
        self.assertEqual(self.import_posts()['unchanged'],21)
        self.assertTrue(json.loads(Path('private/posts.json').read_text())['posts'][0]['approved'])

    def test_duplicate_or_conflicting_import_atomic(self):
        self.import_posts()
        before=Path('private/posts.json').read_bytes()
        self.rows['posts'][0]['text']='変更内容'
        Path('private/week.json').write_text(json.dumps(self.rows))
        with self.assertRaises(SafeError): self.import_posts()
        self.assertEqual(Path('private/posts.json').read_bytes(),before)

    def test_wrong_count_and_public_output_rejected(self):
        self.rows['posts'].pop()
        Path('private/week.json').write_text(json.dumps(self.rows))
        with self.assertRaises(SafeError): self.import_posts()
        with self.assertRaises(SafeError): import_week('private/week.json','posts/posts.json','2026-10-12')

    def test_edit_revokes_approval_export_only_approved(self):
        self.import_posts()
        set_approval('private/posts.json','w-0',True)
        edit_text('private/posts.json','w-0','編集した本文')
        self.assertIs(json.loads(Path('private/posts.json').read_text())['posts'][0]['approved'],False)
        set_approval('private/posts.json','w-1',True)
        self.assertEqual(export_approved('private/posts.json','private/approved-posts.json')['approved'],1)
        self.assertEqual(json.loads(Path('private/approved-posts.json').read_text())['posts'][0]['id'],'w-1')

    def test_secret_queue_missing_invalid_never_logs(self):
        with patch.dict(os.environ,{'THREADS_SCHEDULE_JSON':'secret-sensitive-text'}):
            with self.assertRaises(SafeError) as caught: materialize_secret_queue()
            self.assertNotIn('secret-sensitive-text',str(caught.exception))
        self.import_posts(); set_approval('private/posts.json','w-0',True)
        export_approved('private/posts.json','private/export.json')
        with patch.dict(os.environ,{'THREADS_SCHEDULE_JSON':Path('private/export.json').read_text()}):
            self.assertEqual(materialize_secret_queue(),1)

    def test_encrypted_history_roundtrip_and_wrong_key(self):
        from cryptography.fernet import Fernet
        key=Fernet.generate_key().decode()
        history=History('private/history.sqlite3')
        history.reserve('account','reserved',{'text':'DO-NOT-LEAK'})
        history.db.close()
        # Pending history itself does not contain text; other confidential state is encrypted too.
        Path('private/insights.json').write_text('{"confidential":"DO-NOT-LEAK"}')
        state=PrivateState(key=key);state.save()
        self.assertNotIn(b'DO-NOT-LEAK',Path('state/private-state.enc').read_bytes())
        Path('private/history.sqlite3').unlink();Path('private/insights.json').unlink()
        state.restore()
        restored=History('private/history.sqlite3')
        self.assertTrue(restored.recorded('account','reserved'));restored.db.close()
        with self.assertRaises(SafeError): PrivateState(key=Fernet.generate_key().decode()).restore()

    def test_legacy_history_bootstrap_and_prepublish_reservation(self):
        from cryptography.fernet import Fernet
        Path('state').mkdir()
        legacy=History('state/history.sqlite3');legacy.reserve('a','old');legacy.db.close()
        original=Path('state/history.sqlite3').read_bytes()
        state=PrivateState(key=Fernet.generate_key().decode());state.restore()
        history=History('private/history.sqlite3',state.save)
        client=Mock();client.connect.return_value={'id':'a'};client.create.side_effect=SafeError('failure')
        with self.assertRaises(SafeError): publish_post(client,history,{'id':'new','text':'UNPUBLISHED','approved':True},True,'new')
        self.assertTrue(history.recorded('a','old'));self.assertTrue(history.recorded('a','new'))
        self.assertIsNone(history.db.execute("SELECT text FROM posts WHERE post_id='new'").fetchone()[0])
        history.db.close()
        self.assertEqual(Path('state/history.sqlite3').read_bytes(),original)
        self.assertTrue(Path('state/private-state.enc').exists())

    def test_paid_api_is_never_called(self):
        transport=Mock()
        writer=Writer({'enabled':True},'any-key',transport)
        with self.assertRaises(SafeError): writer.generate({})
        transport.assert_not_called()

    def test_weekly_report_no_raw_text_or_account_and_no_fake_rate(self):
        snapshots={'snapshots':[{'account':'PERSONAL-ACCOUNT','post_id':'private-id','remote_id':'r','published_at':'2026-10-06T00:00:00+00:00','checkpoint':'24h','late':False,'text':'SECRET-TEXT','metrics':{'views':100,'likes':5}}]}
        Path('private/insights.json').write_text(json.dumps(snapshots))
        weekly_report('private/insights.json','private/weekly-report.md',datetime(2026,10,8,tzinfo=timezone.utc))
        report=Path('private/weekly-report.md').read_text()
        self.assertNotIn('SECRET-TEXT',report);self.assertNotIn('PERSONAL-ACCOUNT',report)
        self.assertIn('不明',report);self.assertIn('21本',report)
    def test_weekly_report_is_once_per_japan_week(self):
        Path('private/insights.json').write_text('{"snapshots":[]}')
        now=datetime(2026,10,11,16,tzinfo=timezone.utc) # Monday in Tokyo.
        first=weekly_report('private/insights.json','private/weekly-report.md',now,weekly_only=True)
        second=weekly_report('private/insights.json','private/weekly-report.md',now,weekly_only=True)
        self.assertNotIn('skipped',first);self.assertTrue(second['skipped'])

    def test_unchanged_vault_no_reencryption_and_paid_persistence_forbidden(self):
        from cryptography.fernet import Fernet
        from threads_publisher.storage import git_persist
        history=History('private/history.sqlite3');history.db.close()
        state=PrivateState(key=Fernet.generate_key().decode());state.save()
        before=Path('state/private-state.enc').read_bytes()
        state.save();self.assertEqual(Path('state/private-state.enc').read_bytes(),before)
        with self.assertRaises(SafeError):git_persist(['posts/posts.json'])
    def test_new_legacy_manual_records_merge_into_encrypted_history(self):
        from cryptography.fernet import Fernet
        Path('state').mkdir()
        legacy=History('state/history.sqlite3');legacy.reserve('a','initial');legacy.db.close()
        state=PrivateState(key=Fernet.generate_key().decode());state.restore();state.save()
        legacy=History('state/history.sqlite3');legacy.reserve('a','later-manual');legacy.finish('a','later-manual','remote');legacy.db.close()
        state.restore()
        private=History('private/history.sqlite3')
        self.assertTrue(private.recorded('a','later-manual'))
        private.db.close()
