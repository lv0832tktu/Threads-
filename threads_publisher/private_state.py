"""Authenticated encrypted durable state. Keys never enter Git or logs."""
import base64
import json
import os
import shutil
from pathlib import Path
from .core import SafeError
from .storage import atomic_json

FILES=('history.sqlite3','insights.json','weekly-report.md','improvement.json','report-status.json','market-data.json','market-report.md','keyword-history.json','keyword-analysis.json','followers.json','manual-public-observations.json','trend-history.json','trend-data.json')


class PrivateState:
    def __init__(self, root='private', encrypted='state/private-state.enc', key=None):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise SafeError('Private state dependency missing; install requirements-private.txt with the Python interpreter running this command') from None
        value=key or os.environ.get('THREADS_STATE_KEY','')
        if not value:
            raise SafeError('THREADS_STATE_KEY is missing or unavailable; configure the GitHub Actions repository Secret with this exact name')
        try:
            self.cipher=Fernet(value.encode())
        except (ValueError, TypeError, AttributeError):
            raise SafeError('THREADS_STATE_KEY is invalid; use the existing Fernet key without surrounding quotes or extra whitespace; do not replace a key used by encrypted history') from None
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


def validated_queue(posts):
    """Normalize typed queue, binding human approval to reviewed content."""
    from .schema import normalize_post, approval_digest
    if not isinstance(posts, list):
        raise SafeError('Approved queue must contain posts')
    prepared = []; ids = set(); fingerprints = set()
    for raw in posts:
        post = normalize_post(raw)
        if post.get('approved') is not True or not post.get('scheduled_at'):
            raise SafeError('Only approved scheduled posts may enter the queue')
        if post.get('post_type') in ('image','carousel') and post.get('rights_confirmed') is not True:
            raise SafeError('Image rights must be confirmed')
        # Typed content requires approval binding; legacy text remains compatible.
        if ('post_type' in raw or post.get('approval_digest')) and post.get('approval_digest') != approval_digest(post):
            raise SafeError('Content approval digest is missing or changed')
        fingerprint=json.dumps({key:post.get(key) for key in ('text','thread_items','image_urls')},ensure_ascii=False,sort_keys=True)
        if post['id'] in ids or fingerprint in fingerprints:
            raise SafeError('Duplicate approved queue')
        if 'post_type' not in raw and not raw.get('approval_digest'):
            post.pop('post_type', None)  # Preserve legacy dispatch without inventing human approval.
        ids.add(post['id']); fingerprints.add(fingerprint); prepared.append(post)
    return prepared


def materialize_secret_queue(target='private/approved-posts.json'):
    from .weekly import private_path
    try:
        posts=[]
        for suffix in ('','_2','_3','_4'):
            value=os.environ.get('THREADS_SCHEDULE_JSON'+suffix)
            if value:
                document=json.loads(value)
                if not isinstance(document['posts'],list): raise ValueError()
                posts.extend(document['posts'])
        if not posts: raise ValueError()
        prepared=validated_queue(posts)
        atomic_json(private_path(target),{'posts':prepared})
        return len(prepared)
    except (ValueError, TypeError, KeyError, SafeError):
        raise SafeError('Approved schedule Secret is missing or invalid; no queue logged or published') from None


def export_queue(source, target, limit=45000):
    from .weekly import private_path
    from .storage import read_json
    source,target=private_path(source),private_path(target)
    selected=[post for post in read_json(source)['posts'] if post.get('approved') is True and post.get('scheduled_at')]
    prepared=validated_queue(selected)
    chunks=[]; current=[]
    for post in prepared:
        candidate=current+[post]
        if len((json.dumps({'posts':candidate},ensure_ascii=False,indent=2)+'\n').encode())>limit:
            if not current: raise SafeError('A post exceeds the GitHub Secret size limit')
            chunks.append(current); current=[post]
            if len((json.dumps({'posts':current},ensure_ascii=False,indent=2)+'\n').encode())>limit:
                raise SafeError('A post exceeds the GitHub Secret size limit')
        else: current=candidate
    if current or not chunks: chunks.append(current)
    if len(chunks)>4: raise SafeError('Queue exceeds four GitHub Secret chunks; shorten the batch')
    for index,chunk in enumerate(chunks):
        path=target if index==0 else target.with_name(target.stem+f'-{index+1}'+target.suffix)
        atomic_json(path,{'posts':chunk})
    # Clear stale optional chunks so a later smaller export cannot accidentally
    # reuse old approved content when the operator copies each output.
    for index in range(len(chunks),4):
        path=target.with_name(target.stem+f'-{index+1}'+target.suffix)
        atomic_json(path,{'posts':[]})
    return {'approved':len(prepared),'chunks':len(chunks)}
