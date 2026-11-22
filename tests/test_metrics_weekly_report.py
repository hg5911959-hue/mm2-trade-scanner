import json, tempfile, unittest
from test_support import IsolatedDataTestCase
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from tools.metrics import update_metrics
from tools.weekly_report import build_report, send_report


class MetricsReportTests(IsolatedDataTestCase):
    def test_metrics_atomic_counter_and_14_day_retention(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "metrics.json"
            update_metrics(p, increments={"scans": 1, "alerts_sent": 2}, now=datetime(2026, 11, 13, tzinfo=timezone.utc))
            data = json.loads(p.read_text())
            self.assertEqual(data["2026-11-13"]["alerts_sent"], 2)
            self.assertFalse(p.with_name("metrics.json.tmp").exists())

    def test_report_from_fixture_metrics(self):
        report = build_report(
            {"2026-11-13": {"scans": 10, "scan_failures": 1, "alerts_sent": 8, "alerts_deferred": 3, "rblxvalue_requests": 12}},
            {"stale": False}, {"permanent": False}, 60,
            datetime(2026, 11, 13, tzinfo=timezone.utc),
        )
        self.assertIn("Scan success: 90.0%", report)
        self.assertIn("Alerts sent: 8 | deferred: 3", report)
        self.assertIn("12/60", report)
        self.assertIn("fresh", report)

    def test_report_with_empty_metrics(self):
        self.assertIn("Scan success: 0.0%", build_report({}, now=datetime(2026, 11, 13, tzinfo=timezone.utc)))

    def test_report_optional_sections_use_real_line_breaks(self):
        report = build_report(
            {}, now=datetime(2026, 11, 13, tzinfo=timezone.utc),
            blocker_summary="Blockers", preflight_checks=["PASS"],
        )
        self.assertIn("\n\nBlockers", report)
        self.assertIn("\n\nPreflight checks: PASS", report)
        self.assertNotIn("\\n", report)


    def test_weekly_report_requires_telegram_api_ok_true(self):
        class Response:
            status = 200
            def read(self, limit):
                return b'{"ok":false,"description":"chat not found"}'
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch("tools.weekly_report.TELEGRAM_OPENER.open", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "API rejected"):
                send_report("weekly status", token="placeholder", chat="123")

    def test_weekly_report_accepts_confirmed_telegram_delivery(self):
        class Response:
            status = 200
            def read(self, limit): return b'{"ok":true}'
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch("tools.weekly_report.TELEGRAM_OPENER.open", return_value=Response()):
            self.assertTrue(send_report("weekly status", token="placeholder", chat="123"))

    def test_weekly_report_rejects_malformed_telegram_response(self):
        class Response:
            status = 200
            def read(self, limit): return b"not-json"
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch("tools.weekly_report.TELEGRAM_OPENER.open", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "invalid response"):
                send_report("weekly status", token="placeholder", chat="123")

    def test_report_missing_metrics_file_is_represented_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "missing.json"
            self.assertFalse(missing.exists())
            self.assertIn("Scan success: 0.0%", build_report({}))


if __name__ == "__main__":
    unittest.main()
