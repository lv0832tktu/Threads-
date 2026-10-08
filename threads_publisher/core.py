import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path


class SafeError(Exception):
    pass


class Client:
    def __init__(self, token, transport=None):
        if not token:
            raise SafeError('THREADS_ACCESS_TOKEN is required')
        self.token = token
        self.transport = transport or urllib.request.urlopen

    def request(self, path, payload=None):
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
            raise SafeError(f'API request failed (HTTP {error.code}); inspect permissions and retry connection check') from None
        except (OSError, ValueError):
            raise SafeError('API connection or response failed; publication may be uncertain') from None
        if not isinstance(result, dict) or not result.get('id'):
            raise SafeError('API returned an unexpected response')
        return result

    def connect(self):
        return self.request('me?fields=id,username')

    def create(self, account, text):
        return self.request(account + '/threads', {'media_type': 'TEXT', 'text': text})['id']

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
        self.db.commit()
        self.persist = persist

    def reserve(self, account, post_id):
        try:
            self.db.execute('INSERT INTO posts VALUES (?, ?, ?, NULL)', (account, post_id, 'pending'))
            self.db.commit()
        except sqlite3.IntegrityError:
            raise SafeError('Post already recorded or pending; refusing duplicate publication') from None
        # Durable reservation MUST succeed before any publication operation.
        self.persist()

    def finish(self, account, post_id, remote_id):
        self.db.execute('UPDATE posts SET status=?, remote_id=? WHERE account=? AND post_id=?',
                        ('published', remote_id, account, post_id))
        self.db.commit()
        self.persist()


def publish_post(client, history, post, enabled=False, approved_id=''):
    if not enabled or approved_id != post['id'] or post.get('approved') is not True:
        raise SafeError('Publication disabled or explicit approval missing')
    account = client.connect()['id']
    history.reserve(account, post['id'])
    container = client.create(account, post['text'])
    remote_id = client.publish(account, container)
    history.finish(account, post['id'], remote_id)
    return remote_id
