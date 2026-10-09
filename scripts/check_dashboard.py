"""Offline UI smoke test using disposable state; run with dashboard dependencies."""
import os,sys,tempfile,shutil,json
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from threads_publisher.operator import Operator
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder)
 shutil.copytree(root/'config',p/'config');(p/'threads_publisher').mkdir()
 shutil.copyfile(root/'threads_publisher/dashboard.py',p/'threads_publisher/dashboard.py')
 os.environ['THREADS_STATE_KEY']=Fernet.generate_key().decode()
 import streamlit as st
 st.config.get_config_options()
 st.config._set_option('server.address','127.0.0.1','test')
 with patch('socket.getaddrinfo',side_effect=AssertionError('External network forbidden')):
  app=AppTest.from_file(str(p/'threads_publisher/dashboard.py'),default_timeout=15).run()
  assert not app.exception
  app.text_input[0].set_value('https://threads.com/@sample/post/test')
  next(x for x in app.button if x.label=='公開情報を登録').click().run()
  assert not app.exception
  assert len(json.loads((p/'private/manual-public-observations.json').read_text())['observations'])==1
  operator=Operator(p)
  operator.import_drafts(json.dumps({'posts':[{'id':f'w{i}','text':f'確認用 {i}'} for i in range(21)]}).encode(),'2026-10-12',True)
  app.run()
  app.text_area(key='body-w0').set_value('編集後の確認用本文')
  app.button(key='body-save-w0').click().run()
  app.checkbox(key='review-w0').check().run()
  app.button(key='approve-w0').click().run()
  next(x for x in app.button if x.label=='承認した内容を予定日時で予約').click().run()
  rows=json.loads((p/'private/posts.json').read_text())['posts']
  assert rows[0]['text']=='編集後の確認用本文' and rows[0]['publish_status']=='scheduled'
  next(x for x in app.button if x.label=='最新の保存済みデータで週報を更新').click().run()
  assert not app.exception
  assert (p/'reports/latest.md').exists() and app.code
  assert len(app.tabs)==7
  print('PASS: 7 tabs, competitor registration, 21 draft intake, UI edit/approval/schedule, report/prompt; no external network')

 os.chdir(root)
