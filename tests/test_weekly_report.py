"""Regression tests for weekly health report formatting."""
from datetime import datetime, timezone
import unittest

from tools.weekly_report import build_report
from test_support import IsolatedDataTestCase


class WeeklyReportFormattingTests(IsolatedDataTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def test_blockers_and_preflight_use_real_line_breaks(self):
        report = build_report(
            {}, now=self.now, blocker_summary="Top blockers: Celestial Set",
            preflight_checks=["API key blocked", "Telegram disabled"],
        )
        self.assertIn("\n\nTop blockers: Celestial Set", report)
        self.assertIn("\n\nPreflight checks: API key blocked; Telegram disabled", report)
        self.assertNotIn(r"\\n", report)

    def test_empty_preflight_uses_pass_label_on_separate_line(self):
        report = build_report({}, now=self.now)
        self.assertIn("\n\nPreflight checks: PASS", report)
        self.assertNotIn(r"\\n", report)


if __name__ == "__main__":
    unittest.main()
