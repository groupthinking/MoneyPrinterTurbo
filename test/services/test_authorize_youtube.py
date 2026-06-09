"""Unit tests for authorize_youtube.py"""
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestAuthorizeYoutubeMain(unittest.TestCase):
    def _run_main(self):
        from authorize_youtube import main
        return main

    def test_get_auth_url_runtime_error_prints_and_returns(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", side_effect=RuntimeError("no creds")), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            main()
        output = mock_out.getvalue()
        self.assertIn("Error:", output)
        self.assertIn("config.toml", output)

    def test_success_flow_saves_token(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", return_value="https://auth.url/"), \
             patch("authorize_youtube.exchange_code") as mock_exchange, \
             patch("authorize_youtube.webbrowser.open") as mock_browser, \
             patch("builtins.input", return_value="mycode"), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            main()
        mock_browser.assert_called_once_with("https://auth.url/")
        mock_exchange.assert_called_once_with("mycode")
        output = mock_out.getvalue()
        self.assertIn("Done!", output)

    def test_exchange_code_exception_prints_and_returns(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", return_value="https://auth.url/"), \
             patch("authorize_youtube.exchange_code", side_effect=ValueError("bad code")), \
             patch("authorize_youtube.webbrowser.open"), \
             patch("builtins.input", return_value="badcode"), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            main()
        output = mock_out.getvalue()
        self.assertIn("Authorization failed", output)
        self.assertIn("bad code", output)

    def test_opens_browser_with_auth_url(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", return_value="https://example.com/auth"), \
             patch("authorize_youtube.exchange_code"), \
             patch("authorize_youtube.webbrowser.open") as mock_browser, \
             patch("builtins.input", return_value="code123"), \
             patch("sys.stdout", new_callable=StringIO):
            main()
        mock_browser.assert_called_once_with("https://example.com/auth")

    def test_strips_whitespace_from_code(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", return_value="https://auth.url/"), \
             patch("authorize_youtube.exchange_code") as mock_exchange, \
             patch("authorize_youtube.webbrowser.open"), \
             patch("builtins.input", return_value="  mycode  "), \
             patch("sys.stdout", new_callable=StringIO):
            main()
        mock_exchange.assert_called_once_with("mycode")

    def test_prints_auth_url_as_fallback(self):
        from authorize_youtube import main
        with patch("authorize_youtube.get_auth_url", return_value="https://fallback.url/"), \
             patch("authorize_youtube.exchange_code"), \
             patch("authorize_youtube.webbrowser.open"), \
             patch("builtins.input", return_value="code"), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            main()
        output = mock_out.getvalue()
        self.assertIn("https://fallback.url/", output)


if __name__ == "__main__":
    unittest.main()
