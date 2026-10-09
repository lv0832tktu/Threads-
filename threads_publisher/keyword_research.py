"""Bounded GET-only public keyword samples; never stores another author's text."""
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError
from .storage import read_json, atomic_json
from .market import public_url, features, metric, POST_METRICS

JST = ZoneInfo('Asia/Tokyo')


def keyword_catalog(config):
    words = {}
    try:
        for group in config['groups']:
            for word in group['keywords']:
                if not isinstance(word, str) or not 1 <= len(word.strip()) <= 100:
                    raise ValueError()
                normalized = unicodedata.normalize('NFKC', word).strip().casefold()
                entry = words.setdefault(normalized, {'keyword': word, 'categories': []})
                if group['theme'] not in entry['categories']:
                    entry['categories'].append(group['theme'])
        if not words:
            raise ValueError()
        return list(words.values())
    except (KeyError, TypeError, ValueError):
        raise SafeError('Invalid keyword categories') from None


def analyze_keywords(client, config_path='config/keywords.json', history_path='private/keyword-history.json',
                     output_path='private/keyword-analysis.json', now=None, persist=lambda: None):
    config = read_json(config_path)
    if config.get('enabled') is not True:
        return {'disabled': True, 'requests': 0, 'successful_queries': 0}
    try:
        for name, ceiling in [('daily_request_limit', 20), ('weekly_request_limit', 100),
                              ('max_pages', 2), ('page_size', 25), ('frequency_hours', 168)]:
            if type(config[name]) is not int or not 1 <= config[name] <= ceiling:
                raise ValueError()
        if config['timezone'] != 'Asia/Tokyo' or config['search_type'] not in ('RECENT', 'TOP'):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise SafeError('Invalid keyword research limits') from None
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    day = now.date().isoformat()
    week = f'{now.isocalendar().year}-{now.isocalendar().week:02}'
    history = read_json(history_path) if Path(history_path).exists() else {'cursor': 0, 'queries': []}
    output = read_json(output_path) if Path(output_path).exists() else {'schema_version': 1, 'runs': []}
    words = keyword_catalog(config)
    run = {'analyzed_at': now.isoformat(), 'queries': [], 'posts': []}
    requests = 0
    start = history['cursor'] % len(words)
    for offset in range(len(words)):
        entry = words[(start + offset) % len(words)]
        if any(q['keyword'] == entry['keyword'] and
               datetime.fromisoformat(q['reserved_at']) > now - timedelta(hours=config['frequency_hours'])
               for q in history['queries']):
            continue
        cursor = None
        seen = set()
        for page in range(config['max_pages']):
            daily = sum(q['day'] == day for q in history['queries'])
            weekly = sum(q['week'] == week for q in history['queries'])
            if daily >= config['daily_request_limit'] or weekly >= config['weekly_request_limit']:
                break
            reservation = {**entry, 'reserved_at': now.isoformat(), 'day': day, 'week': week,
                           'status': 'reserved', 'page': page + 1}
            history['queries'].append(reservation)
            history['cursor'] = (start + offset + 1) % len(words)
            atomic_json(history_path, history)
            persist()  # Durable allowance first, including failed and interrupted requests.
            query = {'q': entry['keyword'], 'search_type': config['search_type'],
                     'fields': 'id,text,timestamp,permalink', 'limit': config['page_size']}
            if cursor:
                query['after'] = cursor
            requests += 1
            try:
                result = client.get('keyword_search', query)
                if not isinstance(result.get('data'), list):
                    raise SafeError('Unexpected keyword search response')
                rows = result['data'][:config['page_size']]
                reservation.update(status='success', returned=len(rows))
                run['queries'].append({**entry, 'success': True, 'returned': len(rows), 'page': page + 1})
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    url = public_url(row.get('permalink'))
                    extracted = features(row.get('text'), [])
                    if not url or not extracted:
                        continue
                    run['posts'].append({'url': url, 'keyword': entry['keyword'],
                                         'categories': entry['categories'], 'characters': extracted['characters'],
                                         'hook': extracted['hook'], 'observed_at': now.isoformat(),
                                         'provenance': 'official_keyword_search',
                                         'metrics': {name: metric(row.get(field)) for name, field in POST_METRICS.items()}})
                cursor = result.get('paging', {}).get('cursors', {}).get('after')
                if not result.get('paging', {}).get('next') or not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                    cursor = None
                elif cursor:
                    seen.add(cursor)
            except SafeError:
                reservation['status'] = 'failed'
                run['queries'].append({**entry, 'success': False, 'page': page + 1})
                cursor = None
            atomic_json(history_path, history)
            output['runs'].append(run)
            output['runs'] = output['runs'][-52:] if len(output['runs']) > 52 else output['runs']
            atomic_json(output_path, output)
            output['runs'].pop()  # Save progressive run without duplicate append on next page.
            persist()
            if reservation['status'] == 'failed' or not cursor:
                break
        if requests >= config['daily_request_limit']:
            break
    history['queries'] = [q for q in history['queries'] if datetime.fromisoformat(q['reserved_at']) >= now - timedelta(days=52)]
    atomic_json(history_path, history)
    persist()
    return {'disabled': False, 'requests': requests,
            'successful_queries': sum(q['success'] for q in run['queries']),
            'errors': sum(not q['success'] for q in run['queries']), 'posts': len(run['posts'])}


def import_public_observations(source, target='private/manual-public-observations.json'):
    """Whitelist structured public observations; text and usernames never retained."""
    document = read_json(source)
    clean = []
    for row in document.get('observations', []):
        url = public_url(row.get('url'))
        try:
            observed = datetime.fromisoformat(row['observed_at'])
            if not url or observed.tzinfo is None or type(row['characters']) is not int or not 0 <= row['characters'] <= 500:
                raise ValueError()
            if row['hook'] not in ('質問型', '数字・手順型', '説明・体験型'):
                raise ValueError()
            theme = row['theme']
            if not isinstance(theme, str) or not 1 <= len(theme) <= 60:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise SafeError('Invalid manual public observation') from None
        clean.append({'url': url, 'observed_at': observed.isoformat(), 'characters': row['characters'],
                      'hook': row['hook'], 'theme': theme, 'provenance': 'manual_public',
                      'metrics': {name: metric(row.get('metrics', {}).get(name)) for name in POST_METRICS}})
    existing = read_json(target) if Path(target).exists() else {'observations': []}
    known = {(r['url'], r['observed_at']) for r in existing['observations']}
    added = []
    for row in clean:
        identity = (row['url'], row['observed_at'])
        if identity not in known:
            added.append(row)
            known.add(identity)
    existing['observations'].extend(added)
    atomic_json(target, existing)
    return {'imported': len(added)}
