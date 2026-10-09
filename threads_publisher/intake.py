"""External draft intake. Approval and scheduling are independent human actions."""
import csv
import json
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError
from .management import _edit_json
from .schema import normalize_post, timestamp, approval_digest


def _records(path):
    try:
        path = Path(path)
        if path.suffix.lower() == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as stream:
                records = list(csv.DictReader(stream))
            for record in records:
                for key in ('image_urls', 'thread_items', 'keywords', 'sources'):
                    if record.get(key):
                        record[key] = json.loads(record[key])
                    elif key in record:
                        record.pop(key)
                for key in ('approved', 'rights_confirmed'):
                    if key in record:
                        value = record[key].lower()
                        if value not in ('true', 'false', ''):
                            raise ValueError()
                        record[key] = value == 'true'
            return records
        value = json.loads(path.read_text(encoding='utf-8'))
        return value['posts'] if isinstance(value, dict) else value
    except (OSError, ValueError, KeyError, TypeError):
        raise SafeError('Invalid JSON/CSV intake file') from None


def import_batch(source_path, posts_path, schedule_path='config/posting_schedule.json', start_date=None, require_21=True, text_only=False):
    records = _records(source_path)
    if not isinstance(records, list) or not records:
        raise SafeError('Intake must contain posts')
    try:
        schedule = json.loads(Path(schedule_path).read_text(encoding='utf-8'))
        if schedule['timezone'] != 'Asia/Tokyo':
            raise ValueError()
        slots = {kind: datetime.strptime(schedule['slots'][kind], '%H:%M').time()
                 for kind in ('text', 'image', 'thread')}
        beginning = date.fromisoformat(start_date) if start_date else datetime.now(ZoneInfo('Asia/Tokyo')).date()
    except (OSError, KeyError, TypeError, ValueError):
        raise SafeError('Invalid posting schedule or start date') from None
    counts = {'text': 0, 'image': 0, 'thread': 0}
    imported = []
    for record in records:
        # Never trust imported approval or an externally supplied active schedule.
        if not isinstance(record, dict):
            raise SafeError('Each imported post must be an object')
        raw = dict(record)
        raw['approved'] = False
        raw['approval_status'] = 'pending_approval'
        raw['publish_status'] = 'pending_approval'
        raw.pop('approval_digest', None)
        if raw.get('publish_datetime') and raw.get('scheduled_at') and timestamp(raw['publish_datetime']) != timestamp(raw['scheduled_at']):
            raise SafeError('Conflicting publish times; batch not imported')
        planned = raw.pop('publish_datetime', None) or raw.pop('scheduled_at', None) or raw.get('planned_at')
        raw.pop('scheduled_at', None)
        normalized = normalize_post(raw)
        bucket = 'image' if normalized['post_type'] == 'carousel' else normalized['post_type']
        if planned:
            normalized['planned_at'] = timestamp(planned)
        else:
            day = beginning + timedelta(days=counts[bucket]//3 if text_only else counts[bucket])
            clock = list(slots.values())[counts[bucket]%3] if text_only else slots[bucket]
            normalized['planned_at'] = datetime.combine(day, clock, ZoneInfo('Asia/Tokyo')).isoformat()
        counts[bucket] += 1
        normalized['review_status'] = 'pending'
        imported.append(normalized)
    if require_21 and counts != ({'text':21,'image':0,'thread':0} if text_only else {'text':7,'image':7,'thread':7}):
        raise SafeError('Weekly batch requires 7 text, 7 image/carousel, and 7 thread drafts')
    if require_21:
        dated = {}
        for post in imported:
            when = datetime.fromisoformat(post['planned_at'])
            bucket = 'image' if post['post_type'] == 'carousel' else post['post_type']
            if (when.time() not in slots.values() if text_only else when.time() != slots[bucket]):
                raise SafeError('Weekly planned times must match configured slots')
            key = (when.date(), when.time() if text_only else bucket)
            if key in dated:
                raise SafeError('Weekly batch requires one post per category per day')
            dated[key] = True
        dates = sorted({day for day, _ in dated})
        if len(dates) != 7 or dates != [dates[0] + timedelta(days=i) for i in range(7)]:
            raise SafeError('Weekly batch requires seven consecutive days')
    def merge(document):
        existing = document['posts']
        combined = existing + imported
        ids = [p['id'] for p in combined]
        if len(ids) != len(set(ids)):
            raise SafeError('Duplicate post id; batch not imported')
        fingerprints = [json.dumps({'text':p.get('text'), 'thread_items':p.get('thread_items')}, ensure_ascii=False, sort_keys=True) for p in combined]
        if len(fingerprints) != len(set(fingerprints)):
            raise SafeError('Duplicate post content; batch not imported')
        times = [(p.get('account', 'default'),p.get('planned_at') or p.get('scheduled_at')) for p in combined if p.get('planned_at') or p.get('scheduled_at')]
        if len(times) != len(set(times)):
            raise SafeError('Duplicate planned slot; batch not imported')
        document['posts'] = combined
    _edit_json(posts_path, merge)
    return [p['id'] for p in imported]


def _find(document, post_id):
    matches = [p for p in document['posts'] if p['id'] == post_id]
    if len(matches) != 1:
        raise SafeError('Post not found or duplicate id')
    return matches[0]


def approve_record(posts_path, post_id, approved=True):
    if type(approved) is not bool:
        raise SafeError('Approval must be boolean')
    def change(document):
        post = _find(document, post_id)
        normalize_post(post)
        if approved and post.get('post_type') in ('image', 'carousel') and post.get('rights_confirmed') is not True:
            raise SafeError('Confirm image rights before approval')
        if approved and post.get('post_type') in ('image', 'carousel') and not post.get('image_sha256'):
            raise SafeError('Bind verified image SHA256 hashes before approval')
        post['approved'] = approved
        post['approval_status'] = 'approved' if approved else 'pending_approval'
        post['publish_status'] = 'approved' if approved else 'pending_approval'
        if approved:
            post['approval_digest'] = approval_digest(post)
        else:
            post.pop('approval_digest', None)
        post['review_status'] = 'approved' if approved else 'pending'
        if not approved:
            post.pop('scheduled_at', None)
    _edit_json(posts_path, change)


def schedule_record(posts_path, post_id, publish_datetime=None):
    def change(document):
        post = _find(document, post_id)
        if post.get('approved') is not True:
            raise SafeError('Approve post before scheduling')
        if post.get('approval_digest') != approval_digest(post):
            raise SafeError('Post changed since approval; approve it again')
        when = timestamp(publish_datetime or post.get('planned_at'))
        if any(p['id'] != post_id and p.get('account','default') == post.get('account','default') and
               p.get('scheduled_at') == when for p in document['posts']):
            raise SafeError('Scheduled slot already occupied')
        normalized = normalize_post({**post, 'scheduled_at': when})
        post.update(normalized)
        post['publish_status'] = 'scheduled'
    _edit_json(posts_path, change)


def edit_record(posts_path, post_id, changes):
    if not isinstance(changes, dict) or any(key in changes for key in ('id','post_id','approved','approval_status','scheduled_at','publish_datetime')):
        raise SafeError('Edit cannot change identity, approval, or active scheduling')
    def change(document):
        post = _find(document, post_id)
        candidate = {**post, **changes, 'approved': False, 'approval_status': 'pending_approval', 'publish_status':'pending_approval', 'review_status':'pending'}
        candidate.pop('scheduled_at', None)
        candidate.pop('approval_digest', None)
        post.clear()
        post.update(normalize_post(candidate))
    _edit_json(posts_path, change)
