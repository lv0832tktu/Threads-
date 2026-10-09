import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from threads_publisher.core import History, SafeError
from threads_publisher.state_migration import prepare


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'state').mkdir()
        db=History(self.root/'state/history.sqlite3')
        db.reserve('account','published');db.finish('account','published','remote')
        db.reserve('account','uncertain');db.db.close()
        self.old=Fernet.generate_key().decode();self.new=Fernet.generate_key().decode()

    def test_legacy_preserved_no_network_or_empty_reset(self):
        before=(self.root/'state/history.sqlite3').read_bytes()
        with patch('socket.getaddrinfo',side_effect=AssertionError('No network')):
            result=prepare(self.root,next_key=self.new)
        self.assertEqual(result['history_rows'],2)
        self.assertEqual(before,(self.root/'state/history.sqlite3').read_bytes())
        self.assertFalse((self.root/'state/private-state.enc').exists())
        d=json.loads(Fernet(self.new.encode()).decrypt((self.root/'state/private-state.next.enc').read_bytes()))
        self.assertEqual(base64.b64decode(d['files']['history.sqlite3']),before)

    def encrypted(self):
        files={'history.sqlite3':base64.b64encode((self.root/'state/history.sqlite3').read_bytes()).decode(), 'insights.json':base64.b64encode(b'{"private":"secret analysis"}').decode()}
        plain=json.dumps({'version':1,'files':files}).encode()
        encrypted=Fernet(self.old.encode()).encrypt(plain)
        (self.root/'state/private-state.enc').write_bytes(encrypted)
        return plain,encrypted

    def test_encrypted_all_bytes_and_original_preserved(self):
        plain,original=self.encrypted()
        prepare(self.root,self.old,self.new)
        self.assertEqual((self.root/'state/private-state.enc').read_bytes(),original)
        self.assertEqual((self.root/'state/private-state.before-migration.enc').read_bytes(),original)
        candidate=(self.root/'state/private-state.next.enc').read_bytes()
        self.assertEqual(Fernet(self.new.encode()).decrypt(candidate),plain)
        self.assertNotIn(b'secret analysis',candidate)

    def test_wrong_old_key_writes_nothing(self):
        _,original=self.encrypted()
        with self.assertRaises(SafeError):prepare(self.root,Fernet.generate_key().decode(),self.new)
        self.assertEqual((self.root/'state/private-state.enc').read_bytes(),original)
        self.assertFalse((self.root/'state/private-state.next.enc').exists())

    def test_existing_output_not_overwritten(self):
        prepare(self.root,next_key=self.new)
        before=(self.root/'state/private-state.next.enc').read_bytes()
        with self.assertRaises(SafeError):prepare(self.root,next_key=self.new)
        self.assertEqual((self.root/'state/private-state.next.enc').read_bytes(),before)

    def test_local_private_and_missing_history_blocked(self):
        (self.root/'private').mkdir()
        with self.assertRaises(SafeError):prepare(self.root,next_key=self.new)
        (self.root/'private').rmdir();(self.root/'state/history.sqlite3').unlink()
        with self.assertRaises(SafeError):prepare(self.root,next_key=self.new)

    def test_invalid_next_key_and_same_key_blocked(self):
        with self.assertRaises(SafeError):prepare(self.root,next_key='invalid')
        self.encrypted()
        with self.assertRaises(SafeError):prepare(self.root,self.old,self.old)

    def test_corrupted_encrypted_source_is_not_reinitialized(self):
        (self.root/'state/private-state.enc').write_bytes(b'corrupt')
        with self.assertRaises(SafeError):prepare(self.root,self.old,self.new)
        self.assertFalse((self.root/'state/private-state.next.enc').exists())

    def test_active_sqlite_journal_is_rejected(self):
        (self.root/'state/history.sqlite3-wal').write_bytes(b'active')
        with self.assertRaises(SafeError):prepare(self.root,next_key=self.new)
        self.assertFalse((self.root/'state/private-state.next.enc').exists())
