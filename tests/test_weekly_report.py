"""Regression tests for weekly report formatting and Telegram transport."""
from datetime import datetime, timezone
import unittest
import urllib.request

from tools import weekly_report
from test_support import IsolatedDataTestCase


class WeeklyReportTests(IsolatedDataTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def test_blockers_and_preflight_use_real_line_breaks(self):
        report = weekly_report.build_report(
            {}, now=self.now, blocker_summary="Top blockers: Celestial Set",
            preflight_checks=["API key blocked", "Telegram disabled"],
        )
        self.assertIn("\n\nTop blockers: Celestial Set", report)
        self.assertIn("\n\nPreflight checks: API key blocked; Telegram disabled", report)
        self.assertNotIn(r"\\n", report)

    def test_empty_preflight_uses_pass_label_on_separate_line(self):
        report = weekly_report.build_report({}, now=self.now)
        self.assertIn("\n\nPreflight checks: PASS", report)
        self.assertNotIn(r"\\n", report)

    def test_weekly_report_redirects_are_rejected(self):
        handler = weekly_report._RejectTelegramRedirects()
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
