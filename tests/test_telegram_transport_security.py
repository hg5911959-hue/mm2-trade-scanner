"""Regression tests for Telegram credential redirect protection."""
import urllib.request
from test_support import IsolatedDataTestCase

import scanner
from tools import weekly_report


class TelegramRedirectSecurityTests(IsolatedDataTestCase):
    def _request(self):
        return urllib.request.Request(
            "https://api.telegram.org/botplaceholder/sendMessage",
            data=b"chat_id=123",
        )

    def test_scanner_alert_transport_refuses_redirects(self):
        handler = scanner._RejectTelegramRedirects()
        self.assertIsNone(handler.redirect_request(
            self._request(), None, 302, "Found", {},
            "https://attacker.example/collect",
        ))

    def test_weekly_report_transport_refuses_redirects(self):
        handler = weekly_report._RejectTelegramRedirects()
        self.assertIsNone(handler.redirect_request(
            self._request(), None, 302, "Found", {},
            "https://attacker.example/collect",
        ))


if __name__ == "__main__":
    unittest.main()
