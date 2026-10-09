"""Scheduler: delayed Actions runs catch up within an explicit expiry window."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .core import SafeError, load_post, publish_post
from .storage import read_json

JST = ZoneInfo('Asia/Tokyo')


def parse_time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(JST)
    except (TypeError, ValueError, OverflowError):
        raise SafeError('scheduled_at must be an ISO datetime with explicit timezone, e.g. 2026-10-09T08:00:00+09:00') from None


def due_posts(posts, config, now=None, account='default'):
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    limits = config.get('schedule', {})
    max_age = float(limits.get('max_lateness_hours', 24))
    if not 0 < max_age <= 168:
        raise SafeError('max_lateness_hours must be within 0–168')
    result = []
    ids = set()
    for post in posts:
        if not isinstance(post, dict) or not isinstance(post.get('id'), str) or not post['id'] or post['id'] in ids:
            raise SafeError('Posts require unique nonempty string IDs')
        ids.add(post['id'])
        if post.get('account', 'default') != account or post.get('approved') is not True or not post.get('scheduled_at'):
            continue
        if 'post_type' in post:
            if post.get('publish_status') != 'scheduled':
                continue
            from .schema import normalize_post, approval_digest
            post = normalize_post(post)
            if post.get('approval_digest') != approval_digest(post):
                continue
        when = parse_time(post['scheduled_at'])
        if 'post_type' in post and config.get('posting_schedule'):
            slots = config['posting_schedule'].get('slots', {})
            bucket = 'image' if post['post_type'] in ('image', 'carousel') else post['post_type']
            valid = when.strftime('%H:%M') in slots.values() if config['posting_schedule'].get('format_mode')=='flexible' else slots.get(bucket)==when.strftime('%H:%M')
            if not valid or when.second or when.microsecond:
                continue
        if when <= now <= when + timedelta(hours=max_age):
            result.append(post)
    return sorted(result, key=lambda post: (parse_time(post['scheduled_at']), post['id']))


def run_scheduled(client, history, posts_path, config, enabled=False, auto_enabled=False, now=None, account='default'):
    posting_policy = config.get('posting_schedule')
    if not enabled or not auto_enabled or config.get('auto_publish_enabled') is not True or (
            posting_policy is not None and posting_policy.get('auto_publish_enabled') is not True):
        return {'published': 0, 'skipped': 0, 'disabled': True}
    if config.get('timezone') != 'Asia/Tokyo':
        raise SafeError('Scheduler timezone must be Asia/Tokyo')
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    data = read_json(posts_path)
    if not isinstance(data, dict) or not isinstance(data.get('posts'), list):
        raise SafeError('Invalid posts data')
    due = due_posts(data['posts'], config, now, account)
    if not due:
        return {'published': 0, 'skipped': 0, 'disabled': False}
    user_id = client.connect()['id']
    expected = config.get('accounts', {}).get(account, {}).get('expected_user_id')
    if expected and user_id != expected:
        raise SafeError('Token account does not match configured account')
    limits = config.get('schedule', {})
    daily = int(limits.get('max_posts_per_day', 3))
    per_run = int(limits.get('max_posts_per_run', 3))
    if not 1 <= daily <= 100 or not 1 <= per_run <= daily:
        raise SafeError('Invalid scheduler publication limits')
    # Count reservations, including uncertainty, to avoid exceeding the quota after failures.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = 0
    for (created,) in history.db.execute('SELECT created_at FROM posts WHERE account=?', (user_id,)):
        if created and start <= parse_time(created) < start + timedelta(days=1):
            used += 1
    # Per-format quotas use the requested JST day, even for delayed executions.
    # Pending/uncertain jobs count immediately; reservations carry metadata before API calls.
    typed_usage = {}
    columns = {row[1] for row in history.db.execute('PRAGMA table_info(posts)')}
    kind_column = 'post_type' if 'post_type' in columns else 'NULL'
    for scheduled, created, kind in history.db.execute(
            f'SELECT scheduled_at, created_at, {kind_column} FROM posts WHERE account=?', (user_id,)):
        instant = scheduled or created
        if not instant:
            continue
        day = parse_time(instant).date().isoformat()
        bucket = 'image' if kind in ('image', 'carousel') else (kind or 'text')
        if posting_policy and posting_policy.get('format_mode')=='flexible':bucket=parse_time(instant).strftime('%H:%M')
        typed_usage[(day, bucket)] = typed_usage.get((day, bucket), 0) + 1
    published = skipped = 0
    for post in due:
        if history.recorded(user_id, post['id']):
            skipped += 1
            continue
        if published >= min(per_run, max(0, daily - used)):
            break
        if 'post_type' in post:
            kind = post['post_type']
            if kind not in ('text', 'image', 'carousel', 'thread'):
                raise SafeError('Unknown scheduled post type')
            bucket = 'image' if kind in ('image', 'carousel') else kind
            if posting_policy and posting_policy.get('format_mode')=='flexible':bucket=parse_time(post['scheduled_at']).strftime('%H:%M')
            key = (parse_time(post['scheduled_at']).date().isoformat(), bucket)
            if typed_usage.get(key, 0) >= 1:
                skipped += 1
                continue
        else:
            key = None  # Preserve the existing untyped manual/legacy scheduling contract.
        post = load_post(posts_path, post['id'])
        publish_post(client, history, post, enabled=True, approved_id=post['id'])
        published += 1
        if key is not None:
            typed_usage[key] = typed_usage.get(key, 0) + 1
    return {'published': published, 'skipped': skipped, 'disabled': False}
