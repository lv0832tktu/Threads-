"""Cross-platform advisory locks; the same byte is locked by every Windows caller."""
import os

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

if os.name == 'nt':
    import msvcrt
else:
    import fcntl as _posix


def flock(stream, flags):
    if os.name != 'nt':
        return _posix.flock(stream, flags)
    position = stream.tell()
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write('0')
            stream.flush()
        stream.seek(0)
        mode = msvcrt.LK_UNLCK if flags & LOCK_UN else (
            msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK)
        try:
            msvcrt.locking(stream.fileno(), mode, 1)
        except OSError:
            raise BlockingIOError('Another local operation is running') from None
    finally:
        stream.seek(position)
