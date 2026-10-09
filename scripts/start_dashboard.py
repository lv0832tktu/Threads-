"""Local Windows launcher. Never reads the Threads access token or calls an API."""
import getpass
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def open_when_ready(process, port):
    url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(90):
        if process.poll() is not None:
            return
        try:
            with opener.open(url + '/_stcore/health', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(1)


def main():
    os.chdir(ROOT)
    if not os.environ.get('THREADS_STATE_KEY'):
        if not sys.stdin.isatty():
            print('秘密入力が可能なコマンド画面から起動してください。')
            return 1
        print('既存のTHREADS_STATE_KEYを貼り付けてEnter（表示・保存しません）。')
        print('GitHub Secretsの値は読み戻せません。登録時に保管した同じキーを使用してください。')
        key = getpass.getpass('暗号化キー: ').strip()
        try:
            from cryptography.fernet import Fernet
            Fernet(key.encode())
        except (ValueError, TypeError):
            print('キーの形式を確認してください。既存の履歴用キーを新規生成しないでください。')
            return 1
        os.environ['THREADS_STATE_KEY'] = key
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    for name in list(env):
        if name.startswith('THREADS_ACCESS_TOKEN'):
            env.pop(name)
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run',
        'threads_publisher/dashboard.py', '--server.address', '127.0.0.1',
        '--server.port', str(port), '--server.headless', 'true',
        '--server.maxUploadSize', '1', '--browser.gatherUsageStats', 'false'], env=env)
    threading.Thread(target=open_when_ready, args=(process, port), daemon=True).start()
    print(f'管理画面: http://127.0.0.1:{port} （このパソコン専用）')
    print('終了するときはこのウィンドウでCtrl+Cを押してください。')
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
