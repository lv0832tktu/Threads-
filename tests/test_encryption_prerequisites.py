import builtins
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from cryptography.fernet import Fernet
from threads_publisher.search_check import main


class EncryptionPrerequisiteTests(unittest.TestCase):
    def validate(self, key):
        output=io.StringIO()
        with patch.dict(os.environ,{'THREADS_STATE_KEY':key}), patch('sys.argv',['search-check','--validate-key']), patch('threads_publisher.search_check.run_check') as search, redirect_stdout(output):
            status=main()
        search.assert_not_called()
        return status,output.getvalue()

    def test_valid_key_local_check_needs_no_token_or_api(self):
        key=Fernet.generate_key().decode()
        status,output=self.validate(key)
        self.assertEqual(status,0)
        self.assertIn('no API request',output)
        self.assertNotIn(key,output)

    def test_missing_and_invalid_secrets_have_distinct_safe_errors(self):
        for key,message in [('', 'missing or unavailable'),('PRIVATE-INVALID-KEY','is invalid')]:
            status,output=self.validate(key)
            self.assertEqual(status,1)
            self.assertIn(message,output)
            if key:self.assertNotIn(key,output)

    def test_missing_dependency_identified_without_secret_leak(self):
        original_import=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='cryptography.fernet':raise ImportError('unavailable')
            return original_import(name,*args,**kwargs)
        key=Fernet.generate_key().decode()
        with patch('builtins.__import__',side_effect=missing):
            status,output=self.validate(key)
        self.assertEqual(status,1)
        self.assertIn('dependency missing',output)
        self.assertNotIn(key,output)
