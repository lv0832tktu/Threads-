"""Authenticated encrypted durable state. Keys never enter Git or logs."""
import base64
import json
import os
import shutil
from pathlib import Path
from .core import SafeError
from .storage import atomic_json

FILES=('history.sqlite3','insights.json','weekly-report.md','improvement.json','report-status.json')


class PrivateState:
    def __init__(self, root='private', encrypted='state/private-state.enc', key=None):
        try:
            from cryptography.fernet import Fernet
            self.cipher=Fernet((key or os.environ.get('THREADS_STATE_KEY','')).encode())
        except (ImportError, ValueError, TypeError):
            raise SafeError('Private state needs the free cryptography dependency and THREADS_STATE_KEY Secret') from None
        self.root,self.encrypted=Path(root),Path(encrypted)

    def restore(self, legacy='state/history.sqlite3'):
        self.root.mkdir(parents=True,exist_ok=True)
        if self.encrypted.exists():
            try:
                document=json.loads(self.cipher.decrypt(self.encrypted.read_bytes()))
                if document.get('version')!=1 or any(name not in FILES for name in document['files']):
                    raise ValueError()
                decoded={name:base64.b64decode(value,validate=True) for name,value in document['files'].items()}
                if 'history.sqlite3' not in decoded:
                    raise ValueError()
            except Exception:
                raise SafeError('Encrypted state authentication failed; do not reset history or change key') from None
            for name,data in decoded.items():
                self._write(self.root/name,data)
        elif not (self.root/'history.sqlite3').exists() and Path(legacy).exists():
            shutil.copyfile(legacy,self.root/'history.sqlite3')

        # Incorporate newly recorded legacy manual publications without exposing private state.
        if Path(legacy).exists() and (self.root/'history.sqlite3').exists():
            import sqlite3
            from .core import History
            with sqlite3.connect(Path(legacy).resolve().as_uri()+'?mode=ro',uri=True) as source:
                source.row_factory=sqlite3.Row
                old_rows=[dict(row) for row in source.execute('SELECT * FROM posts')]
            history=History(self.root/'history.sqlite3')
            try:
                columns={row[1] for row in history.db.execute('PRAGMA table_info(posts)')}
                for row in old_rows:
                    found=history.db.execute('SELECT status,remote_id FROM posts WHERE account=? AND post_id=?',
                                             (row['account'],row['post_id'])).fetchone()
                    if found:
                        if found[1] and row.get('remote_id') and found[1]!=row['remote_id']:
                            raise SafeError('Conflicting private and legacy publication history; manual investigation required')
                        if found[0]!='published' and row['status']=='published':
                            history.db.execute('UPDATE posts SET status=?,remote_id=? WHERE account=? AND post_id=?',
                                               ('published',row.get('remote_id'),row['account'],row['post_id']))
                    else:
                        names=[name for name in row if name in columns]
                        history.db.execute('INSERT INTO posts ('+','.join(names)+') VALUES ('+','.join('?' for _ in names)+')',
                                           [row[name] for name in names])
                history.db.commit()
            finally:
                history.db.close()

    @staticmethod
    def _write(path,data):
        import tempfile
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name): os.unlink(name)

    def save(self):
        files={name:base64.b64encode((self.root/name).read_bytes()).decode() for name in FILES if (self.root/name).exists()}
        if 'history.sqlite3' not in files:
            raise SafeError('Private state must preserve publication history')
        plaintext=json.dumps({'version':1,'files':files}).encode()
        if self.encrypted.exists():
            try:
                if self.cipher.decrypt(self.encrypted.read_bytes()) == plaintext:
                    return
            except Exception:
                raise SafeError('Existing encrypted state cannot be verified; refusing overwrite') from None
        self._write(self.encrypted,self.cipher.encrypt(plaintext))


def materialize_secret_queue(target='private/approved-posts.json'):
    # Never print the JSON or write it into the checkout's tracked posts directory.
    try:
        document=json.loads(os.environ.get('THREADS_SCHEDULE_JSON',''))
        posts=document['posts']
        if not isinstance(posts,list): raise ValueError()
        ids=set(); texts=set()
        from .editor import normalize
        from .scheduler import parse_time
        for post in posts:
            if post.get('approved') is not True or not isinstance(post.get('id'),str) or not post['id']:
                raise ValueError()
            text=post['text']
            if not isinstance(text,str) or not 1<=len(text)<=500: raise ValueError()
            if post['id'] in ids or normalize(text) in texts: raise ValueError()
            parse_time(post['scheduled_at'])
            ids.add(post['id']); texts.add(normalize(text))
        atomic_json(target,document)
        return len(posts)
    except (ValueError, TypeError, KeyError, SafeError):
        raise SafeError('Approved schedule Secret is missing or invalid; no queue logged or published') from None
