import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock
from threads_publisher.core import Client, History, SafeError, load_post, publish_post


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'history.sqlite3')
        self.post = {'id': 'one', 'text': 'hello', 'approved': True}
        self.client = Mock()
        self.client.connect.return_value = {'id': 'account'}
        self.client.create.return_value = 'container'
        self.client.publish.return_value = 'published'

    def history(self, persist=lambda: None):
        history = History(self.path, persist)
        self.addCleanup(history.db.close)
        return history

    def test_default_disabled(self):
        with self.assertRaises(SafeError):
            publish_post(self.client, self.history(), self.post)
        self.client.connect.assert_not_called()

    def test_all_approval_gates(self):
        for enabled, approved, record in [(False, 'one', True), (True, '', True), (True, 'other', True), (True, 'one', False)]:
            with self.assertRaises(SafeError):
                publish_post(self.client, self.history(), dict(self.post, approved=record), enabled, approved)
        self.client.create.assert_not_called()

    def test_publish_and_durable_duplicate_prevention(self):
        persist = Mock()
        history = self.history(persist)
        self.assertEqual(publish_post(self.client, history, self.post, True, 'one'), 'published')
        self.assertEqual(persist.call_count, 2)
        with self.assertRaises(SafeError):
            publish_post(self.client, self.history(), self.post, True, 'one')
        self.client.publish.assert_called_once_with('account', 'container')

    def test_failed_persistence_prevents_api_creation(self):
        history = self.history(Mock(side_effect=SafeError('push failed')))
        with self.assertRaises(SafeError):
            publish_post(self.client, history, self.post, True, 'one')
        self.client.create.assert_not_called()

    def test_uncertain_publication_blocks_retry(self):
        self.client.publish.side_effect = SafeError('timeout')
        with self.assertRaises(SafeError):
            publish_post(self.client, self.history(), self.post, True, 'one')
        with self.assertRaises(SafeError):
            publish_post(self.client, self.history(), self.post, True, 'one')
        self.client.create.assert_called_once()

    def test_account_history_is_separate(self):
        history = self.history()
        history.reserve('a', 'one')
        history.reserve('b', 'one')

    def test_connection_uses_get_and_header(self):
        def transport(request, timeout):
            self.assertEqual(request.get_method(), 'GET')
            self.assertNotIn('secret', request.full_url)
            self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
            return io.BytesIO(b'{"id":"123","username":"test"}')
        self.assertEqual(Client('secret', transport).connect()['id'], '123')

    def test_expired_token_sanitized(self):
        def transport(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, 'secret', {}, io.BytesIO(b'{"error":{"code":190,"message":"secret"}}'))
        with self.assertRaises(SafeError) as caught:
            Client('secret', transport).connect()
        self.assertIn('Token expired', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_network_and_api_errors_sanitized(self):
        for error in [urllib.error.URLError('secret'), urllib.error.HTTPError('secret', 429, 'secret', {}, io.BytesIO(b'not json'))]:
            with self.assertRaises(SafeError) as caught:
                Client('secret', Mock(side_effect=error)).connect()
            self.assertNotIn('secret', str(caught.exception))

    def test_json_validation(self):
        path = Path(self.tmp.name) / 'posts.json'
        path.write_text(json.dumps({'posts': [self.post]}))
        self.assertEqual(load_post(path, 'one'), self.post)
        for posts in [[self.post, self.post], [dict(self.post, text='')], [dict(self.post, text='x' * 501)]]:
            path.write_text(json.dumps({'posts': posts}))
            with self.assertRaises(SafeError):
                load_post(path, 'one')
