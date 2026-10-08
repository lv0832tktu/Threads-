"""Optional LOCAL dashboard.

Run: streamlit run threads_publisher/dashboard.py --server.address 127.0.0.1
Do not forward its port or expose it through a tunnel. Public deployment is not
supported; an authenticated, authorized gateway and a deployment security review
are prerequisites for a future public UI. Local users have operator access.
This UI cannot publish, change GitHub Secrets/variables, or launch Actions.
"""
import json
from pathlib import Path
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Streamlit executes this file directly, rather than as a package module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from threads_publisher.management import (ManagementError, read_history,
                                         require_loopback, set_approval,
                                         set_auto_publish, set_schedule)


def main():
    import streamlit as st
    st.set_page_config(page_title='Threads local management', layout='wide')
    try:
        require_loopback(st.get_option('server.address'))
    except ManagementError as error:
        st.error(str(error))
        st.stop()
    root = Path(__file__).resolve().parent.parent
    st.title('Threads 管理画面（ローカル専用）')
    st.caption('この画面は投稿・Actions実行を行いません。下書きはprivate内に保存し、承認済み予約をGitHub Secretへ反映します。ポート転送や外部公開は禁止です。')
    posts_path = root / 'private/posts.json' if (root / 'private/posts.json').exists() else root / 'posts/posts.json'
    config_path = root / 'config/automation.json'
    try:
        posts = json.loads(posts_path.read_text(encoding='utf-8'))['posts']
        config = json.loads(config_path.read_text(encoding='utf-8'))
        history = read_history(root / 'private/history.sqlite3' if (root / 'private/history.sqlite3').exists() else root / 'state/history.sqlite3')
    except (OSError, ValueError, KeyError) as error:
        st.error('設定または履歴を読み込めません。ローカルファイルを確認してください。')
        st.stop()
    accounts = sorted({str(post.get('account', 'default')) for post in posts})
    account = st.selectbox('アカウント（投稿設定上の識別子）', ['すべて'] + accounts)
    selected = [post for post in posts if account == 'すべて' or str(post.get('account', 'default')) == account]
    # History.account is the authenticated remote account ID. Do not silently
    # compare it to a configuration alias or hide records with uncertain mapping.
    st.caption('履歴は全アカウントを表示します。account列はThreads上のアカウントIDです。')
    columns = st.columns(3)
    columns[0].metric('投稿案', len(selected))
    columns[1].metric('公開成功', sum(row['status'] == 'published' for row in history))
    columns[2].metric('未完了・失敗（要確認）', sum(row['status'] != 'published' for row in history))
    calendar, drafts, past, analysis, settings = st.tabs(['投稿予定', '下書き・承認', '履歴', '分析', '運用設定'])
    with calendar:
        st.caption('日時はAsia/Tokyo基準。予定時刻を過ぎても未公開の投稿を含みます。')
        st.dataframe([{'id': p['id'], 'scheduled_at': p.get('scheduled_at'), 'approved': p.get('approved', False), 'text': p['text']} for p in selected], width='stretch')
    with drafts:
        for post in selected:
            with st.expander(post['id']):
                st.write(post['text'])
                if posts_path.parent.name == 'private':
                    edited = st.text_area('本文を編集（保存すると未承認に戻ります）', value=post['text'], key='text-'+post['id'])
                    if st.button('本文を保存', key='edit-'+post['id']):
                        from threads_publisher.weekly import edit_text
                        edit_text(posts_path, post['id'], edited)
                        st.rerun()
                st.write('承認済み' if post.get('approved') is True else '未承認')
                if post.get('editor_review'):
                    st.json(post['editor_review'])
                if post.get('review'):
                    st.json(post['review'])
                if st.checkbox('予約日時を設定する', value=bool(post.get('scheduled_at')), key='schedule-' + post['id']):
                    try:
                        initial = datetime.fromisoformat(post['scheduled_at']).astimezone(ZoneInfo('Asia/Tokyo'))
                    except (KeyError, ValueError):
                        initial = datetime.now(ZoneInfo('Asia/Tokyo')).replace(hour=8, minute=0, second=0, microsecond=0)
                    day = st.date_input('予約日（日本時間）', value=initial.date(), key='day-' + post['id'])
                    clock = st.time_input('予約時刻（日本時間）', value=initial.time().replace(tzinfo=None), key='clock-' + post['id'])
                    if st.button('予約日時を保存', key='save-schedule-' + post['id']):
                        set_schedule(posts_path, post['id'], datetime.combine(day, clock, tzinfo=ZoneInfo('Asia/Tokyo')).isoformat())
                        st.rerun()
                approve, reject = st.columns(2)
                if approve.button('承認', key='approve-' + post['id']):
                    set_approval(posts_path, post['id'], True)
                    st.rerun()
                if reject.button('差し戻し（未承認）', key='reject-' + post['id']):
                    set_approval(posts_path, post['id'], False)
                    st.rerun()
    with past:
        st.dataframe(history, width='stretch')
        st.caption('pendingは公開結果が不確定な可能性があります。履歴削除や自動再投稿はできません。')
    with analysis:
        snapshots = root / 'private/insights.json'
        if snapshots.exists():
            try:
                data = json.loads(snapshots.read_text(encoding='utf-8'))
                snapshots_data = data.get('snapshots', [])
                st.dataframe(snapshots_data, width='stretch')
                chart = [{'post': row['post_id'] + '/' + row['checkpoint'], **row.get('metrics', {})}
                         for row in snapshots_data]
                if chart:
                    st.bar_chart(chart, x='post', y=['views', 'likes', 'replies', 'reposts', 'quotes'])
                st.caption('欠測指標は0に置換しません。異なる取得時点の数値は単純比較できません。')
            except (OSError, ValueError):
                st.warning('分析データのJSONを確認してください。')
        else:
            st.info('分析データはまだありません。取得はこの画面から実行されません。')
    with settings:
        enabled = st.checkbox('設定ファイルの自動公開を許可', value=config.get('auto_publish_enabled') is True)
        st.warning('実際の自動公開にはGitHubのTHREADS_PUBLISH_ENABLEDとTHREADS_AUTO_PUBLISH_ENABLEDもtrueである必要があります。変更後はGitHubへの反映と承認が必要です。')
        if st.button('ローカル設定を保存'):
            set_auto_publish(config_path, enabled)
            st.success('ローカル設定を保存しました。Actionsの設定・実行は変更していません。')


if __name__ == '__main__':
    main()
