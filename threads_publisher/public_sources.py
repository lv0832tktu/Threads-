"""Manual public observations and expressly permitted feeds; no HTML scraping."""
import csv
import json
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .core import SafeError
from .market import public_url, metric, POST_METRICS
from .storage import read_json, atomic_json
from .insights import instant
from .media import _PinnedHTTPS, _public_addresses, MediaError

MAX_BYTES=1024*1024


def import_competitors(source,target='private/manual-public-observations.json',now=None):
    """URL/CSV entries are manual observations, never fetched or given invented metrics."""
    source=Path(source); now=now or datetime.now(timezone.utc)
    try:
        if source.stat().st_size>MAX_BYTES:raise ValueError()
        if source.suffix.lower()=='.csv':
            with source.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        elif source.suffix.lower()=='.json':rows=read_json(source)['observations']
        else:rows=[{'url':line.strip()} for line in source.read_text().splitlines() if line.strip()]
        if not isinstance(rows,list) or len(rows)>1000:raise ValueError()
        clean=[]
        for row in rows:
            url=public_url(row.get('url'))
            if not url:raise ValueError()
            observed=instant(row.get('observed_at') or now.isoformat())
            if observed>now:raise ValueError()
            theme=row.get('theme') or row.get('keyword') or '未分類'
            if not isinstance(theme,str) or len(theme)>100:raise ValueError()
            chars=row.get('characters')
            if chars in (None,''):chars=None
            else:
                if isinstance(chars,bool):raise ValueError()
                if isinstance(chars,float):raise ValueError()
                chars=int(chars)
                if not 0<=chars<=500:raise ValueError()
            hook=row.get('hook') or None
            if hook not in (None,'質問型','数字・手順型','説明・体験型'):raise ValueError()
            metrics={}
            raw_metrics=row.get('metrics',{})
            if not isinstance(raw_metrics,dict):raise ValueError()
            for name in POST_METRICS:
                value=raw_metrics.get(name,row.get(name))
                if value in (None,''):value=None
                elif isinstance(value,str):value=float(value)
                if value is not None and metric(value) is None:raise ValueError()
                metrics[name]=metric(value)
            clean.append({'url':url,'theme':theme,'observed_at':observed.isoformat(),'characters':chars,'hook':hook,'metrics':metrics,'provenance':'manual_public'})
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise SafeError('Invalid manual public URL/CSV input; nothing imported') from None
    document=read_json(target) if Path(target).exists() else {'observations':[]}
    known={(row['url'],row['observed_at']) for row in document['observations']}
    added=[]
    for row in clean:
        key=(row['url'],row['observed_at'])
        if key not in known:added.append(row);known.add(key)
    document['observations'].extend(added);atomic_json(target,document)
    return {'imported':len(added),'network_requests':0}


def allowed_url(value,hosts):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment or parsed.query or any(ord(c)<33 for c in value):
        raise SafeError('Source URL requires an explicitly permitted public HTTPS host')
    return parsed


def fetch_feed(url,hosts,transport=None,resolver=None):
    parsed=allowed_url(url,hosts);connection=None
    try:
        addresses=_public_addresses(parsed.hostname,resolver or socket.getaddrinfo)
        if transport:response=transport(url,timeout=30)
        else:
            connection=_PinnedHTTPS(parsed.hostname,addresses[0])
            connection.request('GET',parsed.path or '/',headers={'Accept':'application/rss+xml, application/atom+xml, application/json, application/xml','Accept-Encoding':'identity'})
            response=connection.getresponse()
        with response:
            if response.status!=200 or response.headers.get('Content-Encoding','identity').lower()!='identity':raise SafeError('Permitted feed unavailable or redirected')
            data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise SafeError('Permitted feed exceeds size limit')
            return data
    except SafeError:raise
    except (OSError,ValueError,MediaError):raise SafeError('Permitted feed connection failed') from None
    finally:
        if connection:connection.close()


def parse_feed(data,kind):
    try:
        if kind=='json':rows=json.loads(data)['items']
        else:
            text=data.decode('utf-8-sig')
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError()
            root=ET.fromstring(text)
            if root.tag=='rss':
                rows=[{'title':item.findtext('title'),'url':item.findtext('link'),'published_at':item.findtext('pubDate')} for item in root.findall('./channel/item')[:50]]
            elif root.tag=='{http://www.w3.org/2005/Atom}feed':
                ns='{http://www.w3.org/2005/Atom}'
                rows=[]
                for item in root.findall(ns+'entry')[:50]:
                    link=next((v.get('href') for v in item.findall(ns+'link') if v.get('rel','alternate')=='alternate'),None)
                    rows.append({'title':item.findtext(ns+'title'),'url':link,'published_at':item.findtext(ns+'published') or item.findtext(ns+'updated')})
            else:raise ValueError()
        if not isinstance(rows,list):raise ValueError()
        return rows[:50]
    except (ValueError,TypeError,KeyError,ET.ParseError):raise SafeError('Invalid permitted RSS/Atom/JSON feed; HTML scraping is unsupported') from None


def collect_trends(config_path='config/trend_sources.json',history_path='private/trend-history.json',output_path='private/trend-data.json',now=None,persist=lambda:None,transport=None,resolver=None):
    config=read_json(config_path)
    if config.get('enabled') is not True:return {'disabled':True,'requests':0,'errors':0}
    now=now or datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()
    if now.tzinfo is None:raise SafeError('Trend collection time requires timezone')
    sources=config.get('sources',[]);hosts=config.get('allowed_hosts',[]);cap=config.get('daily_request_limit',3)
    if not isinstance(sources,list) or len(sources)>5 or not isinstance(hosts,list) or type(cap) is not int or not 1<=cap<=5:raise SafeError('Invalid permitted source limits')
    for source in sources:
        if not isinstance(source,dict) or source.get('permission_confirmed') is not True or source.get('kind') not in ('rss','atom','json') or source.get('official_source') is not True:raise SafeError('Source needs explicit permission and official-source confirmation')
        try:
            allowed_url(source['url'],hosts)
            allowed_url(source['permission_reference'],hosts)
        except (KeyError,TypeError,ValueError):raise SafeError('Source requires a valid permission reference and feed URL') from None
    history=read_json(history_path) if Path(history_path).exists() else {'requests':[]}
    output=read_json(output_path) if Path(output_path).exists() else {'items':[]}
    result={'disabled':False,'requests':0,'errors':0}
    for source in sources:
        if sum(r['day']==day for r in history['requests'])>=cap:break
        if any(r['day']==day and r['source']==source['url'] for r in history['requests']):continue
        reservation={'day':day,'source':source['url'],'status':'reserved'}
        history['requests'].append(reservation);atomic_json(history_path,history);persist()
        result['requests']+=1
        try:
            data=fetch_feed(source['url'],hosts,transport,resolver)
            for row in parse_feed(data,source['kind']):
                try:
                    parsed=allowed_url(row['url'],hosts)
                    title=row['title']
                    if not isinstance(title,str) or not title.strip():continue
                    timestamp=row.get('published_at');published=None
                    if timestamp:
                        try:published=instant(timestamp).isoformat()
                        except (ValueError,AttributeError):
                            parsed_time=parsedate_to_datetime(timestamp)
                            if parsed_time.tzinfo is not None:published=parsed_time.isoformat()
                    clean={'url':urlunsplit(parsed),'title':title[:200],'published_at':published,'collected_at':now.isoformat(),'source':source['url'],'provenance':'permitted_official_feed'}
                    if not any(v['url']==clean['url'] for v in output['items']):output['items'].append(clean)
                except (SafeError,ValueError,KeyError,TypeError,AttributeError):continue
            reservation['status']='success'
        except SafeError:
            reservation['status']='failed';result['errors']+=1
        atomic_json(history_path,history);atomic_json(output_path,output);persist()
    return result
