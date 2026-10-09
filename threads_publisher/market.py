"""Official public endpoints only. No scraping, AI calls or competitor Insights."""
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError, NoRedirect
from .storage import read_json, atomic_json
from .insights import instant, read_snapshots

JST=ZoneInfo('Asia/Tokyo')
FIELDS='id,username,text,timestamp,permalink'
API_VERSION='v1.0'
POST_METRICS={'likes':'likes_count','replies':'replies_count','reposts':'reposts_count','quotes':'quotes_count'}
PROFILE_METRICS={**POST_METRICS,'views':'views_count','followers':'follower_count'}


def load_market(path):
    config=read_json(path)
    try:
        if config['timezone']!='Asia/Tokyo' or type(config['enabled']) is not bool:raise ValueError()
        for field in ('competitors','keywords','themes'):
            if not isinstance(config[field],list) or len(config[field])>10:raise ValueError()
        if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,64}',v) for v in config['competitors']):raise ValueError()
        if any(not isinstance(v,str) or not v.strip() or len(v)>100 for v in config['keywords']):raise ValueError()
        for theme in config['themes']:
            if not isinstance(theme.get('name'),str) or len(theme['name'])>60 or not isinstance(theme.get('terms'),list) or not 1<=len(theme['terms'])<=20 or any(not isinstance(term,str) or not term or len(term)>100 for term in theme['terms']):raise ValueError()
        if config['search_type'] not in ('TOP','RECENT'):raise ValueError()
        for field,ceiling in (('max_pages',5),('page_size',50),('request_limit',100)):
            if type(config[field]) is not int or not 1<=config[field]<=ceiling:raise ValueError()
        if type(config['profile_summary']) is not bool or type(config['weekly_only']) is not bool:raise ValueError()
        return config
    except (KeyError,TypeError,AttributeError,ValueError):
        raise SafeError('Invalid market configuration') from None


def public_url(value):
    if not isinstance(value,str):return None
    try:
        url=urllib.parse.urlsplit(value)
        port=url.port
    except ValueError:
        return None
    if url.scheme!='https' or url.hostname not in ('www.threads.net','threads.net','www.threads.com','threads.com') or url.username or url.password or port not in (None,443):return None
    if not re.fullmatch(r'/@[A-Za-z0-9_.]+/post/[A-Za-z0-9_-]+/?',url.path):return None
    return urllib.parse.urlunsplit(('https',url.hostname,url.path,'',''))


def metric(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None


def features(text,themes):
    if not isinstance(text,str):return None
    opening=text.split('\n',1)[0]
    hook='質問型' if '?' in opening or '？' in opening else '数字・手順型' if re.search(r'\d',opening) else '説明・体験型'
    tags=[theme['name'] for theme in themes if any(term.casefold() in text.casefold() for term in theme['terms'])]
    return {'characters':len(text),'hook':hook,'themes':tags or ['未分類']}


class PublicAPIError(SafeError):
    """Only fixed classifications and bounded numeric API metadata are exposed."""
    def __init__(self,message,kind,http_status=None,code=None,subcode=None):
        super().__init__(message)
        self.diagnostic={'kind':kind,'http_status':http_status if type(http_status) is int and 100<=http_status<=599 else None,
                         'api_code':code if type(code) is int and 0<=code<=1000000 else None,
                         'api_subcode':subcode if type(subcode) is int and 0<=subcode<=1000000000 else None}


def public_api_error(status,payload):
    error=payload.get('error',{}) if isinstance(payload,dict) else {}
    if not isinstance(error,dict):error={}
    code,subcode=error.get('code'),error.get('error_subcode')
    if status==401 or code==190:kind,message='authentication','Public research token expired or invalid'
    elif status==429 or code in (4,17,32,613):kind,message='rate_limit','Public research rate limited; retry on a later approved run'
    elif status==403 or code in (10,200):kind,message='permission_or_access','Public endpoint permission or app access review required'
    elif status==400:kind,message='request_or_access','Public request rejected; check parameters, API version and app access'
    else:kind,message='api_error','Public endpoint request failed'
    return PublicAPIError(message,kind,status,code,subcode)


class MarketClient:
    def __init__(self,token,limit=20,transport=None,api_version=API_VERSION):
        if not token:raise SafeError('Threads token missing for public research')
        if not isinstance(api_version,str) or not re.fullmatch(r'v[1-9][0-9]{0,2}\.[0-9]{1,2}',api_version):raise SafeError('Invalid API version syntax')
        self.api_version=api_version
        self.token,self.limit,self.calls=token,limit,0
        self.transport=transport or urllib.request.build_opener(NoRedirect()).open

    def get(self,endpoint,params):
        if endpoint not in ('keyword_search','profile_posts','profile_lookup'):raise SafeError('Public endpoint not allowed')
        if self.calls>=self.limit:raise SafeError('Public research request budget reached')
        self.calls+=1
        request=urllib.request.Request('https://graph.threads.net/'+self.api_version+'/'+endpoint+'?'+urllib.parse.urlencode(params),headers={'Authorization':'Bearer '+self.token})
        status=None
        try:
            with self.transport(request,timeout=30) as response:
                status=getattr(response,'status',200)
                result=json.load(response)
            if not isinstance(result,dict):raise ValueError()
            if 'error' in result:raise public_api_error(status,result)
            return result
        except urllib.error.HTTPError as error:
            try:payload=json.loads(error.read(65536))
            except (ValueError,AttributeError):payload={}
            raise public_api_error(error.code,payload) from None
        except PublicAPIError:raise
        except SafeError:
            raise PublicAPIError('Public API redirect refused','redirect_refused') from None
        except OSError:
            raise PublicAPIError('Public research connection failed','connection_error') from None
        except (ValueError,TypeError):
            raise PublicAPIError('Public research response format invalid','response_format',status) from None

    def posts(self,endpoint,params,config):
        rows=[];cursor=None;seen=set()
        for _ in range(config['max_pages']):
            query={**params,'fields':FIELDS,'limit':config['page_size']}
            if cursor:query['after']=cursor
            response=self.get(endpoint,query)
            if not isinstance(response.get('data'),list):raise SafeError('Unexpected public post response')
            rows.extend(response['data'][:config['page_size']])
            # Never follow paging.next, which may contain credentials or an arbitrary host.
            cursor=response.get('paging',{}).get('cursors',{}).get('after')
            if not isinstance(cursor,str) or not cursor or cursor in seen or len(cursor)>2048:break
            if not response.get('paging',{}).get('next'):break
            seen.add(cursor)
        return rows


def collect_market(client,config,path,now=None):
    if config['enabled'] is not True:return {'disabled':True,'requests':0}
    if not config['competitors'] and not config['keywords']:raise SafeError('Configure competitors or search keywords first')
    now=(now or datetime.now(timezone.utc)).astimezone(JST)
    cutoff=now-timedelta(days=7)
    week=f'{now.isocalendar().year}-{now.isocalendar().week:02}'
    document=read_json(path) if Path(path).exists() else {'schema_version':1,'runs':[]}
    if config['weekly_only'] and any(run['week']==week and run.get('complete') for run in document['runs']):return {'skipped':True,'requests':0}
    run={'week':week,'collected_at':now.isoformat(),'window_start':cutoff.isoformat(),'posts':[], 'profiles':[], 'errors':[], 'complete':False}
    records={}
    sources=[('profile_posts',{'username':username},'競合',username) for username in config['competitors']]
    sources += [('keyword_search',{'q':word,'search_type':config['search_type'],'since':int(cutoff.timestamp()),'until':int(now.timestamp())},'検索',word) for word in config['keywords']]
    for endpoint,params,kind,label in sources:
        try:
            for post in client.posts(endpoint,params,config):
                if not isinstance(post,dict) or not isinstance(post.get('id'),str):continue
                try:published=instant(post['timestamp'])
                except (ValueError,TypeError,KeyError,AttributeError):continue
                url=public_url(post.get('permalink'))
                extracted=features(post.get('text'),config['themes'])
                if not url or not extracted or not cutoff<=published<=now:continue
                record=records.setdefault(post['id'],{'id':post['id'],'url':url,'published_at':published.isoformat(),**extracted,'metrics':{name:metric(post.get(field)) for name,field in POST_METRICS.items()},'sources':[]})
                source={'kind':kind,'label':label}
                if source not in record['sources']:record['sources'].append(source)
            if kind=='競合' and config['profile_summary']:
                profile=client.get('profile_lookup',{'username':label})
                run['profiles'].append({'username':label,'scope':'profile_total_at_collection','metrics':{name:metric(profile.get(field)) for name,field in PROFILE_METRICS.items()}})
        except SafeError as error:
            run['errors'].append({'endpoint':endpoint,'reason':str(error)})
    run['posts']=list(records.values())
    run['complete']=not run['errors']
    # Store only features, URLs and numeric values, never the competitor's wording.
    document['runs'].append(run)
    document['runs']=document['runs'][-52:]
    atomic_json(path,document)
    return {'posts':len(run['posts']),'requests':client.calls,'errors':len(run['errors']),'skipped':False}


def market_report(path,own_insights,output,now=None):
    now=(now or datetime.now(timezone.utc)).astimezone(JST)
    document=read_json(path)
    if not document.get('runs'):raise SafeError('No public research data available')
    run=document['runs'][-1]
    posts=run['posts']
    themes=Counter(theme for post in posts for theme in post['themes'])
    hooks=Counter(post['hook'] for post in posts)
    lengths=[post['characters'] for post in posts]
    lines=['# Threads競合・トレンド週次レポート',f"取得日時：{run['collected_at']}",
           '公式APIが返した公開情報のサンプルです。市場全体の流行、因果関係、競合の閲覧数・反応率を推測しません。',
           f"対象：直近7日、ユニーク投稿{len(posts)}件。取得エラー{len(run['errors'])}件。",
           '競合本文は保存・転載していません。未取得指標は不明です。','', '## 観察されたテーマとフック']
    for name,count in themes.most_common():lines.append(f'- テーマ「{name}」：{count}件（設定語との一致による分類）')
    for name,count in hooks.most_common():lines.append(f'- {name}：{count}件')
    if lengths:lines.append(f'- 平均文字数：{sum(lengths)/len(lengths):.0f}字、範囲：{min(lengths)}〜{max(lengths)}字')
    previous=next((item for item in reversed(document['runs'][:-1]) if item['week']!=run['week'] and item.get('complete')),None)
    if previous:
        old=Counter(theme for post in previous['posts'] for theme in post['themes'])
        lines.append('### 前回サンプルとの比較（検索設定・件数・順位の変化に影響されます）')
        for theme in themes:lines.append(f'- {theme}：前回{old[theme]}件 → 今回{themes[theme]}件。全体の流行を示す増減ではありません。')
    else:lines.append('前週データがないため、増加・減少の判定はしません。')
    lines.extend(['','## 公開反応数と根拠URL','|投稿URL|フック分類|文字数|いいね|返信|リポスト|引用|','|---|---|---:|---:|---:|---:|---:|'])
    for post in posts[:50]:
        values=[str(post['metrics'].get(name)) if post['metrics'].get(name) is not None else '取得不可' for name in POST_METRICS]
        lines.append(f"|{post['url']}|{post['hook']}|{post['characters']}|"+'|'.join(values)+'|')
    for profile in run['profiles']:
        data='、'.join(f'{name}={value if value is not None else "取得不可"}' for name,value in profile['metrics'].items())
        lines.append(f"- 公開プロフィール全体 @{profile['username']}：{data}。投稿別・週別の値ではありません。")
    lines.extend(['','## 自分のInsightsとの比較'])
    own=read_snapshots(own_insights)['snapshots']
    grouped=defaultdict(list)
    for snapshot in own:
        try:published=instant(snapshot['published_at'])
        except (ValueError,KeyError,TypeError):continue
        if now-timedelta(days=7)<=published<=now:grouped[(snapshot['checkpoint'],snapshot.get('late',False))].append(snapshot)
    for (checkpoint,late),samples in grouped.items():
        unique={(p['account'],p['post_id']):p for p in samples}
        complete=[p for p in unique.values() if p['metrics'].get('views',0)>0 and all(name in p['metrics'] for name in POST_METRICS)]
        views=sum(p['metrics']['views'] for p in complete)
        reactions=sum(sum(p['metrics'][name] for name in POST_METRICS) for p in complete)
        rate=f'{reactions/views:.2%}' if views else '不明'
        own_lengths=[len(p.get('text','')) for p in unique.values()]
        lines.append(f'- 自分：{checkpoint}、遅延={late}、{len(unique)}投稿、平均{sum(own_lengths)/len(own_lengths):.0f}字、完全取得分の加重反応率={rate}。')
    if not grouped:lines.append('- 対象期間の自分のInsightsが不足しています。比較結果を断定しません。')
    own_hooks=Counter(features(p.get('text',''),[])['hook'] for samples in grouped.values() for p in {(s['account'],s['post_id']):s for s in samples}.values())
    for hook,count in own_hooks.items():lines.append(f'- 自分のフック分類：{hook} {count}観測（取得時点ごと。同じ投稿の異なる時点を独立投稿数とみなさない）。')
    lines.append('競合の閲覧数・公開後同時点の値がないため、自分との反応率ランキングは作りません。')
    if len(posts)<5:lines.append('競合サンプルが5件未満のため、傾向・優位性を判断するには不足しています。')
    lines.extend(['','## 次週のテーマ・改善点・推奨構成（検証する仮説）'])
    selected=[theme for theme,_ in themes.most_common(3) if theme!='未分類']
    if selected:
        for theme in selected:
            urls=[p['url'] for p in posts if theme in p['themes']][:3]
            lines.append(f'- テーマ候補「{theme}」：自分の経験・独自の根拠で扱う。根拠サンプル：'+ '、'.join(urls))
    else:lines.append('- テーマ分類または取得情報が不足しています。ジャンルと分類語を設定してからテーマを選びます。')
    lines.extend(['- 改善点：一度に変える要素は1つ。冒頭を質問型/体験型で比較し、文字数と時間帯は揃える。',
                  '- 推奨構成：独自の問い・気づき → 自分の具体例または確認済み根拠 → 読者への短い問い。',
                  '- 08:00は気づき、19:00は具体例、22:00は振り返りという構成を仮説として検証する。',
                  '','## ChatGPT Plusへ貼り付ける依頼',
                  '上記の観察と根拠URLを参考に、指定ジャンル・読者・口調で次週7日×3本=21本を作成してください。',
                  '競合の文章・特徴的な言い回し・事例はコピーせず、独自の構成と自分の情報で書いてください。',
                  '取得不可の数値や流行の確実性を捏造しないでください。少数データは仮説として扱ってください。',
                  '各本文500文字以内、{"posts":[{"id":"一意のID","text":"本文"},...]} のJSONで出力してください。',
                  '承認は人が行います。公開・AI APIへの自動送信は行いません。',''])
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text('\n'.join(lines),encoding='utf-8')
    return {'posts':len(posts),'own_groups':len(grouped),'errors':len(run['errors'])}
