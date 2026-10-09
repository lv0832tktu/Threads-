"""Validated multiformat post schema; validation makes no API requests."""
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from .core import SafeError


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(ZoneInfo('Asia/Tokyo')).isoformat()
    except (ValueError, TypeError, AttributeError):
        raise SafeError('Post datetime must include timezone') from None


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise SafeError('Post text must contain 1–500 characters')
    return value


def public_image_url(value):
    try:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        import ipaddress
        host = parsed.hostname.lower()
        if host in ('localhost',) or host.endswith(('.local', '.localhost')):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        if parsed.port not in (None, 443):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise SafeError('Images require public HTTPS URLs without credentials') from None


def normalize_post(post, default_datetime=None):
    if not isinstance(post, dict):
        raise SafeError('Post must be an object')
    result = deepcopy(post)
    if result.get('id') and result.get('post_id') and result['id'] != result['post_id']:
        raise SafeError('Conflicting post identifiers')
    result['id'] = result.get('id') or result.get('post_id')
    if not isinstance(result['id'], str) or not result['id'].strip() or len(result['id']) > 120:
        raise SafeError('Post requires a stable id')
    kind = result.setdefault('post_type', 'text')
    if kind not in ('text', 'image', 'carousel', 'thread'):
        raise SafeError('Unsupported post_type')
    result['topic'] = result.get('topic', result.get('theme', ''))
    result.setdefault('category', '')
    result.setdefault('keywords', [])
    if any(not isinstance(result[key], str) for key in ('category', 'topic')):
        raise SafeError('category and topic must be strings')
    if not isinstance(result['keywords'], list) or len(result['keywords']) > 30 or any(not isinstance(v, str) or len(v) > 100 for v in result['keywords']):
        raise SafeError('keywords must be a string list')
    if 'approved' not in result:
        result['approved'] = result.get('approval_status') == 'approved'
    if type(result['approved']) is not bool:
        raise SafeError('approved must be a boolean')
    if result.get('scheduled_at') and result.get('publish_datetime'):
        if timestamp(result['scheduled_at']) != timestamp(result['publish_datetime']):
            raise SafeError('Conflicting publish times')
    scheduled = result.get('scheduled_at') or result.get('publish_datetime') or default_datetime
    if scheduled:
        result['scheduled_at'] = timestamp(scheduled)
    if result.get('planned_at'):
        result['planned_at'] = timestamp(result['planned_at'])
    if kind == 'thread':
        items = result.get('thread_items')
        if not isinstance(items, list) or not 3 <= len(items) <= 7:
            raise SafeError('thread_items must contain 3–7 entries')
        normalized = []
        for item in items:
            if isinstance(item, str):
                item = {'text': item}
            if not isinstance(item, dict):
                raise SafeError('Invalid thread item')
            normalized.append({**item, 'text': _text(item.get('text'))})
        result['thread_items'] = normalized
        if result.get('text') and result['text'] != normalized[0]['text']:
            raise SafeError('Thread text must match its first item')
        result['text'] = _text(normalized[0]['text'])
    else:
        result['text'] = _text(result.get('text'))
    if kind in ('image', 'carousel'):
        urls = result.get('image_urls')
        expected = (1, 1) if kind == 'image' else (2, 20)
        if not isinstance(urls, list) or not expected[0] <= len(urls) <= expected[1]:
            raise SafeError('Image count must match post_type')
        result['image_urls'] = [public_image_url(url) for url in urls]
        if 'image_sha256' in result:
            hashes = result['image_sha256']
            import re
            if not isinstance(hashes, list) or len(hashes) != len(urls) or any(
                    not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in hashes):
                raise SafeError('image_sha256 must contain one lowercase SHA256 per URL')
        alt = result.get('alt_text', '')
        if not isinstance(alt, (str, list)) or (isinstance(alt, list) and
                (len(alt) != len(urls) or any(not isinstance(v, str) for v in alt))):
            raise SafeError('alt_text must be text or one string per image')
        if type(result.get('rights_confirmed', False)) is not bool:
            raise SafeError('rights_confirmed must be boolean')
    result.pop('post_id', None)
    result.pop('publish_datetime', None)
    return result


def approval_digest(post):
    import hashlib
    import json
    normalized = normalize_post(post)
    keys = ('id', 'post_type', 'text', 'thread_items', 'image_urls', 'alt_text',
            'rights_confirmed', 'image_sha256', 'category', 'topic', 'keywords', 'sources', 'planned_at')
    contents = {key: normalized.get(key) for key in keys}
    return hashlib.sha256(json.dumps(contents, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()
