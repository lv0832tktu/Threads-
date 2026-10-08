"""Research packages human-supplied evidence; no automated fact verification claim."""
from .core import SafeError


def research_context(sources=None):
    sources = sources or []
    if not isinstance(sources, list) or len(sources) > 10:
        raise SafeError('Research sources must be a list of at most 10 items')
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('summary'), str) or len(source['summary']) > 1000:
            raise SafeError('Each research source requires a summary')
    return {'sources': sources, 'verification': 'human_review_required'}
