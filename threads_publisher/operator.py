"""Local UI operations. No API calls, publication, secrets editing or Actions."""
import json
from pathlib import Path
from .core import History,SafeError
from .private_state import PrivateState,export_queue
from .storage import atomic_json,read_json
from .operations import operation_lock
from .intake import import_batch,edit_record,approve_record,schedule_record
from .public_sources import import_competitors,allowed_url

class Operator:
    def __init__(self,root):
        self.root=Path(root).resolve();self.private=self.root/'private'
        if not self.private.resolve().is_relative_to(self.root):raise SafeError('privateフォルダがリポジトリ外にあります')
    def state_action(self,action):
        with operation_lock(self.root/'state/operations.lock'):
            state=PrivateState(self.private,self.root/'state/private-state.enc')
            state.restore(self.root/'state/history.sqlite3')
            history=History(self.private/'history.sqlite3');history.db.close()
            result=action()
            state.save()
            return result
    def restore(self):
        with operation_lock(self.root/'state/operations.lock'):
            PrivateState(self.private,self.root/'state/private-state.enc').restore(self.root/'state/history.sqlite3')
    def upload(self,name,data):
        if not isinstance(data,bytes) or len(data)>1024*1024:raise SafeError('アップロードは1MiB以内です')
        path=self.private/name
        if not path.resolve().is_relative_to(self.private.resolve()):raise SafeError('保存先が不正です')
        self.private.mkdir(parents=True,exist_ok=True);PrivateState._write(path,data)
        return path
    def competitors(self,data,suffix):
        if suffix not in ('.csv','.json','.txt'):raise SafeError('CSV・JSON・URLリストだけ登録できます')
        source=self.upload('competitor-upload'+suffix,data)
        return self.state_action(lambda:import_competitors(source,self.private/'manual-public-observations.json'))
    def import_drafts(self,data,start_date,text_only=False):
        source=self.upload('draft-upload.json',data);target=self.private/'posts.json'
        if not target.exists():atomic_json(target,{'posts':[]})
        return import_batch(source,target,self.root/'config/posting_schedule.json',start_date,text_only=text_only)
    def edit(self,post_id,changes):
        if isinstance(changes,dict) and 'theme' in changes and 'topic' not in changes:changes={**changes,'topic':changes['theme']}
        edit_record(self.private/'posts.json',post_id,changes)
    def approve(self,post_id,approved):approve_record(self.private/'posts.json',post_id,approved)
    def schedule(self,post_id):
        target=self.private/'posts.json';post=next(p for p in read_json(target)['posts'] if p['id']==post_id)
        from .scheduler import parse_time
        slots=read_json(self.root/'config/posting_schedule.json')['slots'].values()
        if parse_time(post['planned_at']).strftime('%H:%M') not in slots:raise SafeError('旧時刻の案です。予定日時を08:00・19:00・22:00に編集し、再承認してください')
        schedule_record(target,post_id)
    def source(self,url,kind,reference,confirmed):
        if confirmed is not True:raise SafeError('公式性と利用条件の確認が必要です。未確認の情報源は登録できません')
        if kind not in ('rss','atom','json'):raise SafeError('RSS・Atom・JSONだけ登録できます')
        from urllib.parse import urlsplit
        hosts=list(dict.fromkeys([urlsplit(url).hostname,urlsplit(reference).hostname]))
        if None in hosts:raise SafeError('HTTPSの公式配信URLと利用条件URLを入力してください')
        allowed_url(url,hosts);allowed_url(reference,hosts)
        path=self.root/'config/trend_sources.json'
        from .management import _edit_json
        def update(config):
            if any(s['url']==url for s in config['sources']):raise SafeError('同じ配信元は登録済みです')
            if len(config['sources'])>=5:raise SafeError('配信元は最大5件です')
            config['sources'].append({'url':url,'kind':kind,'permission_reference':reference,'official_source':True,'permission_confirmed':True})
            config['allowed_hosts']=list(dict.fromkeys(config['allowed_hosts']+hosts));config['enabled']=False
        _edit_json(path,update)
    def remove_source(self,url):
        from .management import _edit_json
        def update(config):
            config['sources']=[s for s in config['sources'] if s['url']!=url];config['enabled']=False
        _edit_json(self.root/'config/trend_sources.json',update)
    def report(self):
        def action():
            from .career_reports import generate_report
            history=History(self.private/'history.sqlite3')
            try:return generate_report(history,self.private/'insights.json',self.private/'keyword-analysis.json',self.root/'reports',followers_path=self.private/'followers.json',force=True)
            finally:history.db.close()
        return self.state_action(action)
