"""Prepare an encrypted candidate only; never replace active state or Secrets."""
import base64
import json
import os
from pathlib import Path
import sqlite3
from .core import SafeError
from .operations import operation_lock
from .private_state import FILES, PrivateState


def cipher(value):
    from cryptography.fernet import Fernet
    try:
        if not value:
            raise ValueError()
        return Fernet(value.encode())
    except (ValueError, TypeError, AttributeError):
        raise SafeError('Migration key missing or invalid; no key logged') from None


def validate(plaintext):
    try:
        document = json.loads(plaintext)
        if document.get('version') != 1 or not isinstance(document.get('files'), dict):
            raise ValueError()
        if any(name not in FILES for name in document['files']):
            raise ValueError()
        files = {name: base64.b64decode(value, validate=True) for name, value in document['files'].items()}
        data = files['history.sqlite3']
        db = sqlite3.connect(':memory:')
        try:
            db.deserialize(data)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError()
            count = db.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
        finally:
            db.close()
        return files, count
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        raise SafeError('Migration source validation failed; preserve original state') from None


def prepare(root='.', old_key=None, next_key=None):
    root = Path(root).resolve()
    active = root/'state/private-state.enc'
    candidate = root/'state/private-state.next.enc'
    backup = root/'state/private-state.before-migration.enc'
    new = cipher(next_key or os.environ.get('THREADS_STATE_KEY_NEXT'))
    with operation_lock(root/'state/operations.lock'):
        if candidate.exists() or backup.exists():
            raise SafeError('Migration output already exists; refusing overwrite')
        # Fail rather than silently omit a local, potentially newer history/draft.
        if (root/'private').exists():
            raise SafeError('Local private state exists; reconcile and back it up before migration')
        original = active.read_bytes() if active.exists() else None
        if original is not None:
            old_value = old_key or os.environ.get('THREADS_STATE_KEY')
            if old_value == (next_key or os.environ.get('THREADS_STATE_KEY_NEXT')):
                raise SafeError('Migration requires a different new key')
            old = cipher(old_value)
            try:
                plaintext = old.decrypt(original)
            except Exception:
                raise SafeError('Old key cannot authenticate state; no migration written') from None
        else:
            legacy = root/'state/history.sqlite3'
            if not legacy.is_file() or any(Path(str(legacy)+suffix).exists() for suffix in ('-wal','-journal')):
                raise SafeError('Stable legacy history required; never initialize empty history')
            plaintext = json.dumps({'version':1,'files':{'history.sqlite3':base64.b64encode(legacy.read_bytes()).decode()}}).encode()
        files, count = validate(plaintext)
        encrypted = new.encrypt(plaintext)
        recovered = new.decrypt(encrypted)
        if recovered != plaintext or validate(recovered)[0] != files:
            raise SafeError('Migration verification failed')
        if original is not None:
            PrivateState._write(backup, original)
        PrivateState._write(candidate, encrypted)
        return {'verified':True,'source':'encrypted' if original is not None else 'legacy',
                'files':len(files),'history_rows':count,'active_state_changed':False}


def main():
    try:
        print(json.dumps(prepare()))
        return 0
    except SafeError as error:
        print(str(error))  # All migration errors above are fixed strings.
        return 1
    except OSError:
        print('Migration preparation failed; original state and Secrets unchanged. Check local files without sharing secrets.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
