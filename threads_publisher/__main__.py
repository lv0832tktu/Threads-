import argparse
import json
import os
from .core import Client, History, SafeError, load_post, publish_post
from .storage import git_persist, read_json
from .operations import account_token, operation_lock, token_status, job_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'publish', 'schedule', 'generate', 'insights', 'improve', 'token-status', 'import-week', 'edit-draft', 'approve-draft', 'export-approved', 'report'], nargs='?', default='check')
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
    args = parser.parse_args()
    history = None
    try:
        if args.command in ('import-week','edit-draft','approve-draft','export-approved'):
            if args.persist_git:
                raise SafeError('Private draft operations never commit to Git')
            from .weekly import import_week, edit_text, export_approved, private_path
            from .management import set_approval
            if args.command=='import-week':
                result=import_week(args.input,args.drafts,args.start_date,args.account)
            elif args.command=='edit-draft':
                edit_text(args.drafts,args.post_id,private_path(args.text_file).read_text(encoding='utf-8').strip())
                result={'edited':True,'approved':False}
            elif args.command=='approve-draft':
                set_approval(private_path(args.drafts),args.post_id,True)
                result={'approved':True}
            else:
                result=export_approved(args.drafts,args.export)
            print(json.dumps(result))
            return 0
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
            private=None
            if args.private_state:
                from .private_state import PrivateState
                private=PrivateState()
                private.restore()
            def save_private():
                private.save()
                if args.persist_git:
                    git_persist(['state/private-state.enc'])
            if args.command=='report':
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
                        if post.get('account', 'default') != args.account:
                            raise SafeError('Post does not belong to the selected account')
                        expected = config.get('accounts', {}).get(args.account, {}).get('expected_user_id') if config else None
                        if expected and client.connect()['id'] != expected:
                            raise SafeError('Connected account does not match configuration')
                        publish_post(client, history, post, os.environ.get('THREADS_PUBLISH_ENABLED') == 'true', args.approved_post_id)
                        print('Publication recorded')
                    elif args.command == 'schedule':
                        from .scheduler import run_scheduled
                        result = run_scheduled(client, history, args.posts, config, enabled=True, auto_enabled=True, account=args.account)
                        print(json.dumps(result))
                    elif args.command == 'insights':
                        from .insights import InsightsClient, collect
                        user_id = client.connect()['id']
                        expected = config.get('accounts', {}).get(args.account, {}).get('expected_user_id')
                        if expected and user_id != expected:
                            raise SafeError('Connected account does not match configuration')
                        metrics = config.get('insights', {}).get('metrics')
                        result = collect(history, InsightsClient(token, metrics=metrics), args.insights, account=user_id, backfill=args.backfill_metadata)
                        if private:
                            save_private()
                        elif args.persist_git:
                            raise SafeError('Insights require private encrypted state for Git persistence')
                        print(json.dumps({key: result[key] for key in ('collected', 'skipped')}))
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
