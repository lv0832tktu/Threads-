import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from threads_publisher.__main__ import main
from threads_publisher.core import SafeError, NoRedirect
from threads_publisher.operations import operation_lock, token_status
from threads_publisher.storage import git_persist


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'accounts':{'default':{'token_env':'THREADS_ACCESS_TOKEN', 'expected_user_id':'expected'}},'timezone':'Asia/Tokyo','auto_publish_enabled':False}))

    def test_manual_default_account_obeys_expected_identity(self):
        client=Mock()
        client.connect.return_value={'id':'wrong'}
        with patch('sys.argv',['publisher','check','--config',str(self.config)]), patch('threads_publisher.__main__.Client',return_value=client), patch('threads_publisher.__main__.operation_lock'):
            self.assertEqual(main(),1)
        client.create.assert_not_called()

    def test_schedule_default_disabled_needs_no_token(self):
        with patch('sys.argv',['publisher','schedule','--config',str(self.config)]), patch('threads_publisher.__main__.Client') as client:
            self.assertEqual(main(),0)
            client.assert_not_called()

    def test_insights_wrong_identity_never_collects(self):
        client=Mock()
        client.connect.return_value={'id':'wrong'}
        history=Mock()
        with patch('sys.argv',['publisher','insights','--config',str(self.config)]), patch('threads_publisher.__main__.Client',return_value=client), patch('threads_publisher.__main__.History',return_value=history), patch('threads_publisher.__main__.operation_lock'), patch('threads_publisher.insights.collect') as collect:
            self.assertEqual(main(),1)
            collect.assert_not_called()

    def test_duplicate_local_execution_is_rejected(self):
        path=self.root/'operations.lock'
        with operation_lock(path):
            with self.assertRaises(SafeError):
                with operation_lock(path):
                    pass

    def test_expiry_plan_without_token_access(self):
        config={'accounts':{'default':{'token_expires_at':'2026-10-10T00:00:00+00:00'}}}
        result=token_status(config,now=datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual(result['days_remaining'],2)
        self.assertIn('refresh',result['action'])
        self.assertEqual(token_status({'accounts':{'default':{}}})['expiry'],'unknown')

    def test_git_persistence_scope_noop_and_conflict(self):
        with self.assertRaises(SafeError):
            git_persist(['.env'])
        with patch('threads_publisher.storage.subprocess.run',return_value=Mock(returncode=0)) as run:
            git_persist(['state/history.sqlite3'])
            self.assertEqual(run.call_count,2)
        import subprocess
        with patch('threads_publisher.storage.subprocess.run',side_effect=[Mock(),Mock(returncode=1),Mock(),subprocess.CalledProcessError(1,'push')]) as run:
            with self.assertRaises(SafeError): git_persist(['state/history.sqlite3'])
            self.assertEqual(run.call_args.args[0],['git','push','origin','HEAD:main'])

    def test_api_redirect_does_not_forward_credentials(self):
        with self.assertRaises(SafeError):
            NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid')

    def test_report_persistence_allowlist_rejects_private_and_traversal(self):
        for path in ('reports/../private/posts.json','reports/weekly/secrets.json','private/insights.json'):
            with self.assertRaises(SafeError): git_persist([path])
        with patch('threads_publisher.storage.subprocess.run',return_value=Mock(returncode=0)) as run:
            git_persist(['reports/latest.md','reports/latest.json','reports/weekly/2026-10-11.md','reports/weekly/2026-10-11.json'])
            self.assertEqual(run.call_count,2)
