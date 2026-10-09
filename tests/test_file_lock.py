import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from threads_publisher import file_lock
from threads_publisher.operations import operation_lock
from threads_publisher.core import SafeError


class FileLockTests(unittest.TestCase):
    def test_competing_operation_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'operation.lock'
            with operation_lock(path):
                with self.assertRaises(SafeError):
                    with operation_lock(path):
                        self.fail('Concurrent operation entered')
            with operation_lock(path):
                pass

    def test_windows_lock_unlock_same_byte(self):
        backend = Mock(LK_UNLCK=0, LK_NBLCK=2, LK_LOCK=1)
        with tempfile.TemporaryFile(mode='w+') as stream:
            with patch.object(file_lock.os, 'name', 'nt'), patch.object(file_lock, 'msvcrt', backend, create=True):
                file_lock.flock(stream, file_lock.LOCK_EX | file_lock.LOCK_NB)
                file_lock.flock(stream, file_lock.LOCK_UN)
            self.assertEqual(backend.locking.call_args_list[0].args[1:], (2, 1))
            self.assertEqual(backend.locking.call_args_list[1].args[1:], (0, 1))
            stream.seek(0)
            self.assertEqual(stream.read(), '0')

    def test_windows_contention_becomes_safe_error(self):
        backend = Mock(LK_UNLCK=0, LK_NBLCK=2, LK_LOCK=1)
        backend.locking.side_effect = OSError('private path')
        with tempfile.TemporaryFile(mode='w+') as stream:
            with patch.object(file_lock.os, 'name', 'nt'), patch.object(file_lock, 'msvcrt', backend, create=True):
                with self.assertRaises(BlockingIOError) as error:
                    file_lock.flock(stream, file_lock.LOCK_EX | file_lock.LOCK_NB)
                self.assertNotIn('private path', str(error.exception))
