"""Human-reviewed text queue operations; no API or key required for drafts."""
from datetime import datetime
import hashlib
import json
from .core import SafeError
from .management import _edit_json
from .schema import approval_digest, normalize_post
from .intake import timestamp


def review_digest(posts):
    return hashlib.sha256(json.dumps(posts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def approve_text_batch(path, ids, reviewed_digest):
    def change(document):
        selected = [post for post in document['posts'] if post['id'] in ids]
        if not ids or len(set(ids)) != len(ids) or len(selected) != len(ids):
            raise SafeError('Select unique existing drafts')
        if review_digest(selected) != reviewed_digest:
            raise SafeError('Drafts changed after review; review again')
        for post in selected:
            normalize_post(post)
            if post.get('post_type') != 'text' or post.get('publish_status') not in ('pending_approval','draft'):
                raise SafeError('Only pending text drafts can be batch approved')
        for post in selected:
            post.update(approved=True, approval_status='approved', publish_status='approved', review_status='approved')
            post['approval_digest'] = approval_digest(post)
    _edit_json(path, change)


def schedule_text_batch(path, ids, slots, now=None):
    now = now or datetime.now().astimezone()
    def change(document):
        selected = [post for post in document['posts'] if post['id'] in ids]
        if not ids or len(set(ids)) != len(ids) or len(selected) != len(ids):
            raise SafeError('Select unique approved drafts')
        candidates = []
        for post in selected:
            if post.get('post_type') != 'text' or post.get('approved') is not True or post.get('publish_status') != 'approved':
                raise SafeError('Approve text drafts before scheduling')
            if post.get('approval_digest') != approval_digest(post):
                raise SafeError('Draft changed after approval')
            when = timestamp(post.get('planned_at'))
            date = datetime.fromisoformat(when)
            if date <= now or date.strftime('%H:%M') not in slots or date.second or date.microsecond:
                raise SafeError('Choose future configured posting slots')
            candidates.append(normalize_post({**post,'scheduled_at':when}))
        combined = [post for post in document['posts'] if post['id'] not in ids] + candidates
        occupied = [(post.get('account','default'), datetime.fromisoformat(timestamp(post['scheduled_at']))) for post in combined if post.get('scheduled_at')]
        if len(occupied) != len(set(occupied)):
            raise SafeError('Scheduled slot already occupied')
        for post, candidate in zip(selected, candidates):
            post.update(candidate)
            post['publish_status'] = 'scheduled'
    _edit_json(path, change)


def weekly_prompt(start):
    return f'''翌週の投稿案を21本作成してください。開始日は{start}、Asia/Tokyoで毎日08:00・19:00・22:00に1本ずつです。
ジャンルは就活・転職、AI、仕事術、お金、節約の5つをバランスよく扱ってください。
各投稿は500文字以内。冒頭で課題を示し、具体的な実行方法と短い締めを入れてください。
架空の体験、金融商品の利益保証、根拠のない断定、過度な不安の煽り、他人の文章コピーは禁止。
採用・税制・制度など変わる情報は公式出典と確認日を本文で示し、確認できないものはテーマを変更してください。
分析レポートがなくても作れます。同じ内容を繰り返さないでください。
出力はコードブロックなしのJSONだけ。形式は{{"posts":[{{"id":"week-{start}-01","post_type":"text","category":"AI","theme":"テーマ","keywords":["キーワード"],"text":"本文","approved":false}}]}}。
01〜21の順に日時を割り当てるので日時・承認・予約を付与しないでください。全件、人間が確認する下書きです。'''
