import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone


class SafeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafeError('API redirect refused; verify endpoint')


class Client:
    def __init__(self, token, transport=None, media_hosts=None):
        if not token:
            raise SafeError('THREADS_ACCESS_TOKEN is required')
        self.media_hosts = media_hosts or []
        self.token = token
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open

    def request(self, path, payload=None, require_id=True):
        request = urllib.request.Request(
            'https://graph.threads.net/v1.0/' + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json'})
        try:
            with self.transport(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # Never log raw response bodies, URLs, headers, or exception strings.
            try:
                code = json.loads(error.read()).get('error', {}).get('code')
            except (ValueError, AttributeError):
                code = None
            if code == 190 or error.code == 401:
                raise SafeError('Token expired or invalid; reauthorize and update GitHub Secret') from None
            if error.code == 429 or code in (4, 17, 32, 613):
                raise SafeError('API rate limit reached; wait before another read; do not retry uncertain publication') from None
            raise SafeError(f'API request failed (HTTP {error.code}); inspect permissions and retry connection check') from None
        except (OSError, ValueError):
            raise SafeError('API connection or response failed; publication may be uncertain') from None
        if not isinstance(result, dict) or (require_id and not result.get('id')):
            raise SafeError('API returned an unexpected response')
        return result

    def connect(self):
        return self.request('me?fields=id,username')

    def create(self, account, text=None, **payload):
        payload.setdefault('media_type', 'TEXT')
        if text is not None:
            payload['text'] = text
        return self.request(account + '/threads', payload)['id']

    def container_status(self, container):
        return self.request(container + '?fields=id,status,error_message', require_id=False).get('status')

    def publish(self, account, container):
        return self.request(account + '/threads_publish', {'creation_id': container})['id']


def load_post(path, post_id):
    try:
        posts = json.loads(Path(path).read_text(encoding='utf-8'))['posts']
        ids = [p['id'] for p in posts]
        if len(ids) != len(set(ids)):
            raise ValueError()
        post = next(p for p in posts if p['id'] == post_id)
        if not isinstance(post['text'], str) or not 1 <= len(post['text']) <= 500:
            raise ValueError()
        return post
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        raise SafeError('Invalid posts JSON, duplicate ID, missing post, or text outside 1–500 characters') from None


class History:
    def __init__(self, path, persist=lambda: None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS posts (account TEXT, post_id TEXT, status TEXT, remote_id TEXT, PRIMARY KEY(account, post_id))')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(posts)')}
        for name in ('created_at', 'published_at', 'text', 'scheduled_at', 'topic', 'variant', 'error_kind', 'post_type', 'category', 'keywords', 'remote_ids', 'payload_digest', 'job_status'):
            if name not in columns:
                self.db.execute(f'ALTER TABLE posts ADD COLUMN {name} TEXT')
        self.db.execute('''CREATE TABLE IF NOT EXISTS post_parts (account TEXT, post_id TEXT, part_index INTEGER, status TEXT, container_id TEXT, remote_id TEXT, reply_to_id TEXT, published_at TEXT, text TEXT, PRIMARY KEY(account,post_id,part_index))''')
        self.db.commit()
        self.persist = persist

    def reserve(self, account, post_id, post=None):
        try:
            metadata = post or {}
            self.db.execute('''INSERT INTO posts
                (account, post_id, status, created_at, text, scheduled_at, topic, variant)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (account, post_id, 'pending', datetime.now(timezone.utc).isoformat(),
                 None, metadata.get('scheduled_at'), None, None))
            self.db.execute('UPDATE posts SET post_type=?,job_status=? WHERE account=? AND post_id=?',
                            (metadata.get('post_type','text'),'posting',account,post_id))
            self.db.commit()
        except sqlite3.IntegrityError:
            raise SafeError('Post already recorded or pending; refusing duplicate publication') from None
        # Durable reservation MUST succeed before any publication operation.
        self.persist()

    def finish(self, account, post_id, remote_id, post=None):
        self.db.execute('UPDATE posts SET status=?, remote_id=?, published_at=? WHERE account=? AND post_id=?',
                        ('published', remote_id, datetime.now(timezone.utc).isoformat(), account, post_id))
        if post:
            self.db.execute('UPDATE posts SET text=?, scheduled_at=?, topic=?, variant=? WHERE account=? AND post_id=?',
                            (post.get('text'), post.get('scheduled_at'), post.get('topic'), post.get('variant'), account, post_id))
        metadata = post or {}
        self.db.execute('UPDATE posts SET job_status=?,post_type=?,category=?,keywords=?,remote_ids=? WHERE account=? AND post_id=?',
            ('posted',metadata.get('post_type','text'),metadata.get('category'),json.dumps(metadata.get('keywords',[]),ensure_ascii=False),json.dumps(metadata.get('remote_ids',[remote_id])),account,post_id))
        self.db.commit()
        self.persist()

    def recorded(self, account, post_id):
        return self.db.execute('SELECT 1 FROM posts WHERE account=? AND post_id=?', (account, post_id)).fetchone() is not None

    def mark_uncertain(self, account, post_id):
        self.db.execute('UPDATE posts SET error_kind=?,job_status=? WHERE account=? AND post_id=?',
                        ('publication_failed_or_uncertain', 'failed', account, post_id))
        self.db.commit()
        self.persist()


def publish_post(client, history, post, enabled=False, approved_id=''):
    if 'post_type' in post:
        from .publisher import publish_content
        return publish_content(client, history, post, enabled, approved_id)
    if not enabled or approved_id != post['id'] or post.get('approved') is not True:
        raise SafeError('Publication disabled or explicit approval missing')
    account = client.connect()['id']
    history.reserve(account, post['id'], post)
    try:
        container = client.create(account, post['text'])
        remote_id = client.publish(account, container)
    except SafeError:
        history.mark_uncertain(account, post['id'])
        raise
    history.finish(account, post['id'], remote_id, post)
    return remote_id
