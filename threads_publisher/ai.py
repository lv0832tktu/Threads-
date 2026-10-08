"""Draft generation with durable request reservations and atomic JSON writes."""
import json
import fcntl
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError
from .director import load_config
from .editor import edit_draft
from .research import research_context
from .writer import Writer


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _generate_drafts(config_path, posts_path, usage_path, persist=lambda: None,
                    writer=None, now=None, sources=None, improvement=None):
    config = load_config(config_path)
    ai = config['ai']
    if not ai['enabled']:
        return {'status': 'disabled', 'generated': 0}
    if not ai.get('theme') or not ai.get('audience'):
        raise SafeError('Set AI theme and audience before generation')
    writer = writer or Writer(ai, os.environ.get(ai['api_key_env'], ''))
    now = (now or datetime.now(ZoneInfo('Asia/Tokyo'))).astimezone(ZoneInfo('Asia/Tokyo'))
    day, month = now.date().isoformat(), now.strftime('%Y-%m')
    try:
        document = json.loads(Path(posts_path).read_text(encoding='utf-8'))
        posts = document['posts']
        if not isinstance(posts, list) or any(not isinstance(p.get('text'), str) for p in posts):
            raise ValueError()
        if len({p['id'] for p in posts}) != len(posts):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SafeError('Invalid posts file') from None
    Path(usage_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(usage_path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS ai_usage (id TEXT PRIMARY KEY, day TEXT, month TEXT, status TEXT, post_id TEXT)')
    db.commit()
    generated = 0
    try:
        slots = sorted(datetime.strptime(value, '%H:%M').time() for value in config['daily_slots'])
        if not slots or len(slots) != len(set(slots)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        db.close()
        raise SafeError('Invalid daily scheduling slots') from None
    research = research_context(sources)
    if improvement is not None and not isinstance(improvement, dict):
        db.close()
        raise SafeError('Improvement guidance must be an object')
    try:
        for _ in range(ai['daily_drafts']):
            db.execute('BEGIN IMMEDIATE')
            daily = db.execute('SELECT COUNT(*) FROM ai_usage WHERE day=?', (day,)).fetchone()[0]
            monthly = db.execute('SELECT COUNT(*) FROM ai_usage WHERE month=?', (month,)).fetchone()[0]
            if daily >= min(ai['daily_drafts'], ai['daily_request_limit']) or monthly >= ai['monthly_request_limit']:
                db.rollback()
                break
            reservation = str(uuid.uuid4())
            db.execute('INSERT INTO ai_usage VALUES (?, ?, ?, ?, NULL)', (reservation, day, month, 'reserved'))
            db.commit()
            persist()  # Failure stops before any potentially billable API call.
            guidance = {}
            if improvement:
                guidance = {'experiment_id': improvement.get('experiment_id', 'baseline'),
                            'suggestions': [str(item)[:500] for item in improvement.get('suggestions', [])[:8]],
                            'advisory_only': True}
            context = {'theme': ai['theme'], 'audience': ai['audience'], 'tone': ai['tone'],
                       'max_characters': ai['max_text_chars'], 'avoid_recent': [p['text'][:500] for p in posts[-15:]],
                       'research': research, 'improvement': guidance}
            try:
                candidate = writer.generate(context)
                text = edit_draft(candidate, posts, ai['max_text_chars'], ai['duplicate_threshold'])
                post_id = 'ai-' + day + '-' + uuid.uuid4().hex[:12]
                scheduled = next_slot(now, slots, posts, 'default')
                experiment = (improvement or {}).get('experiment_id') if isinstance(improvement, dict) else None
                posts.append({'id': post_id, 'text': text, 'approved': False,
                              'account': 'default', 'scheduled_at': scheduled.isoformat(),
                              'topic': ai['theme'], 'variant': experiment,
                              'created_at': now.isoformat(), 'origin': 'ai',
                              'review_status': 'pending', 'theme': ai['theme'],
                              'research': context['research'], 'generation_id': reservation})
                atomic_json(posts_path, document)
                db.execute('UPDATE ai_usage SET status=?, post_id=? WHERE id=?', ('draft', post_id, reservation))
                db.commit()
                persist()
                generated += 1
            except SafeError:
                db.execute('UPDATE ai_usage SET status=? WHERE id=?', ('failed', reservation))
                db.commit()
                persist()
                raise
        return {'status': 'ok', 'generated': generated}
    finally:
        db.close()


def generate_drafts(config_path, posts_path, usage_path, persist=lambda: None,
                    writer=None, now=None, sources=None, improvement=None):
    # Shared with management JSON edits: prevent lost approvals/drafts.
    path = Path(posts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _generate_drafts(config_path, posts_path, usage_path, persist,
                                writer, now, sources, improvement)


def next_slot(now, slots, posts, account):
    occupied = set()
    for post in posts:
        if post.get('account', 'default') != account or not post.get('scheduled_at'):
            continue
        try:
            existing = datetime.fromisoformat(post['scheduled_at'])
            if existing.tzinfo is None:
                raise ValueError()
            occupied.add(existing.astimezone(ZoneInfo('Asia/Tokyo')))
        except (ValueError, TypeError):
            raise SafeError('Invalid existing scheduled_at') from None
    for offset in range(366):
        day = now.date() + timedelta(days=offset)
        for slot in slots:
            candidate = datetime.combine(day, slot, tzinfo=ZoneInfo('Asia/Tokyo'))
            if candidate > now and candidate not in occupied:
                return candidate
    raise SafeError('No scheduling slots available in next 366 days')
