"""Legacy adapter retained for compatibility; paid API calls are prohibited."""
from .core import SafeError


class Writer:
    def __init__(self, config, key='', transport=None):
        self.config=config

    def generate(self, context):
        raise SafeError('Paid AI API generation is disabled in ChatGPT Plus mode; import weekly drafts instead')
