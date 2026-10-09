"""Offline simple-posting UI test using disposable drafts and no keys."""
import json,os,sys,shutil,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import streamlit as st
from streamlit.testing.v1 import AppTest
from threads_publisher.simple_posting import review_digest
root=Path(__file__).resolve().parents[1]
st.config.get_config_options();st.config._set_option('server.address','127.0.0.1','test')
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);shutil.copytree(root/'config',p/'config');(p/'threads_publisher').mkdir();shutil.copyfile(root/'threads_publisher/dashboard.py',p/'threads_publisher/dashboard.py')
 with patch.dict(os.environ,{}),patch('socket.getaddrinfo',side_effect=AssertionError('No external API')):
  os.environ.pop('THREADS_STATE_KEY',None)
  app=AppTest.from_file(str(p/'threads_publisher/dashboard.py'),default_timeout=15).run()
  assert not app.exception
  app.date_input(key='simple-week').set_value(__import__('datetime').date(2030,1,7))
  app.text_area(key='simple-json').set_value(json.dumps({'posts':[{'id':f'first-week-{i:02d}','post_type':'text','text':f'オフライン確認用 {i}'} for i in range(1,22)]}))
  app.run()
  next(b for b in app.button if b.label=='21本を下書きとして取り込む').click().run()
  assert not app.exception
  rows=json.loads((p/'private/posts.json').read_text())['posts'];assert len(rows)==21 and not any(x['approved'] for x in rows)
  app.text_area(key='simple-body-first-week-01').set_value('保存前の変更')
  app.run();app.checkbox(key='simple-confirm-'+review_digest(rows)).check().run()
  assert next(b for b in app.button if b.label=='確認した投稿をまとめて承認').disabled
  app.button(key='simple-save-first-week-01').click().run()
  rows=json.loads((p/'private/posts.json').read_text())['posts']
  app.checkbox(key='simple-confirm-'+review_digest(rows)).check().run()
  next(b for b in app.button if b.label=='確認した投稿をまとめて承認').click().run()
  next(b for b in app.button if b.label=='承認済み投稿をまとめて予約').click().run()
  next(b for b in app.button if b.label=='GitHub用の予約ファイルを作る').click().run()
  assert not app.exception
  rows=json.loads((p/'private/posts.json').read_text())['posts'];assert all(x['publish_status']=='scheduled' for x in rows)
  assert (p/'private/approved-posts-4.json').exists()
  assert not (p/'state/private-state.enc').exists()
  print('PASS: no key, 21 draft intake, unsaved edit approval blocked, human batch review, scheduling, four exports; no network')
 os.chdir(root)
