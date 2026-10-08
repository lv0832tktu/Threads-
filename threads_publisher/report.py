"""Python-only weekly report for copy/paste into ChatGPT; no model API calls."""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
from .insights import read_snapshots, instant
from .storage import read_json, atomic_json


def weekly_report(insights_path, output, now=None, weekly_only=False):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: raise ValueError('Report time needs timezone')
    now=now.astimezone(ZoneInfo('Asia/Tokyo'))
    marker=Path(output).with_name('report-status.json')
    week=f'{now.isocalendar().year}-{now.isocalendar().week:02}'
    if weekly_only and marker.exists() and read_json(marker).get('week')==week:
        return {'skipped':True,'reason':'weekly report already generated'}
    original=read_snapshots(insights_path)
    cutoff=now-timedelta(days=7)
    selected=[s for s in original['snapshots'] if cutoff<=instant(s['published_at'])<=now]
    # Compare each checkpoint separately; never count different ages as independent posts.
    groups=defaultdict(list)
    for snapshot in selected:
        groups[(snapshot['checkpoint'],snapshot.get('late',False))].append(snapshot)
    lines=['# Threads週次改善相談レポート',f'対象：公開日時が {cutoff.date()}〜{now.date()} の投稿（直近7日）',
           'このレポートは集計のみです。AI APIは使用していません。',
           '個人情報や本文は含めません。少数データ・欠測・取得遅延では効果を断定しないでください。','',
           '|取得時点|遅延|投稿数|閲覧数|既知の反応数|完全取得の加重反応率|',
           '|---|---|---:|---:|---:|---:|']
    for (checkpoint,late),samples in sorted(groups.items()):
        # Enforce unique media per account/checkpoint.
        unique={(s['account'],s.get('remote_id',s['post_id'])):s for s in samples}
        views=reactions=complete_views=complete_reactions=0
        for snapshot in unique.values():
            metrics=snapshot['metrics']
            v=metrics.get('views')
            names=('likes','replies','reposts','quotes')
            if isinstance(v,(int,float)) and v>0:
                views+=v
                reactions+=sum(metrics.get(name,0) for name in names)
                if all(name in metrics for name in names):
                    complete_views+=v; complete_reactions+=sum(metrics[name] for name in names)
        rate=f'{complete_reactions/complete_views:.2%}' if complete_views else '不明'
        lines.append(f'|{checkpoint}|{late}|{len(unique)}|{views}|{reactions}|{rate}|')
    features=defaultdict(list)
    for snapshot in selected:
        local=instant(snapshot['published_at']).astimezone(ZoneInfo('Asia/Tokyo'))
        length=len(snapshot.get('text',''))
        for dimension,value in [('日本時間帯',f'{local.hour:02}:00'),('文字数区分','短文' if length<=100 else '中文' if length<=250 else '長文')]:
            features[(snapshot['checkpoint'],snapshot.get('late',False),dimension,value)].append(snapshot)
    lines.extend(['','## 時間帯・文字数の観察比較','|時点|遅延|区分|値|完全取得投稿数|加重反応率|','|---|---|---|---|---:|---:|'])
    for (checkpoint,late,dimension,value),samples in sorted(features.items()):
        complete=[s for s in {(s['account'],s.get('remote_id',s['post_id'])):s for s in samples}.values() if s['metrics'].get('views',0)>0 and all(k in s['metrics'] for k in ('likes','replies','reposts','quotes'))]
        views=sum(s['metrics']['views'] for s in complete)
        reactions=sum(sum(s['metrics'][k] for k in ('likes','replies','reposts','quotes')) for s in complete)
        rate=f'{reactions/views:.2%}' if views else '不明'
        lines.append(f'|{checkpoint}|{late}|{dimension}|{value}|{len(complete)}|{rate}|')
    lines.append('5件未満は参考値です。比較は関連の観察であり、因果関係の証明ではありません。')
    if not selected: lines.append('\n対象期間の分析データはまだありません。')
    lines.extend(['','## ChatGPTへの依頼','上記を踏まえて次の7日分、毎日8:00・19:00・22:00（日本時間）の投稿案21本を作成してください。',
                  '効果は仮説として述べ、誇張・根拠のない断定・個人情報を避けてください。',
                  'ジャンル、読者、口調は私が指定します。日別・時間帯別のバリエーションを作ってください。',
                  '出力は {"posts":[{"id":"一意のID","text":"500文字以内の投稿文"}, ...]} のJSONとしてください。',
                  '改善点と検証する仮説も説明してください。本文の最終確認と承認は私が行います。',''])
    path=Path(output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines),encoding='utf-8')
    atomic_json(marker,{'week':week})
    return {'snapshots':len(selected),'groups':len(groups)}
