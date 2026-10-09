"""Japanese operator UI: loopback-only; no API calls or publication."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from threads_publisher.management import require_loopback,ManagementError,read_history
from threads_publisher.core import SafeError
from threads_publisher.operator import Operator
from threads_publisher.storage import read_json


def main():
    import streamlit as st
    st.set_page_config(page_title='Threads AI・仕事術・お金・節約',layout='wide')
    try:require_loopback(st.get_option('server.address'))
    except ManagementError:
        st.error('外部公開は禁止です。127.0.0.1を指定して起動してください。');st.stop()
    root=Path(__file__).resolve().parents[1];os.chdir(root);operator=Operator(root)
    st.title('Threads AI・仕事術・お金・節約 管理画面')
    st.caption('ローカル専用。API取得・投稿・Actions実行・Secrets変更は行いません。承認と予約は別の操作です。')
    def action(callback):
        try:
            result=callback();st.toast('保存しました。公開・取得は実行していません。');st.rerun();return result
        except (SafeError,ManagementError) as error:st.error(str(error))
        except (OSError,ValueError,TypeError,KeyError,StopIteration):st.error('入力形式・ローカル設定を確認してください。保存に失敗しました。')
        return None
    def data(path,default):
        try:return read_json(root/path)
        except SafeError:return default
    tabs=st.tabs(['はじめに','競合登録','情報源','レポート・ChatGPT','投稿案・承認','予約・書き出し','履歴・エラー'])
    with tabs[0]:
        st.markdown('1. 既存の暗号化状態を読み込む\n2. 公開競合URL・CSVを登録する\n3. 保存済み実績から週報を作る\n4. プロンプトをChatGPTへコピーする\n5. 21本を取り込み、確認・承認する\n6. 承認済みだけ予約し、Secret用JSONを書き出す')
        st.info('毎日08:00・19:00・22:00（日本時間）。定期実行と自動公開は初期無効です。')
        if st.button('既存の暗号化状態を読み込む'):action(operator.restore)
        st.caption('THREADS_STATE_KEYは起動環境へ安全に注入します。キーやトークンを画面へ入力・表示しません。')
        st.markdown('詳しい手順は docs/operations-dashboard.md を参照してください。')
    with tabs[1]:
        st.caption('URLを登録しても開きません。確認できた数値だけ入力し、未取得は空欄にしてください。本文は保存しません。')
        keywords=['AI','仕事術','お金','節約','ChatGPT','生成AI','AI活用','業務効率化','家計管理','新NISA','固定費','生活防衛資金']
        with st.form('competitor'):
            url=st.text_input('Threads公開投稿URL')
            theme=st.selectbox('分析テーマ',keywords)
            hook=st.selectbox('冒頭の形式',['未確認','質問型','数字・手順型','説明・体験型'])
            chars=st.text_input('文字数（未確認は空欄）')
            observed=st.text_input('確認日時（タイムゾーン付き）',datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'))
            numbers={name:st.text_input(label+'（未取得は空欄）') for name,label in [('likes','いいね'),('replies','返信'),('reposts','リポスト'),('quotes','引用')]}
            if st.form_submit_button('公開情報を登録'):
                row={'url':url,'theme':theme,'hook':None if hook=='未確認' else hook,'characters':chars,'observed_at':observed,**numbers}
                action(lambda:operator.competitors(json.dumps({'observations':[row]}).encode(),'.json'))
        upload=st.file_uploader('競合CSV／JSON／URLリスト',type=['csv','json','txt'],key='competitor-upload')
        if st.button('ファイルを登録') and upload:
            action(lambda:operator.competitors(upload.getvalue(),Path(upload.name).suffix.lower()))
        st.dataframe(data('private/manual-public-observations.json',{'observations':[]})['observations'],width='stretch')
    with tabs[2]:
        st.warning('配信元の公式性・利用条件を確認できたものだけ登録してください。登録しても収集は無効です。')
        with st.form('source'):
            url=st.text_input('公式RSS／Atom／JSONのURL')
            kind=st.selectbox('配信形式',['rss','atom','json'])
            reference=st.text_input('取得・利用条件の根拠URL')
            confirmed=st.checkbox('公式配信元であり、取得・利用条件を確認した')
            if st.form_submit_button('確認済み配信元を登録'):action(lambda:operator.source(url,kind,reference,confirmed))
        sources=data('config/trend_sources.json',{'sources':[]})['sources']
        st.dataframe(sources,width='stretch')
        if sources:
            remove=st.selectbox('削除する配信元',[source['url'] for source in sources])
            if st.button('配信元を削除'):action(lambda:operator.remove_source(remove))
        st.caption('経済産業省・厚生労働省・金融庁・消費者庁・国税庁などの公式情報は候補です。今回の環境では利用条件を確認できず、収集先を事前登録していません。')
    with tabs[3]:
        st.caption('自分の実績取得は公式Insightsの承認済みActionsで別途実行します。この画面は保存済みデータだけ使います。')
        if st.button('最新の保存済みデータで週報を更新'):action(operator.report)
        report=root/'reports/latest.md';prompt=root/'reports/latest-prompt.md'
        if report.exists():st.markdown(report.read_text())
        else:st.info('週報はまだありません。')
        if prompt.exists():
            st.subheader('ChatGPTへコピーする指示');st.code(prompt.read_text(),language='markdown')
            st.download_button('プロンプトを保存',prompt.read_bytes(),'next-week-prompt.md')
        csv=root/'reports/latest.csv'
        if csv.exists():st.download_button('週報CSVを保存',csv.read_bytes(),'weekly-report.csv','text/csv')
    with tabs[4]:
        st.caption('取り込みは全件未承認に戻します。編集すると承認と予約を解除します。画像は権利・SHA256も確認してください。')
        upload=st.file_uploader('ChatGPTの21本JSON',type=['json'],key='draft-upload')
        today=datetime.now(ZoneInfo('Asia/Tokyo')).date()
        start=st.date_input('翌週の開始日',today+timedelta(days=(7-today.weekday())%7 or 7))
        mode=st.selectbox('週21本の形式',['通常文章21本','文章・画像・ツリー各7本'])
        if st.button('未承認の投稿案として取り込む') and upload:
            action(lambda:operator.import_drafts(upload.getvalue(),start.isoformat(),mode=='通常文章21本'))
        posts=data('private/posts.json',{'posts':[]})['posts']
        for post in posts:
            with st.expander(post['id']+' / '+post.get('publish_status','下書き')):
                st.write(post['text']);st.write('予定案：'+str(post.get('planned_at') or post.get('scheduled_at','未設定')))
                try:planned=datetime.fromisoformat(post.get('planned_at') or post.get('scheduled_at')).astimezone(ZoneInfo('Asia/Tokyo'))
                except (TypeError,ValueError):planned=datetime.now(ZoneInfo('Asia/Tokyo'))
                planned_day=st.date_input('予定日（変更は再承認が必要）',planned.date(),key='plan-day-'+post['id'])
                clocks=['08:00','19:00','22:00']
                planned_clock=st.selectbox('予定時刻',clocks,index=clocks.index(planned.strftime('%H:%M')) if planned.strftime('%H:%M') in clocks else 0,key='plan-clock-'+post['id'])
                if st.button('予定案を変更（予約はまだしません）',key='plan-save-'+post['id']):action(lambda:operator.edit(post['id'],{'planned_at':planned_day.isoformat()+'T'+planned_clock+':00+09:00'}))
                st.caption('JSON編集項目：text／thread_items／image_urls／image_sha256／rights_confirmed／theme／keywords／planned_at。ID・承認・予約状態は変更できません。')
                if post.get('post_type')=='thread':
                    items=[st.text_area('ツリー本文 '+str(index+1),item['text'],key=f'body-{post["id"]}-{index}') for index,item in enumerate(post['thread_items'])]
                    if st.button('本文を保存（承認・予約を解除）',key='body-save-'+post['id']):action(lambda:operator.edit(post['id'],{'text':items[0],'thread_items':[{'text':value} for value in items]}))
                else:
                    body=st.text_area('本文を編集',post['text'],key='body-'+post['id'])
                    if st.button('本文を保存（承認・予約を解除）',key='body-save-'+post['id']):action(lambda:operator.edit(post['id'],{'text':body}))
                changes=st.text_area('変更項目のJSON','{}',key='changes-'+post['id'])
                if st.button('編集を保存（再承認が必要）',key='edit-'+post['id']):action(lambda:operator.edit(post['id'],json.loads(changes)))
                confirmed=st.checkbox('本文・根拠・画像権利・日時を確認した',key='review-'+post['id'])
                if st.button('この投稿案を承認',key='approve-'+post['id'],disabled=not confirmed):action(lambda:operator.approve(post['id'],True))
                if st.button('差し戻す',key='reject-'+post['id']):action(lambda:operator.approve(post['id'],False))
    with tabs[5]:
        posts=data('private/posts.json',{'posts':[]})['posts']
        st.dataframe([{'投稿ID':p['id'],'予定案':p.get('planned_at'),'予約日時':p.get('scheduled_at'),'承認':p.get('approved',False),'状態':p.get('publish_status')} for p in posts],width='stretch')
        approved=[p['id'] for p in posts if p.get('approved')]
        if approved:
            chosen=st.selectbox('承認済み投稿を予約',approved)
            if st.button('承認した内容を予定日時で予約'):action(lambda:operator.schedule(chosen))
        if st.button('Secret用の承認済み予約JSONを作成'):
            from threads_publisher.private_state import export_queue
            action(lambda:export_queue('private/posts.json','private/approved-posts.json'))
        for index in range(1,5):
            path=root/'private'/('approved-posts.json' if index==1 else f'approved-posts-{index}.json')
            if path.exists():st.download_button('予約JSON '+str(index)+' を保存（公開しない）',path.read_bytes(),path.name,key='export-'+str(index))
        st.warning('GitHub Repository SecretsのTHREADS_SCHEDULE_JSON／_2／_3／_4へ対応するファイルを設定します。未使用分も空JSONに更新し、古い予約を残さないでください。画面からSecrets変更や投稿は行いません。')
    with tabs[6]:
        history=root/'private/history.sqlite3'
        if not history.exists():history=root/'state/history.sqlite3'
        st.dataframe(read_history(history),width='stretch')
        for name in ('trend-history.json','report-status.json','followers.json'):
            path='private/'+name
            if (root/path).exists():st.subheader(name);st.json(data(path,{}))
        st.caption('実行失敗はGitHubのActions実行概要でも確認してください。pending／partial／公開結果不明は履歴を削除せず照合します。')

if __name__=='__main__':main()
