"""Regression tests for Telegram bot-token redirect protection."""
import unittest
import urllib.request

import telegram_delivery
from test_support import IsolatedDataTestCase


class TelegramDeliverySecurityTests(IsolatedDataTestCase):
    def test_opener_installs_redirect_rejection_handler(self):
        handler = next(
            item for item in telegram_delivery._TELEGRAM_OPENER.handlers
            if isinstance(item, telegram_delivery.RejectTelegramRedirects)
        )
        request = urllib.request.Request(
            "https://api.telegram.org/botplaceholder/sendMessage",
            data=b"chat_id=123",
        )

        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/collect",
        )

        self.assertIsNone(redirected)


if __name__ == "__main__":
    unittest.main()
