"""Local management only: never call APIs, read credentials, or launch workflows.

JSON edits use a shared advisory lock, atomic replacement and fsync. Dashboard
changes must be reviewed and committed to GitHub before Actions can see them.
"""
import fcntl
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path


class ManagementError(ValueError):
    pass


def _edit_json(path, transform):
    path = Path(path)
    with path.with_name(path.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        document = json.loads(path.read_text(encoding='utf-8'))
        transform(document)
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(document, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def set_approval(path, post_id, approved):
    if type(approved) is not bool:
        raise ManagementError('Approval must be a boolean')
    def update(document):
        posts = document['posts']
        ids = [post['id'] for post in posts]
        if len(ids) != len(set(ids)):
            raise ManagementError('Duplicate post IDs')
        matches = [post for post in posts if post['id'] == post_id]
        if len(matches) != 1:
            raise ManagementError('Post not found')
        matches[0]['approved'] = approved
    _edit_json(path, update)



def set_schedule(path, post_id, scheduled_at):
    try:
        timestamp = datetime.fromisoformat(scheduled_at)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError()
        canonical = timestamp.astimezone(ZoneInfo('Asia/Tokyo')).isoformat()
    except (TypeError, ValueError):
        raise ManagementError('Schedule must be an ISO timestamp with timezone') from None
    def update(document):
        posts = document['posts']
        ids = [post['id'] for post in posts]
        if len(ids) != len(set(ids)):
            raise ManagementError('Duplicate post IDs')
        matches = [post for post in posts if post['id'] == post_id]
        if len(matches) != 1:
            raise ManagementError('Post not found')
        matches[0]['scheduled_at'] = canonical
    _edit_json(path, update)


def set_auto_publish(path, enabled):
    if type(enabled) is not bool:
        raise ManagementError('Enabled must be a boolean')
    def update(document):
        document['auto_publish_enabled'] = enabled
    _edit_json(path, update)


def read_history(path):
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as database:
        database.row_factory = sqlite3.Row
        return [dict(row) for row in database.execute('SELECT * FROM posts')]


def require_loopback(address):
    if address not in ('127.0.0.1', '::1', 'localhost'):
        raise ManagementError('Dashboard requires an explicit loopback binding; public access is disabled')
