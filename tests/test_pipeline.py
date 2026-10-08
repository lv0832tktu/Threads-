"""Offline functional exercise of generation -> approval -> publication -> analysis."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from threads_publisher.ai import generate_drafts
from threads_publisher.core import Client, History
from threads_publisher.insights import InsightsClient, collect
from threads_publisher.management import set_approval, set_schedule
from threads_publisher.scheduler import run_scheduled
from threads_publisher.analyst import write_improvement


class PipelineTests(unittest.TestCase):
    def test_full_pipeline_with_in_memory_api_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            config=json.loads(Path('config/automation.json').read_text())
            config['ai'].update(enabled=True,theme='暮らし',audience='一般',daily_drafts=1)
            config_path=root/'config.json'
            config_path.write_text(json.dumps(config))
            posts=root/'posts.json'
            posts.write_text('{"posts":[]}')
            class Writer:
                def generate(self,context):
                    return {'hook':'作業を始める小さな工夫','body':'今日は机を整理してから始めました。','closing':'皆さんの工夫も知りたいです。'}
            generated=generate_drafts(config_path,posts,root/'usage.sqlite3',writer=Writer(),now=datetime(2026,10,8,tzinfo=ZoneInfo('Asia/Tokyo')))
            self.assertEqual(generated['generated'],1)
            post=json.loads(posts.read_text())['posts'][0]
            self.assertIs(post['approved'],False)
            calls=[]
            def transport(request,timeout):
                calls.append((request.get_method(),request.full_url))
                self.assertNotIn('offline-token',request.full_url)
                if '/insights?' in request.full_url:
                    metric=request.full_url.split('=')[-1]
                    value=100 if metric=='views' else 2
                    return io.BytesIO(json.dumps({'data':[{'name':metric,'values':[{'value':value}]}]}).encode())
                if '/threads_publish' in request.full_url:
                    self.assertEqual(json.loads(request.data),{'creation_id':'container'})
                    return io.BytesIO(b'{"id":"media"}')
                if request.get_method()=='POST':
                    self.assertEqual(json.loads(request.data)['text'],post['text'])
                    return io.BytesIO(b'{"id":"container"}')
                return io.BytesIO(b'{"id":"account"}')
            history=History(root/'history.sqlite3')
            self.addCleanup(history.db.close)
            client=Client('offline-token',transport)
            now=datetime.now(timezone.utc)
            set_schedule(posts,post['id'],now.isoformat())
            config['auto_publish_enabled']=True
            # Unapproved generated drafts cannot reach even the connection endpoint.
            self.assertEqual(run_scheduled(client,history,posts,config,True,True,now)['published'],0)
            self.assertEqual(calls,[])
            set_approval(posts,post['id'],True)
            self.assertEqual(run_scheduled(client,history,posts,config,True,True,now)['published'],1)
            self.assertEqual(run_scheduled(client,history,posts,config,True,True,now)['published'],0)
            self.assertEqual(sum('/threads_publish' in url for _,url in calls),1)
            snapshots=root/'insights.json'
            result=collect(history,InsightsClient('offline-token',transport),snapshots,datetime.now(timezone.utc)+timedelta(hours=25),account='account')
            self.assertEqual(result['collected'],1)
            advice=write_improvement(snapshots,root/'improvement.json')
            self.assertEqual(advice['analysis']['posts'][0]['reaction_rate'],.08)
            self.assertEqual(advice['experiment_id'],'baseline')
            self.assertTrue(advice['advisory_only'])
