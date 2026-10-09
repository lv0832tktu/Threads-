import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch
from cryptography.fernet import Fernet
from threads_publisher.core import SafeError
from threads_publisher.search_check import run_check,decrypt_report,main


class SearchDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads(Path('config/search-keywords.json').read_text())
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        old=Path.cwd();self.addCleanup(os.chdir,old);os.chdir(self.tmp.name)
        self.key=Fernet.generate_key().decode()

    def test_http_classifications_and_sensitive_body_not_retained(self):
        for status,code,kind in [(400,190,'authentication'),(401,None,'authentication'),(403,10,'permission_or_access'),(429,4,'rate_limit'),(400,100,'request_or_access'),(500,None,'api_error')]:
            body=json.dumps({'error':{'code':code,'error_subcode':463,'message':'SECRET-TOKEN','type':'SECRET-TOKEN','fbtrace_id':'SECRET-TOKEN','error_user_msg':'SECRET-TOKEN'}}).encode()
            transport=Mock(side_effect=urllib.error.HTTPError('https://hidden/SECRET-TOKEN',status,'SECRET-TOKEN',{},io.BytesIO(body)))
            result=run_check('SECRET-TOKEN',self.key,self.catalog,['ChatGPT','Claude'],transport=transport)
            transport.assert_called_once()
            diagnostic=result['diagnostics'][0]
            self.assertEqual(diagnostic['http_status'],status);self.assertEqual(diagnostic['kind'],kind)
            self.assertEqual(diagnostic['api_code'],code);self.assertEqual(diagnostic['api_subcode'],463)
            stored=json.loads(Fernet(self.key.encode()).decrypt(Path('private/search-check.enc').read_bytes()))
            self.assertNotIn('SECRET-TOKEN',json.dumps(result)+json.dumps(stored))
            self.assertEqual(stored['request']['api_version'],'v1.0')
            decrypt_report('private/search-check.enc',self.key)
            self.assertIn('http_status',Path('private/search-check.md').read_text())

    def test_malformed_http_body_preserves_status_without_guessing(self):
        transport=Mock(side_effect=urllib.error.HTTPError('hidden',400,'hidden',{},io.BytesIO(b'PRIVATE-NOT-JSON')))
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=transport)
        self.assertIsNone(result['diagnostics'][0]['api_code'])
        self.assertEqual(result['diagnostics'][0]['kind'],'request_or_access')
        self.assertNotIn('PRIVATE-NOT-JSON',json.dumps(result))

    def test_http_success_error_object_is_not_reported_as_success(self):
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=lambda *a,**k:io.BytesIO(b'{"error":{"code":190}}'))
        self.assertEqual(result['successful_queries'],0)
        self.assertEqual(result['diagnostics'][0]['http_status'],200)
        self.assertEqual(result['diagnostics'][0]['kind'],'authentication')

    def test_connection_error_has_no_invented_http_status(self):
        result=run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=Mock(side_effect=OSError('SECRET-TOKEN')))
        self.assertEqual(result['diagnostics'][0]['kind'],'connection_error')
        self.assertIsNone(result['diagnostics'][0]['http_status'])
        self.assertNotIn('SECRET-TOKEN',json.dumps(result))

    def test_version_and_fields_are_explicit_and_invalid_version_blocks_api(self):
        requests=[]
        def transport(request,timeout):
            requests.append(request)
            return io.BytesIO(b'{"data":[]}')
        run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=transport,api_version='v1.0',fields='minimal')
        self.assertIn('/v1.0/keyword_search?',requests[0].full_url)
        self.assertNotIn('username',requests[0].full_url)
        invalid=Mock()
        with self.assertRaises(SafeError):run_check('TOKEN',self.key,self.catalog,['ChatGPT'],transport=invalid,api_version='../TOKEN')
        invalid.assert_not_called()

    def test_local_diagnostics_do_not_claim_permissions_or_send_requests(self):
        Path('catalog.json').write_text(json.dumps(self.catalog))
        output=io.StringIO()
        with patch.dict(os.environ,{'THREADS_ACCESS_TOKEN':'SECRET-TOKEN','THREADS_STATE_KEY':self.key}),patch('sys.argv',['search-check','--diagnose-config','--catalog','catalog.json']),patch('threads_publisher.market.urllib.request.build_opener') as opener,redirect_stdout(output):
            self.assertEqual(main(),0)
        opener.return_value.open.assert_not_called()
        data=json.loads(output.getvalue())
        self.assertEqual(data['granted_permissions'],'not_verified')
        self.assertEqual(data['network_requests'],0)
        self.assertNotIn('SECRET-TOKEN',output.getvalue());self.assertNotIn(self.key,output.getvalue())

    def test_legacy_encrypted_failure_cannot_recover_missing_status(self):
        old={'collected_at':'2026-10-09T00:00:00+00:00','posts':[],'queries':[],'errors':[{'reason':'Public endpoint request failed'}]}
        Path('private').mkdir()
        Path('private/search-check.enc').write_bytes(Fernet(self.key.encode()).encrypt(json.dumps(old).encode()))
        decrypt_report('private/search-check.enc',self.key)
        self.assertIn('旧履歴',Path('private/search-check.md').read_text())
