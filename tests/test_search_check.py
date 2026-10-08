import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from cryptography.fernet import Fernet
from threads_publisher.search_check import select_keywords,run_check,decrypt_report
from threads_publisher.core import SafeError


class SearchCheckTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads(Path('config/search-keywords.json').read_text())
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cwd=Path.cwd();self.addCleanup(os.chdir,cwd);os.chdir(self.tmp.name)
        self.key=Fernet.generate_key().decode()

    def test_catalog_complete_selection_and_caps(self):
        self.assertEqual(sum(len(g['keywords']) for g in self.catalog['groups']),204)
        all_words={w for g in self.catalog['groups'] for w in g['keywords']}
        self.assertEqual(len(all_words),203)
        self.assertTrue({'Nano Banana','S&P500','AI献立（食費節約）','Claude Code','LINE登録'}<=all_words)
        self.assertEqual(select_keywords(self.catalog),['ChatGPT','Claude','Gemini'])
        self.assertEqual(select_keywords(self.catalog,keyword='AI副業'),['AI副業'])
        with self.assertRaises(SafeError):select_keywords(self.catalog,count=11)
        with self.assertRaises(SafeError):select_keywords(self.catalog,keyword='not configured')

    def test_get_only_encoded_queries_no_body_or_token_saved(self):
        requests=[]
        def transport(request,timeout):
            requests.append(request)
            self.assertEqual(request.get_method(),'GET');self.assertNotIn('TOKEN',request.full_url)
            self.assertIn('q=S%26P500',request.full_url)
            self.assertEqual(request.get_header('Authorization'),'Bearer TOKEN')
            return io.BytesIO(json.dumps({'data':[{'id':'public','text':'S&P500の比較 COPY-NOT-SAVED','permalink':'https://www.threads.net/@public/post/abc','likes_count':0}]}).encode())
        result=run_check('TOKEN',self.key,self.catalog,['S&P500'],transport=transport)
        self.assertEqual(result,{'successful_queries':1,'posts':1,'errors':0})
        encrypted=Path('private/search-check.enc').read_bytes()
        self.assertNotIn(b'TOKEN',encrypted)
        payload=json.loads(Fernet(self.key.encode()).decrypt(encrypted))
        self.assertNotIn('COPY-NOT-SAVED',json.dumps(payload))
        self.assertEqual(payload['posts'][0]['metrics']['likes'],0)
        self.assertIsNone(payload['posts'][0]['metrics']['replies'])
        decrypt_report('private/search-check.enc',self.key)
        report=Path('private/search-check.md').read_text()
        self.assertIn('取得不可',report);self.assertIn('お金・投資・資産形成',report)
        self.assertIn('https://www.threads.net/@public/post/abc',report)

    def test_auth_error_encrypted_and_no_raw_secret(self):
        calls=[]
        def fail(request,timeout):
            calls.append(1)
            raise urllib.error.HTTPError(request.full_url,400,'TOKEN',{},io.BytesIO(b'{"error":{"code":190,"message":"TOKEN"}}'))
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT','Claude'],transport=fail)
        self.assertEqual(result['errors'],1);self.assertEqual(len(calls),1)
        payload=json.loads(Fernet(self.key.encode()).decrypt(Path('private/search-check.enc').read_bytes()))
        self.assertNotIn('TOKEN',json.dumps(payload));self.assertIn('expired',payload['errors'][0]['reason'])

    def test_permission_denial_and_limit_without_retry(self):
        for status in (403,429):
            transport=Mock(side_effect=urllib.error.HTTPError('hidden',status,'secret',{},io.BytesIO(b'{}')))
            result=run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=transport)
            self.assertEqual(result['errors'],1);transport.assert_called_once()

    def test_key_required_before_api_and_wrong_key_rejected(self):
        transport=Mock()
        with patch.dict(os.environ,{'THREADS_STATE_KEY':''}):
            with self.assertRaises(SafeError):run_check('TOKEN','',self.catalog,['ChatGPT'],transport=transport)
        transport.assert_not_called()
        run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=lambda *args,**kwargs:io.BytesIO(b'{"data":[]}'))
        with self.assertRaises(SafeError):decrypt_report('private/search-check.enc',Fernet.generate_key().decode())

    def test_zero_results_success_and_invalid_public_output(self):
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=lambda *args,**kwargs:io.BytesIO(b'{"data":[]}'))
        self.assertEqual(result['successful_queries'],1);self.assertEqual(result['posts'],0)
        with self.assertRaises(SafeError):run_check('TOKEN',self.key,self.catalog,['ChatGPT'],path='posts/result.json')

    def test_repeated_post_dedup_and_arbitrary_paging_ignored(self):
        calls=[]
        def transport(request,timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps({'data':[{'id':'one','text':'AI活用の例','permalink':'https://threads.com/@a/post/x'}],'paging':{'next':'https://evil.invalid/TOKEN'}}).encode())
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT','Claude'],transport=transport)
        self.assertEqual(result['posts'],1);self.assertEqual(len(calls),2)
        self.assertTrue(all('keyword_search' in url and 'evil.invalid' not in url for url in calls))
