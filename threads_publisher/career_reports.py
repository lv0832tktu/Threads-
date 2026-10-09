"""Public-safe weekly reports from private history. No publication side effects."""
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import SafeError
from .insights import instant, read_snapshots, media_rows
from .market import features

JST = ZoneInfo('Asia/Tokyo')
REACTIONS = ('likes', 'replies', 'reposts', 'quotes')


def closed_week(now):
    local = now.astimezone(JST)
    cutoff = (local - timedelta(days=(local.weekday()+1)%7)).replace(hour=21, minute=0, second=0, microsecond=0)
    if local < cutoff:
        cutoff -= timedelta(days=7)
    return (cutoff-timedelta(days=6)).replace(hour=0), cutoff


def finite(value):
    return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and value >= 0


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(content, encoding='utf-8')
    os.replace(temporary,path)


def save_report(report, destination, output_dir):
    serialized=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    markdown='# 週次運用レポート\n\n'+report['period_start']+' 〜 '+report['period_end_exclusive']+'（終了時刻は含まない）\n\n'+report['caution']+'\n\n'+report['measurement_note']+'\n'
    for title,value in report['sections'].items():
        markdown+='\n## '+title+'\n\n```json\n'+json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n```\n'
    for target in (destination,Path(output_dir)/'latest'):
        atomic(target.with_suffix('.json'),serialized)
        atomic(target.with_suffix('.md'),markdown)


def generate_report(history, insights_path, market_path, output_dir='reports', now=None, followers_path=None, force=False):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SafeError('Report time requires timezone')
    start, cutoff = closed_week(now)
    destination = Path(output_dir)/'weekly'/cutoff.date().isoformat()
    if destination.with_suffix('.json').exists() and not force:
        report=json.loads(destination.with_suffix('.json').read_text(encoding='utf-8'))
        # Repair a interrupted multi-file save without recomputing the week's data.
        save_report(report,destination,output_dir)
        return report
    columns = [r[1] for r in history.db.execute('PRAGMA table_info(posts)')]
    jobs = [dict(zip(columns,r)) for r in history.db.execute('SELECT * FROM posts')]
    latest = {}
    for snap in read_snapshots(insights_path)['snapshots']:
        try:
            captured = instant(snap['collected_at'])
            if captured > now:
                continue
        except (ValueError,KeyError,AttributeError):
            continue
        key = (str(snap['account']), str(snap['remote_id']))
        if key not in latest or captured > instant(latest[key]['collected_at']):
            latest[key] = snap
    taxonomy = json.loads((Path(__file__).resolve().parents[1]/'config/keywords.json').read_text(encoding='utf-8'))
    allowed_words = {w for group in taxonomy.get('groups',[]) for w in group.get('keywords',[]) if isinstance(w,str)}
    allowed_categories = {g.get('id') for g in taxonomy.get('groups',[])} | {g.get('theme') for g in taxonomy.get('groups',[])}
    public_posts, reply_metrics = [], []
    reporting_jobs = list(jobs)
    existing = {(str(j['account']),str(j.get('remote_id'))) for j in reporting_jobs if j.get('status')=='published'}
    for media in media_rows(history):
        if media.get('component')=='root' and (str(media['account']),str(media['remote_id'])) not in existing:
            reporting_jobs.append(dict(media, status='published', job_status='partial_root_published'))
    stats = Counter()
    after_cutoff = 0
    sunday_end=cutoff.replace(hour=0)+timedelta(days=1)
    for job in reporting_jobs:
        timestamp = job.get('published_at') or job.get('scheduled_at')
        try:
            when = instant(timestamp)
        except (ValueError,AttributeError):
            continue
        if cutoff <= when.astimezone(JST) < sunday_end and job.get('status')=='published':
            after_cutoff += 1
        if not start <= when.astimezone(JST) < cutoff:
            continue
        status = job.get('job_status') or job.get('status') or 'unknown'
        if status == 'posted':
            status = 'published'
        if status != 'partial_root_published':
            stats[status] += 1
        if job.get('status') != 'published' or not job.get('remote_id'):
            continue
        snapshot = latest.get((str(job['account']),str(job['remote_id'])))
        metrics = {k:v for k,v in (snapshot or {}).get('metrics',{}).items() if finite(v)}
        complete = all(k in metrics for k in REACTIONS) and metrics.get('views',0)>0
        try:
            keywords = json.loads(job.get('keywords') or '[]')
        except (ValueError,TypeError):
            keywords = []
        structure=features(job.get('text'),[]) or {}
        length=structure.get('characters')
        approved_words=[k for k in keywords if k in allowed_words] if isinstance(keywords,list) else []
        theme=job.get('topic') if job.get('topic') in allowed_words else (approved_words[0] if approved_words else '未分類')
        # Only derived features and configured published taxonomy enter plaintext reports.
        alias = hashlib.sha256((str(job['account'])+':'+str(job['post_id'])).encode()).hexdigest()[:12]
        public_posts.append({'alias':alias,'post_type':'image' if job.get('post_type')=='carousel' else job.get('post_type') if job.get('post_type') in ('text','image','thread') else 'text','category':job.get('category') if job.get('category') in allowed_categories else '未分類','keywords':[k for k in keywords if k in allowed_words] if isinstance(keywords,list) else [],'characters':length,'characters_bucket':'unknown' if length is None else 'short' if length<=100 else 'medium' if length<=250 else 'long','hook_class':structure.get('hook','unknown'),'theme':theme,'published_at':when.astimezone(JST).isoformat(),'scheduled_day':job.get('scheduled_at','')[:10] if job.get('scheduled_at') else None,'actual_day':when.astimezone(JST).date().isoformat(),'actual_hour_jst':when.astimezone(JST).hour,'metrics':metrics,'reaction_rate':sum(metrics[k] for k in REACTIONS)/metrics['views'] if complete else None,'metrics_as_of':(snapshot or {}).get('collected_at'),'post_cutoff_measurement':bool(snapshot and instant(snapshot['collected_at']).astimezone(JST)>=cutoff)})
    aliases = {(str(j['account']),str(j['post_id'])) for j in reporting_jobs if j.get('status')=='published' and any(p['alias']==hashlib.sha256((str(j['account'])+':'+str(j['post_id'])).encode()).hexdigest()[:12] for p in public_posts)}
    for snap in latest.values():
        if snap.get('component')=='reply' and (str(snap['account']),str(snap.get('parent_post_id',snap['post_id']))) in aliases:
            reply_metrics.append({k:v for k,v in snap.get('metrics',{}).items() if finite(v)})
    aggregates = {}
    for metric in ('views',)+REACTIONS+('shares',):
        values = [p['metrics'][metric] for p in public_posts if metric in p['metrics']]
        aggregates[metric] = {'sum':sum(values) if values else None,'known_posts':len(values),'unknown_posts':len(public_posts)-len(values)}
    eligible = [p for p in public_posts if p['reaction_rate'] is not None]
    ranked = sorted(eligible,key=lambda p:p['reaction_rate'],reverse=True)
    groups = {}
    for dimension in ('post_type','category','actual_day','actual_hour_jst','theme','characters_bucket','hook_class'):
        grouped = defaultdict(list)
        for post in public_posts:
            grouped[post[dimension]].append(post)
        groups[dimension] = {key:{'posts':len(values),'known_views':sum(p['metrics']['views'] for p in values if 'views' in p['metrics']) if any('views' in p['metrics'] for p in values) else None,'views_unknown':sum('views' not in p['metrics'] for p in values),'reaction_rate_mean':sum(p['reaction_rate'] for p in values if p['reaction_rate'] is not None)/sum(p['reaction_rate'] is not None for p in values) if any(p['reaction_rate'] is not None for p in values) else None,'rate_known_posts':sum(p['reaction_rate'] is not None for p in values)} for key,values in grouped.items()}
    follower_change = {'start':None,'end':None,'change':None,'status':'unavailable'}
    if followers_path and Path(followers_path).exists():
        try:
            data = json.loads(Path(followers_path).read_text(encoding='utf-8'))
            samples = data.get('snapshots',[])
            # Per-account deltas require both a baseline and a final observation.
            accounts = defaultdict(list)
            for sample in samples:
                count = sample.get('followers_count')
                if finite(count) and instant(sample['collected_at']) <= now:
                    accounts[str(sample.get('account',''))].append(sample)
            deltas = []
            for samples in accounts.values():
                baseline = [s for s in samples if instant(s['collected_at']).astimezone(JST)<=start]
                finals = [s for s in samples if start<instant(s['collected_at']).astimezone(JST)<=cutoff]
                if baseline and finals:
                    first=max(baseline,key=lambda s:instant(s['collected_at']))
                    last=max(finals,key=lambda s:instant(s['collected_at']))
                    deltas.append((first['followers_count'],last['followers_count']))
            if deltas:
                follower_change={'start':sum(a for a,b in deltas),'end':sum(b for a,b in deltas),'change':sum(b-a for a,b in deltas),'status':'observed_accounts_only','accounts':len(deltas)}
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            pass
    caution = '少数データでは有効性を断定しません。投稿経過時間・閲覧母数・話題の違いがあり、因果関係は未検証です。'
    topics = ['面接で経験を説明する準備','職務経歴書の成果の整理','求人票の条件比較','転職活動の時間管理','未経験分野への学習計画','現職でのスキル棚卸し','応募前の企業情報確認','待遇交渉の情報整理','入社後の期待値確認','転職しない選択の検討']
    market = market_summary(market_path, allowed_words, allowed_categories, public_posts, now, start, cutoff)
    themes = [f'仮説{i+1}：{topic}を具体的な一歩に分けると読者が行動しやすい。要検証。' for i,topic in enumerate(topics)]
    themes = [{'hypothesis': theme, 'source_urls': [], 'evidence':'要検証・データ不足時の編集仮説'} for theme in themes]
    for index, comparison in enumerate(market['keyword_comparisons'][:10]):
        themes[index] = {'hypothesis':f"{comparison['keyword']}の読者課題を具体的な一歩に分解し、公開サンプルの冒頭形式・長さを参考に独自の構成を試す。",'source_urls':comparison['source_urls'][:3],'market_samples':comparison['market_samples'],'own_posts':comparison['own_posts'],'evidence':comparison['evidence']}
    if market['keyword_comparisons']:
        topics = [c['keyword']+'の確認済み知識と実行手順' for c in market['keyword_comparisons'][:7]] + topics
        topics = topics[:10]
    formats = {'text':'冒頭の問い、具体例、実行可能な一歩を500文字以内で構成', 'image':'一つの要点を読みやすい図解にし、本文に代替テキストを用意', 'tree':'親投稿で論点を示し、返信で根拠・手順・注意点を一つずつ説明'}
    prompts = {kind:[f'{topics[i]}について{formats[kind]}。根拠の出典と確認日を確認し、誇張・成果保証を避ける。未承認下書きとして保存。' for i in range(7)] for kind in formats}
    sections = {'1_summary':{'published':len(public_posts),'period_start':start.isoformat(),'period_end_exclusive':cutoff.isoformat()},'2_operations':{'counts':dict(stats),'success_rate':stats.get('published',0)/sum(stats.values()) if sum(stats.values()) else None,'completed_jobs':stats.get('published',0),'excluded_after_cutoff':after_cutoff,'gap_note':'日曜21時から翌月曜0時の公開は週次集計対象外。手動期間レポートで別途確認する','note':'予約ジョブ一件につき一回計上。部分公開は成功完了に含めない'},'3_metrics':aggregates,'4_followers':follower_change,'5_top5':ranked[:5],'6_lower_posts':list(reversed(ranked[-5:])),'7_formats':groups['post_type'],'8_categories':{'categories':groups['category'],'themes':groups['theme'],'characters':groups['characters_bucket'],'hooks':groups['hook_class']},'9_keywords':dict(Counter(k for p in public_posts for k in p['keywords'])),'10_timing':{'actual_days':groups['actual_day'],'actual_hours_jst':groups['actual_hour_jst']},'11_tree_replies':{'components':len(reply_metrics),'metrics':{k:sum(m[k] for m in reply_metrics if k in m) if any(k in m for m in reply_metrics) else None for k in ('views',)+REACTIONS},'note':'返信の閲覧数を親投稿閲覧数に加算しない'},'12_quality':{'eligible_rate_posts':len(eligible),'evidence':'insufficient' if len(eligible)<5 else 'observational_only','weighted_reaction_rate':sum(sum(p['metrics'][k] for k in REACTIONS) for p in eligible)/sum(p['metrics']['views'] for p in eligible) if eligible else None,'caution':caution},'13_market':market,'14_next_themes':themes,'15_next_prompts':prompts}
    report={'schema_version':1,'generated_at':now.isoformat(),'period_start':start.isoformat(),'period_end_exclusive':cutoff.isoformat(),'measurement_note':'各投稿の最新一件の測定を使用。締切後の測定は明示し、チェックポイントを合算しません。sharesは重複の可能性があるため反応率に含めません。','sections':sections,'posts':public_posts,'caution':caution,'continue_and_improve':{'continue':'同じ測定時点で比較し、反応率が確認できたテーマを少数ずつ継続する。' if eligible else '指標不足のため勝ちパターンは未確定。公開済み投稿の測定を継続する。','improve':f'{len(eligible)}件の反応率を確認。冒頭・長さ・投稿形式を一つずつ変えて比較する。市場の公開サンプルは探索仮説に限定する。','metadata_note':'公開承認済み投稿の設定済み分類・キーワードのみ出力する'}}
    save_report(report,destination,output_dir)
    return report


def market_summary(path, allowed_words, allowed_categories, own_posts, now, start=None, cutoff=None):
    from .market import public_url
    rows = []
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            rows = [post for run in data.get('runs',[]) for post in run.get('posts',[])] + data.get('observations',[])
        except (OSError,ValueError,AttributeError,TypeError):
            rows = []
    if path:
        manual_path = Path(path).parent/'manual-public-observations.json'
        if manual_path.exists():
            try:
                manual = json.loads(manual_path.read_text(encoding='utf-8'))
                for row in manual.get('observations',[]):
                    if not isinstance(row,dict):
                        continue
                    # Manual samples may only expose a configured keyword as their theme.
                    keyword = row.get('keyword') or row.get('theme')
                    if keyword in allowed_words:
                        rows.append(dict(row,keyword=keyword))
                    elif keyword in allowed_categories:
                        rows.append(dict(row,keyword=keyword,category=keyword,category_only=True))
            except (OSError,ValueError,TypeError,AttributeError):
                pass
    unique = {}
    for row in rows:
        try:
            word = row.get('keyword')
            url = public_url(row.get('url'))
            observed = instant(row['observed_at'])
            if word not in allowed_words | allowed_categories or not url or observed > now or (start is not None and observed < start) or (cutoff is not None and observed >= cutoff):
                continue
            key=(url,word)
            if key not in unique or observed > instant(unique[key]['observed_at']):
                unique[key] = row
        except (ValueError,TypeError,KeyError,AttributeError):
            continue
    groups = defaultdict(list)
    for row in unique.values():
        groups[row['keyword']].append(row)
    comparisons = []
    for word, samples in groups.items():
        metric_summary = {}
        own = [p for p in own_posts if word in p['keywords'] or word == p['category']]
        for name in ('views',)+REACTIONS+('shares',):
            values = [r.get('metrics',{}).get(name) for r in samples if finite(r.get('metrics',{}).get(name))]
            own_values = [p['metrics'][name] for p in own if name in p['metrics']]
            metric_summary[name] = {'market_mean':sum(values)/len(values) if values else None,'market_known':len(values),'own_mean':sum(own_values)/len(own_values) if own_values else None,'own_known':len(own_values)}
        lengths = [r['characters'] for r in samples if finite(r.get('characters'))]
        comparisons.append({'keyword':word,'market_samples':len(samples),'own_posts':len(own),'metrics':metric_summary,'characters_mean':sum(lengths)/len(lengths) if lengths else None,'hook_counts':dict(Counter(r['hook'] for r in samples if r.get('hook') in ('質問型','数字・手順型','説明・体験型'))),'manual_samples':sum(r.get('provenance')=='manual_public' for r in samples),'category_mapped_samples':sum(bool(r.get('category_only')) for r in samples),'source_urls':[public_url(r['url']) for r in samples][:10],'evidence':'探索的・小標本・投稿年齢と閲覧母数は未統制'})
    return {'status':'available' if comparisons else 'unavailable','keyword_comparisons':comparisons,'source_urls':list(dict.fromkeys(public_url(r['url']) for r in unique.values()))[:20],'note':'公開サンプルの特徴と数値のみ利用。本文は転載しない。未知の指標はゼロに置き換えない。'}
