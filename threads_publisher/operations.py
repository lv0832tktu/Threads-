"""Local operation serialization, safe failure summaries and token expiry planning."""
import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .core import SafeError


@contextmanager
def operation_lock(path='state/operations.lock'):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafeError('Another local operation is running') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def account_token(config, alias):
    accounts = config.get('accounts', {})
    if alias not in accounts:
        raise SafeError('Unknown configured account')
    name = accounts[alias].get('token_env', '')
    if not name.startswith('THREADS_ACCESS_TOKEN') or not name.replace('_', '').isalnum():
        raise SafeError('Account token must use a THREADS_ACCESS_TOKEN-prefixed variable')
    return os.environ.get(name)


def token_status(config, alias='default', now=None):
    account = config.get('accounts', {}).get(alias)
    if account is None:
        raise SafeError('Unknown configured account')
    expiry = account.get('token_expires_at')
    if not expiry:
        return {'account': alias, 'expiry': 'unknown', 'action': 'Record issuer expiry; use read-only connection check to validate the token'}
    try:
        date = datetime.fromisoformat(expiry)
        if date.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise SafeError('token_expires_at requires an ISO timezone datetime') from None
    remaining = (date - (now or datetime.now(timezone.utc))).total_seconds() / 86400
    return {'account': alias, 'days_remaining': round(remaining, 2),
            'action': 'reauthorize' if remaining <= 0 else 'refresh_long_lived_token_in_secure_admin_environment' if remaining <= 7 else 'monitor'}


def job_summary(operation, success):
    # Only enumerated operation names and a fixed outcome are written.
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path and operation in {'check', 'publish', 'schedule', 'generate', 'insights', 'improve', 'token-status'}:
        with open(path, 'a', encoding='utf-8') as out:
            out.write(f'### Threads {operation}\n\nOutcome: {"success" if success else "failed; inspect safe error and pending history before retry"}.\n')
