"""Regression tests for shared Telegram redirect protection."""
import unittest
import urllib.request

from test_support import IsolatedDataTestCase
from tools.telegram_transport import RejectTelegramRedirects, TELEGRAM_OPENER


class TelegramTransportTests(IsolatedDataTestCase):
    def test_opener_contains_redirect_rejection_handler(self):
        self.assertTrue(any(isinstance(handler, RejectTelegramRedirects) for handler in TELEGRAM_OPENER.handlers))

    def test_bot_token_request_is_not_forwarded_to_redirect_target(self):
        handler = RejectTelegramRedirects()
        request = urllib.request.Request(
            "https://api.telegram.org/botplaceholder/sendMessage",
            data=b"chat_id=123",
        )
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://attacker.example/collect"
        )
        self.assertIsNone(redirected)


if __name__ == "__main__":
    unittest.main()
