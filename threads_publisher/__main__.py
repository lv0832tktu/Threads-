import argparse
import os
import subprocess
from .core import Client, History, SafeError, load_post, publish_post


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'publish'], nargs='?', default='check')
    parser.add_argument('--post-id', default='')
    parser.add_argument('--approved-post-id', default='')
    parser.add_argument('--posts', default='posts/posts.json')
    parser.add_argument('--history', default='state/history.sqlite3')
    parser.add_argument('--persist-git', action='store_true')
    args = parser.parse_args()
    try:
        client = Client(os.environ.get('THREADS_ACCESS_TOKEN'))
        if args.command == 'check':
            client.connect()
            print('Connection check passed')
            return 0
        post = load_post(args.posts, args.post_id)
        if args.persist_git and args.history != 'state/history.sqlite3':
            raise SafeError('Git persistence requires the standard history path')

        def persist():
            try:
                for command in [ ['git', 'add', '--', 'state/history.sqlite3'],
                                 ['git', 'commit', '-m', 'Persist Threads publication history'],
                                 ['git', 'push', 'origin', 'HEAD:main'] ]:
                    subprocess.run(command, check=True, capture_output=True)
            except subprocess.CalledProcessError:
                raise SafeError('History persistence failed; resolve Git permissions or branch protection') from None

        history = History(args.history, persist if args.persist_git else lambda: None)
        publish_post(client, history, post,
                     os.environ.get('THREADS_PUBLISH_ENABLED') == 'true', args.approved_post_id)
        print('Publication recorded')
        return 0
    except SafeError as error:
        print(str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
