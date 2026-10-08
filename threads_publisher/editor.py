"""Editor enforces structural checks; a human still reviews facts and wording."""
import re
from difflib import SequenceMatcher
from .core import SafeError


def normalize(text):
    return re.sub(r'\s+', '', text).casefold()


def edit_draft(candidate, existing, max_chars=500, threshold=0.85):
    if not isinstance(candidate, dict):
        raise SafeError('AI response must be an object')
    pieces = [candidate.get(key) for key in ('hook', 'body', 'closing')]
    if any(not isinstance(piece, str) for piece in pieces):
        raise SafeError('AI draft requires hook, body, and closing strings')
    text = '\n'.join(piece.strip() for piece in pieces if piece.strip())
    if not 1 <= len(text) <= max_chars:
        raise SafeError('AI draft length rejected')
    normalized = normalize(text)
    if any(SequenceMatcher(None, normalized, normalize(p['text'])).ratio() >= threshold
           for p in existing):
        raise SafeError('AI draft duplicates an existing post')
    return text
