"""Regression tests for shared Telegram redirect protection."""
import unittest
import urllib.request

from test_support import IsolatedDataTestCase
from tools.telegram_transport import (
    RejectTelegramRedirects,
    TELEGRAM_OPENER,
    telegram_alert_delay,
)


class TelegramTransportTests(IsolatedDataTestCase):
    def test_private_chat_uses_one_message_per_second_safety_margin(self):
        self.assertEqual(telegram_alert_delay("private", 0), 1.05)
        self.assertEqual(telegram_alert_delay(" PRIVATE ", 10), 1.05)

    def test_group_chat_retains_the_twenty_per_minute_safety_floor(self):
        self.assertEqual(telegram_alert_delay("group", 0), 3.2)
        self.assertEqual(telegram_alert_delay("group", 4.5), 4.5)

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
