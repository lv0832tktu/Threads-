"""Atomic JSON storage and narrowly scoped Git persistence."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from .core import SafeError


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise SafeError('Cannot read configuration/data JSON') from None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as out:
            name = out.name
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def git_persist(paths):
    """No force push; a conflict stops work before a potentially unsafe retry."""
    allowed = {'state/history.sqlite3', 'state/ai_usage.sqlite3', 'posts/posts.json',
               'analytics/insights.json', 'experiments/improvement.json', 'state/token_status.json'}
    if not paths or any(path not in allowed for path in paths):
        raise SafeError('Refusing persistence outside known state files')
    try:
        subprocess.run(['git', 'add', '--', *paths], check=True, capture_output=True)
        changed = subprocess.run(['git', 'diff', '--cached', '--quiet', '--', *paths], capture_output=True)
        if changed.returncode == 0:
            return
        if changed.returncode != 1:
            raise SafeError('Cannot inspect staged state')
        subprocess.run(['git', 'commit', '-m', 'Persist Threads operation state', '--', *paths],
                       check=True, capture_output=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        raise SafeError('State persistence failed; resolve Git permissions or branch conflict') from None
