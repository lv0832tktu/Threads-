import argparse
import json
import os
from .core import Client, History, SafeError, load_post, publish_post
from .storage import git_persist, read_json
from .operations import account_token, operation_lock, token_status, job_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'publish', 'schedule', 'generate', 'insights', 'improve', 'token-status', 'import-week', 'edit-draft', 'approve-draft', 'export-approved', 'report', 'market', 'market-report', 'import-batch', 'schedule-draft', 'edit-record', 'reject-draft', 'keywords', 'import-observations', 'career-report', 'import-competitors', 'trends'], nargs='?', default='check')
    parser.add_argument('--post-id', default='')
    parser.add_argument('--approved-post-id', default='')
    parser.add_argument('--posts', default='posts/posts.json')
    parser.add_argument('--history', default='state/history.sqlite3')
    parser.add_argument('--config', default='config/automation.json')
    parser.add_argument('--account', default='default')
    parser.add_argument('--insights', default='analytics/insights.json')
    parser.add_argument('--improvement', default='experiments/improvement.json')
    parser.add_argument('--usage', default='state/ai_usage.sqlite3')
    parser.add_argument('--research', default='research/sources.json')
    parser.add_argument('--persist-git', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--backfill-metadata', action='store_true')
    parser.add_argument('--private-state', action='store_true')
    parser.add_argument('--from-secret', action='store_true')
    parser.add_argument('--drafts', default='private/posts.json')
    parser.add_argument('--input', default='private/week.json')
    parser.add_argument('--start-date')
    parser.add_argument('--text-file', default='private/edited-text.txt')
    parser.add_argument('--export', default='private/approved-posts.json')
    parser.add_argument('--report', default='private/weekly-report.md')
    parser.add_argument('--weekly-only', action='store_true')
    parser.add_argument('--market-config', default='config/market.json')
    parser.add_argument('--posting-schedule', default='config/posting_schedule.json')
    parser.add_argument('--changes-file', default='private/changes.json')
    parser.add_argument('--publish-datetime')
    parser.add_argument('--keywords-config', default='config/keywords.json')
    parser.add_argument('--trend-config', default='config/trend_sources.json')
    parser.add_argument('--latest', action='store_true')
    parser.add_argument('--weekly', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    history = None
    try:
        if args.command=='import-competitors':
            from .public_sources import import_competitors
            from .weekly import private_path
            with operation_lock():
                private=None
                if args.private_state:
                    from .private_state import PrivateState
                    private=PrivateState();private.restore()
                    history=History('private/history.sqlite3')
                elif args.persist_git:
                    raise SafeError('Manual observations require encrypted state for Git persistence')
                elif os.path.exists('state/private-state.enc'):
                    raise SafeError('Existing encrypted observations require --private-state when importing')
                result=import_competitors(private_path(args.input))
                if private:
                    private.save()
                    if args.persist_git:git_persist(['state/private-state.enc'])
                print(json.dumps(result))
            return 0
        if args.command in ('import-week','import-batch','edit-draft','edit-record','approve-draft','reject-draft','schedule-draft','export-approved','import-observations'):
            if args.persist_git:
                raise SafeError('Private draft operations never commit to Git')
            from .weekly import import_week, edit_text, export_approved, private_path
            from .intake import import_batch, approve_record, schedule_record, edit_record
            from .private_state import export_queue
            if args.command=='import-week':
                result=import_week(args.input,args.drafts,args.start_date,args.account)
            elif args.command=='import-batch':
                from .storage import atomic_json
                target=private_path(args.drafts)
                target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists(): atomic_json(target,{'posts':[]})
                result={'imported':len(import_batch(private_path(args.input),target,args.posting_schedule,args.start_date))}
            elif args.command=='edit-record':
                edit_record(private_path(args.drafts),args.post_id,read_json(private_path(args.changes_file)))
                result={'edited':True,'approved':False}
            elif args.command=='schedule-draft':
                schedule_record(private_path(args.drafts),args.post_id,args.publish_datetime)
                result={'scheduled':True}
            elif args.command=='import-observations':
                from .keyword_research import import_public_observations
                result=import_public_observations(private_path(args.input),private_path('private/manual-public-observations.json'))
            elif args.command=='edit-draft':
                edit_record(private_path(args.drafts),args.post_id,{'text':private_path(args.text_file).read_text(encoding='utf-8').strip()})
                result={'edited':True,'approved':False}
            elif args.command in ('approve-draft','reject-draft'):
                approved=args.command=='approve-draft'
                approve_record(private_path(args.drafts),args.post_id,approved)
                result={'approved':approved}
            else:
                result=export_queue(args.drafts,args.export)
            print(json.dumps(result))
            return 0
        if args.resume and args.command != 'publish':
            raise SafeError('Resume is supported only for explicit manual publication')
        if args.command=='trends':
            if read_json(args.trend_config).get('enabled') is not True:
                print(json.dumps({'disabled':True,'requests':0}))
                return 0
            if not args.private_state:raise SafeError('Trend observations require encrypted private storage')
        if args.command=='keywords' and read_json(args.keywords_config).get('enabled') is not True:
            print(json.dumps({'disabled':True,'requests':0}))
            return 0
        if args.command=='keywords' and not args.private_state:
            raise SafeError('Keyword research requires private encrypted storage')
        if args.command=='market':
            from .market import load_market
            if os.environ.get('THREADS_MARKET_CONFIG'):
                from .storage import atomic_json
                try:
                    secret_config=json.loads(os.environ['THREADS_MARKET_CONFIG'])
                except ValueError:
                    raise SafeError('Private research config Secret is invalid') from None
                atomic_json('private/market-config.json',secret_config)
                args.market_config='private/market-config.json'
            market_config=load_market(args.market_config)
            if not market_config['enabled']:
                print('Public competitor research disabled')
                return 0
            if not market_config['competitors'] and not market_config['keywords']:
                raise SafeError('Configure competitors or search keywords first')
            if not args.private_state:
                raise SafeError('Public research requires private encrypted storage')
        if args.command=='generate':
            print('Paid AI API generation disabled; use import-week with ChatGPT Plus drafts')
            return 0
        if args.private_state:
            args.history='private/history.sqlite3'
            args.insights='private/insights.json'
            args.improvement='private/improvement.json'
            if args.command in ('schedule','publish') and args.posts=='posts/posts.json':
                args.posts='private/approved-posts.json'
        if args.persist_git and not args.private_state and (args.history != 'state/history.sqlite3' or args.posts != 'posts/posts.json'
                                 or args.usage != 'state/ai_usage.sqlite3' or args.insights != 'analytics/insights.json'
                                 or args.improvement != 'experiments/improvement.json'):
            raise SafeError('Git persistence requires standard state paths')
        if args.from_secret:
            from .private_state import materialize_secret_queue
            materialize_secret_queue()
        config = read_json(args.config) if os.path.exists(args.config) or args.command not in ('check', 'publish') or args.account != 'default' else None
        if args.command == 'token-status':
            print(json.dumps(token_status(config, args.account)))
            return 0
        if args.command == 'schedule':
            if os.path.exists(args.posting_schedule):
                config['posting_schedule']=read_json(args.posting_schedule)
            from .scheduler import due_posts
            if args.dry_run:
                posts = read_json(args.posts)['posts']
                print(json.dumps({'due_posts': len(due_posts(posts, config, account=args.account)),
                                  'dry_run': True, 'automatic_enabled': config.get('auto_publish_enabled') is True}))
                return 0
            if config.get('auto_publish_enabled') is not True or os.environ.get('THREADS_AUTO_PUBLISH_ENABLED') != 'true' or os.environ.get('THREADS_PUBLISH_ENABLED') != 'true':
                print('Scheduled publication disabled')
                return 0
        if args.dry_run and args.command not in ('schedule', 'token-status'):
            raise SafeError('Use schedule --dry-run for offline publication planning')
        with operation_lock():
            # Once encrypted durable history exists, manual publication must
            # share it with scheduled publication. Missing keys fail closed.
            if args.command == 'publish' and os.path.exists('state/private-state.enc'):
                args.private_state = True
                args.history = 'private/history.sqlite3'
            private=None
            if args.private_state:
                from .private_state import PrivateState
                private=PrivateState()
                private.restore()
            def save_private():
                private.save()
                if args.persist_git:
                    git_persist(['state/private-state.enc'])
            if args.command=='trends':
                from .public_sources import collect_trends
                history=History(args.history)
                result=collect_trends(args.trend_config,persist=save_private)
                save_private()
                print(json.dumps(result))
                if result.get('errors'):raise SafeError('Some permitted feeds failed; encrypted partial results retained')
            elif args.command=='keywords':
                from .keyword_research import analyze_keywords
                from .market import MarketClient
                history=History(args.history)
                token=account_token(config,args.account)
                identity=Client(token).connect()['id']
                expected=config.get('accounts',{}).get(args.account,{}).get('expected_user_id')
                if expected and identity!=expected: raise SafeError('Connected account does not match configuration')
                result=analyze_keywords(MarketClient(token),args.keywords_config,persist=save_private)
                save_private()
                print(json.dumps(result))
                if result.get('errors'): raise SafeError('Some keyword research requests failed; private results retained')
            elif args.command=='career-report':
                from .career_reports import generate_report
                from datetime import datetime
                history=History(args.history)
                result=generate_report(history,args.insights,'private/keyword-analysis.json',followers_path='private/followers.json')
                if private: save_private()
                if args.persist_git:
                    date=datetime.fromisoformat(result['period_end_exclusive']).date().isoformat()
                    git_persist(['reports/latest.json','reports/latest.md',f'reports/weekly/{date}.json',f'reports/weekly/{date}.md','reports/latest.csv','reports/latest-prompt.md',f'reports/weekly/{date}.csv',f'reports/weekly/{date}-prompt.md'])
                print(json.dumps({'report_saved':True}))
            elif args.command in ('market','market-report'):
                from .market import MarketClient,collect_market,market_report
                if args.command=='market':
                    token=account_token(config,args.account)
                    client=Client(token)
                    identity=client.connect()['id']
                    expected=config.get('accounts',{}).get(args.account,{}).get('expected_user_id')
                    if expected and identity!=expected:raise SafeError('Connected account does not match configuration')
                    result=collect_market(MarketClient(token,limit=market_config['request_limit']),market_config,'private/market-data.json')
                    if not result.get('skipped'):
                        market_report('private/market-data.json','private/insights.json','private/market-report.md')
                else:
                    result=market_report('private/market-data.json','private/insights.json','private/market-report.md')
                if private:
                    history=History(args.history)
                    save_private()
                print(json.dumps(result))
                if result.get('errors'):
                    raise SafeError('Some public research endpoints are unavailable; partial report retained privately')
            elif args.command=='report':
                from .report import weekly_report
                print(json.dumps(weekly_report(args.insights,args.report,weekly_only=args.weekly_only)))
                if private:
                    # Seed the preserved legacy history before first report-only save.
                    history=History(args.history)
                    save_private()
            elif args.command == 'improve':
                from .analyst import write_improvement
                write_improvement(args.insights, args.improvement)
                if private:
                    history=History(args.history)
                    save_private()
                elif args.persist_git:
                    raise SafeError('Improvement reports must use private encrypted state')
                print('Improvement report saved; advisory only')
            else:
                token = account_token(config, args.account) if config else os.environ.get('THREADS_ACCESS_TOKEN')
                client = Client(token)
                client.media_hosts = (config or {}).get('media',{}).get('allowed_hosts',[])
                if args.command == 'check':
                    result = client.connect()
                    expected = config.get('accounts', {}).get(args.account, {}).get('expected_user_id') if config else None
                    if expected and result['id'] != expected:
                        raise SafeError('Connected account does not match configuration')
                    print('Connection check passed')
                else:
                    persist = save_private if private else (lambda: git_persist([args.history])) if args.persist_git else lambda: None
                    history = History(args.history, persist)
                    if args.command == 'publish':
                        post = load_post(args.posts, args.post_id)
                        if ('post_type' in post or post.get('approval_digest') or args.resume) and not args.private_state:
                            raise SafeError('Typed publication and resume require private encrypted state')
                        if post.get('account', 'default') != args.account:
                            raise SafeError('Post does not belong to the selected account')
                        expected = config.get('accounts', {}).get(args.account, {}).get('expected_user_id') if config else None
                        if expected and client.connect()['id'] != expected:
                            raise SafeError('Connected account does not match configuration')
                        if args.resume:
                            from .publisher import publish_content
                            publish_content(client,history,post,enabled=os.environ.get('THREADS_PUBLISH_ENABLED')=='true',approved_id=args.approved_post_id,resume=True)
                        else:
                            publish_post(client, history, post, os.environ.get('THREADS_PUBLISH_ENABLED') == 'true', args.approved_post_id)
                        print('Publication recorded')
                    elif args.command == 'schedule':
                        from .scheduler import run_scheduled
                        result = run_scheduled(client, history, args.posts, config, enabled=True, auto_enabled=True, account=args.account)
                        print(json.dumps(result))
                    elif args.command == 'insights':
                        from .insights import InsightsClient, collect, collect_latest
                        user_id = client.connect()['id']
                        expected = config.get('accounts', {}).get(args.account, {}).get('expected_user_id')
                        if expected and user_id != expected:
                            raise SafeError('Connected account does not match configuration')
                        metrics = config.get('insights', {}).get('metrics')
                        insights_client=InsightsClient(token,metrics=metrics)
                        if args.latest:
                            baseline = collect(history,insights_client,args.insights,account=user_id,backfill=True) if args.backfill_metadata else None
                            result = collect_latest(history,insights_client,args.insights,account=user_id,label='weekly' if args.weekly else 'daily')
                            if baseline:
                                result['errors'] += baseline['errors']
                            if private:
                                from .storage import atomic_json
                                from datetime import datetime, timezone
                                followers_path='private/followers.json'
                                followers=read_json(followers_path) if os.path.exists(followers_path) else {'snapshots':[]}
                                followers['snapshots'].append({'account':user_id,'collected_at':datetime.now(timezone.utc).isoformat(),**insights_client.fetch_followers(user_id)})
                                atomic_json(followers_path,followers)
                        else:
                            result = collect(history,insights_client,args.insights,account=user_id,backfill=args.backfill_metadata)
                        if private:
                            save_private()
                        elif args.persist_git:
                            raise SafeError('Insights require private encrypted state for Git persistence')
                        print(json.dumps({key: result[key] for key in ('collected', 'skipped', 'requested', 'limited') if key in result}))
                        if result['errors']:
                            raise SafeError('Some Insights requests failed or have no permitted metrics; inspect permissions and retry later')
            job_summary(args.command, True)
        return 0
    except (SafeError, OSError, ValueError, TypeError, KeyError) as error:
        # Unexpected malformed local data and OS errors must not dump environment or headers.
        print(str(error) if isinstance(error, SafeError) else 'Operation failed; validate local configuration and storage')
        job_summary(args.command, False)
        return 1
    finally:
        if history is not None:
            history.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
