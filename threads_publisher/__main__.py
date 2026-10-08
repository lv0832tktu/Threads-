import argparse
import json
import os
from .core import Client, History, SafeError, load_post, publish_post
from .storage import git_persist, read_json
from .operations import account_token, operation_lock, token_status, job_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'publish', 'schedule', 'generate', 'insights', 'improve', 'token-status'], nargs='?', default='check')
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
    args = parser.parse_args()
    history = None
    try:
        if args.persist_git and (args.history != 'state/history.sqlite3' or args.posts != 'posts/posts.json'
                                 or args.usage != 'state/ai_usage.sqlite3' or args.insights != 'analytics/insights.json'
                                 or args.improvement != 'experiments/improvement.json'):
            raise SafeError('Git persistence requires standard state paths')
        config = read_json(args.config) if os.path.exists(args.config) or args.command not in ('check', 'publish') or args.account != 'default' else None
        if args.command == 'token-status':
            print(json.dumps(token_status(config, args.account)))
            return 0
        if args.command == 'schedule':
            from .scheduler import due_posts
            if args.dry_run:
                posts = read_json(args.posts)['posts']
                print(json.dumps({'due_post_ids': [p['id'] for p in due_posts(posts, config, account=args.account)],
                                  'dry_run': True, 'automatic_enabled': config.get('auto_publish_enabled') is True}))
                return 0
            if config.get('auto_publish_enabled') is not True or os.environ.get('THREADS_AUTO_PUBLISH_ENABLED') != 'true' or os.environ.get('THREADS_PUBLISH_ENABLED') != 'true':
                print('Scheduled publication disabled')
                return 0
        if args.dry_run and args.command not in ('schedule', 'token-status'):
            raise SafeError('Use schedule --dry-run for offline publication planning')
        with operation_lock():
            if args.command == 'generate':
                from .ai import generate_drafts
                improvement = read_json(args.improvement) if os.path.exists(args.improvement) else None
                persist = (lambda: git_persist([args.posts, args.usage])) if args.persist_git else lambda: None
                sources = read_json(args.research).get('sources', []) if os.path.exists(args.research) else []
                result = generate_drafts(args.config, args.posts, args.usage, persist=persist, improvement=improvement, sources=sources)
                print(json.dumps(result))
            elif args.command == 'improve':
                from .analyst import write_improvement
                write_improvement(args.insights, args.improvement)
                if args.persist_git:
                    git_persist([args.improvement])
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
                    persist = (lambda: git_persist([args.history])) if args.persist_git else lambda: None
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
                        if args.persist_git:
                            paths = [args.insights] if os.path.exists(args.insights) else []
                            if args.backfill_metadata:
                                paths.append(args.history)
                            if paths:
                                git_persist(paths)
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
