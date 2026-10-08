"""Director owns the policy; model output never confers publication approval."""
import json
from pathlib import Path
from .core import SafeError


def load_config(path='config/automation.json'):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'))
        ai = config['ai']
        if config['timezone'] != 'Asia/Tokyo' or not isinstance(ai, dict):
            raise ValueError()
        for key, ceiling in [('daily_drafts', 20), ('daily_request_limit', 20),
                             ('monthly_request_limit', 1000), ('max_output_tokens', 4000),
                             ('max_text_chars', 500)]:
            if type(ai[key]) is not int or not 1 <= ai[key] <= ceiling:
                raise ValueError()
        if not 0.5 <= ai['duplicate_threshold'] <= 1:
            raise ValueError()
        if type(ai['enabled']) is not bool:
            raise ValueError()
        return config
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SafeError('Invalid automation configuration') from None
