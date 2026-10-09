import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('dashboard_launcher', Path(__file__).resolve().parents[1]/'scripts/start_dashboard.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_browser_opens_only_after_local_health_check(self):
        process = Mock()
        process.poll.return_value = None
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(launcher.urllib.request, 'build_opener', return_value=opener), patch.object(launcher.webbrowser, 'open') as browser:
            launcher.open_when_ready(process, 8501)
        opener.open.assert_called_once_with('http://127.0.0.1:8501/_stcore/health', timeout=1)
        browser.assert_called_once_with('http://127.0.0.1:8501')

    def test_failed_process_does_not_open_browser(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(launcher.webbrowser, 'open') as browser:
            launcher.open_when_ready(process, 8501)
        browser.assert_not_called()

    def test_health_failure_waits_before_opening(self):
        process = Mock()
        process.poll.side_effect = [None, 1]
        opener = Mock()
        opener.open.side_effect = OSError('unavailable')
        with patch.object(launcher.urllib.request, 'build_opener', return_value=opener), patch.object(launcher.webbrowser, 'open') as browser, patch.object(launcher.time, 'sleep') as wait:
            launcher.open_when_ready(process, 8501)
        browser.assert_not_called()
        wait.assert_called_once_with(1)
