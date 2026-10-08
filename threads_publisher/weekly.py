"""Import ChatGPT-authored weekly drafts locally; no AI/network calls."""
import copy
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError
from .editor import normalize
from .management import _edit_json
from .storage import atomic_json, read_json

SLOTS = ('08:00', '19:00', '22:00')


def private_path(path):
    root = (Path.cwd() / 'private').resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root) or target == root:
        raise SafeError('Weekly drafts must stay in the ignored private directory')
    return target


def import_week(source, target, start_date, account='default'):
    source, target = private_path(source), private_path(target)
    try:
        day = date.fromisoformat(start_date)
        rows = read_json(source)['posts']
        if not isinstance(rows, list) or len(rows) != 21:
            raise ValueError()
        prepared = []
        seen_ids, seen_text = set(), set()
        for index, item in enumerate(rows):
            if isinstance(item, str):
                text, post_id = item, f'weekly-{account}-{day.isoformat()}-{index+1:02}'
            else:
                text, post_id = item['text'], item.get('id') or f'weekly-{account}-{day.isoformat()}-{index+1:02}'
            if not isinstance(text, str) or not text.strip() or len(text) > 500 or not isinstance(post_id, str) or not post_id:
                raise ValueError()
            normalized = normalize(text)
            if post_id in seen_ids or normalized in seen_text:
                raise ValueError()
            seen_ids.add(post_id); seen_text.add(normalized)
            when = datetime.combine(day + timedelta(days=index//3), datetime.strptime(SLOTS[index%3], '%H:%M').time(), ZoneInfo('Asia/Tokyo'))
            prepared.append({'id':post_id,'text':text,'approved':False,'account':account,
                             'scheduled_at':when.isoformat(),'origin':'chatgpt-plus','variant':'baseline'})
    except (ValueError, KeyError, TypeError):
        raise SafeError('Week import requires 21 unique posts, text 1–500 characters and YYYY-MM-DD start date') from None
    if not target.exists():
        atomic_json(target, {'posts':[]})
    count = {'added':0,'unchanged':0}
    def update(document):
        existing = document['posts']
        baseline = read_json('posts/posts.json')['posts'] if Path('posts/posts.json').exists() else []
        if any(post['id'] == old['id'] or normalize(post['text']) == normalize(old['text']) for post in prepared for old in baseline):
            raise SafeError('Weekly import duplicates a legacy post')
        ids = {p['id']:p for p in existing}
        texts = {normalize(p['text']):p['id'] for p in existing}
        times = {(p.get('account','default'), p.get('scheduled_at')) for p in existing if p.get('scheduled_at')}
        if len(ids) != len(existing):
            raise SafeError('Existing private queue has duplicate IDs')
        for post in prepared:
            if post['id'] in ids:
                old = ids[post['id']]
                if any(old.get(field) != post.get(field) for field in ('text','account','scheduled_at')):
                    raise SafeError('Existing ID conflicts with imported post; refusing overwrite')
                count['unchanged'] += 1
                continue
            if normalize(post['text']) in texts or (account,post['scheduled_at']) in times:
                raise SafeError('Duplicate text or occupied weekly time slot')
            existing.append(post)
            ids[post['id']] = post
            texts[normalize(post['text'])] = post['id']
            times.add((account,post['scheduled_at']))
            count['added'] += 1
    _edit_json(target, update)
    return count


def edit_text(path, post_id, text):
    path=private_path(path)
    if not isinstance(text,str) or not text.strip() or len(text)>500:
        raise SafeError('Text must contain 1–500 characters')
    def update(document):
        matches=[p for p in document['posts'] if p['id']==post_id]
        if len(matches)!=1 or any(p['id']!=post_id and normalize(p['text'])==normalize(text) for p in document['posts']):
            raise SafeError('Missing ID or duplicate edited text')
        matches[0]['text']=text
        matches[0]['approved']=False
    _edit_json(path,update)


def export_approved(source, target):
    source,target=private_path(source),private_path(target)
    posts=read_json(source)['posts']
    selected=[copy.deepcopy(post) for post in posts if post.get('approved') is True]
    ids=[p['id'] for p in selected]
    texts=[normalize(p['text']) for p in selected]
    if len(ids)!=len(set(ids)) or len(texts)!=len(set(texts)):
        raise SafeError('Duplicate approved queue')
    # GitHub Secret limit is 48 KB; leave margin for encoding/transport.
    import json
    data={'posts':selected}
    if len(json.dumps(data,ensure_ascii=False).encode())>45000:
        raise SafeError('Approved queue exceeds GitHub Secret size limit')
    atomic_json(target,data)
    return {'approved':len(selected)}
