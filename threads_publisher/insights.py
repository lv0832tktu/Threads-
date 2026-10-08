"""Read-only Threads Insights collection; unavailable metrics remain unknown."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .core import SafeError, NoRedirect

METRICS = ('views', 'likes', 'replies', 'reposts', 'quotes')
CHECKPOINTS = {'24h': 24, '72h': 72, '7d': 168}


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


class InsightsClient:
    def __init__(self, token, transport=None, metrics=None):
        if not token:
            raise SafeError('THREADS_ACCESS_TOKEN is required')
        self.token = token
        self.metrics = tuple(metrics) if metrics is not None else METRICS
        if not self.metrics or len(self.metrics) > 10 or len(set(self.metrics)) != len(self.metrics) or any(not isinstance(m, str) or not re.fullmatch(r'[a-z_]{1,40}', m) for m in self.metrics):
            raise SafeError('Invalid Insights metric names')
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open

    def fetch(self, media_id):
        metrics, unavailable = {}, {}
        # Probe separately: a rejected metric must not discard supported metrics.
        for metric in self.metrics:
            url = 'https://graph.threads.net/v1.0/' + urllib.parse.quote(str(media_id), safe='') + '/insights?metric=' + metric
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token})
            try:
                with self.transport(request, timeout=30) as response:
                    payload = json.load(response)
                entries = payload.get('data', [])
                entry = next((e for e in entries if e.get('name') == metric), None)
                value = None
                if entry:
                    value = entry.get('total_value', {}).get('value')
                    if value is None and entry.get('values'):
                        value = entry['values'][-1].get('value')
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    metrics[metric] = value
                else:
                    unavailable[metric] = 'no_data'
            except urllib.error.HTTPError as exc:
                try:
                    code = json.loads(exc.read()).get('error', {}).get('code')
                except (ValueError, AttributeError):
                    code = None
                if exc.code == 401 or code == 190:
                    raise SafeError('Insights token expired or invalid; reauthorize') from None
                if exc.code == 429 or code in (4, 17, 32, 613):
                    raise SafeError('Insights rate limit reached; retry later') from None
                if exc.code in (400, 403):
                    unavailable[metric] = 'unsupported_or_permission_denied'
                else:
                    raise SafeError('Insights request failed; retry later') from None
            except (OSError, ValueError, AttributeError, TypeError):
                raise SafeError('Insights connection or response failed') from None
        return {'metrics': metrics, 'unavailable': unavailable}


    def metadata(self, media_id):
        url = 'https://graph.threads.net/v1.0/' + urllib.parse.quote(str(media_id), safe='') + '?fields=id,text,timestamp'
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token})
        try:
            with self.transport(request, timeout=30) as response:
                data = json.load(response)
            if str(data.get('id')) != str(media_id) or not isinstance(data.get('text'), str):
                raise ValueError()
            timestamp = instant(data['timestamp']).isoformat()
            return {'text': data['text'], 'published_at': timestamp}
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            raise SafeError('Legacy media metadata unavailable; check account permissions and token') from None


def read_snapshots(path):
    path = Path(path)
    if not path.exists():
        return {'schema_version': 1, 'snapshots': []}
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(result.get('snapshots'), list):
            raise ValueError()
        return result
    except (OSError, ValueError, AttributeError):
        raise SafeError('Invalid insights history; refusing overwrite') from None


def save_snapshots(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def collect(history, client, path, now=None, account=None, backfill=False):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SafeError('Collection time requires timezone')
    document = read_snapshots(path)
    keys = {(s['account'], s['remote_id'], s['checkpoint']) for s in document['snapshots']}
    columns = [r[1] for r in history.db.execute('PRAGMA table_info(posts)')]
    rows = [dict(zip(columns, row)) for row in history.db.execute("SELECT * FROM posts WHERE status='published'")]
    result = {'collected': 0, 'skipped': 0, 'errors': []}
    for row in rows:
        if account is not None and str(row['account']) != str(account):
            continue
        if backfill and not row.get('published_at') and row.get('remote_id'):
            try:
                metadata = client.metadata(row['remote_id'])
                history.db.execute('UPDATE posts SET published_at=?, text=? WHERE account=? AND post_id=?',
                                   (metadata['published_at'], metadata['text'], row['account'], row['post_id']))
                history.db.commit()
                row.update(metadata)
            except SafeError as exc:
                result['errors'].append({'post_id': row['post_id'], 'reason': str(exc)})
                continue
        try:
            published = instant(row.get('published_at') or '')
        except (ValueError, AttributeError):
            result['skipped'] += 1
            continue
        if not row.get('remote_id'):
            result['skipped'] += 1
            continue
        due = [name for name, hours in CHECKPOINTS.items() if now >= published + timedelta(hours=hours) and (row['account'], row['remote_id'], name) not in keys]
        if not due:
            continue
        # Delayed Actions cannot reconstruct historic metrics. Record actual age for each late checkpoint.
        try:
            data = client.fetch(row['remote_id'])
        except SafeError as exc:
            result['errors'].append({'post_id': row['post_id'], 'reason': str(exc)})
            continue
        if not data['metrics']:
            result['errors'].append({'post_id': row['post_id'], 'reason': 'No permitted metrics available'})
            continue
        for checkpoint in due:
            document['snapshots'].append({**data, 'account': row['account'], 'post_id': row['post_id'], 'remote_id': row['remote_id'], 'checkpoint': checkpoint, 'published_at': published.isoformat(), 'collected_at': now.isoformat(), 'actual_age_hours': (now-published).total_seconds()/3600, 'late': now > published + timedelta(hours=CHECKPOINTS[checkpoint]+1), 'text': row.get('text') or '', 'topic': row.get('topic') or '', 'variant': row.get('variant') or 'baseline', 'scheduled_at': row.get('scheduled_at')})
            keys.add((row['account'], row['remote_id'], checkpoint))
            result['collected'] += 1
        save_snapshots(path, document)
    return result
