from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class ScanFeedQualityReportingTests(IsolatedDataTestCase):
    def test_malformed_feed_alert_preserves_counts_and_samples(self):
        output = io.StringIO()
        with (
            patch.object(scanner, "should_alert_malformed_cards", return_value=True),
            patch.object(scanner, "send_maintenance_alert") as alert,
            contextlib.redirect_stdout(output),
        ):
            scanner._report_scan_feed_quality(
                {
                    "malformed_count": 2,
                    "html_malformed_count": 1,
                    "html_parse_warning_count": 3,
                    "candidate_count": 20,
                    "unparsed_ad_ids": ["a1", "a2"],
                    "html_parse_warning_ids": ["h1"],
                    "malformed_samples": ["sample one", "sample two"],
                    "pagination_complete": True,
                }
            )

        alert.assert_called_once()
        args, kwargs = alert.call_args
        self.assertEqual(args[0], "malformed-feed-cards")
        self.assertIn("5/20 (25.0%)", args[1])
        self.assertIn("sample one", args[1])
        self.assertEqual(kwargs, {"cooldown_seconds": 3600})
        self.assertIn("api_unparsed=2", output.getvalue())
        self.assertIn("html_unparsed_or_malformed=3", output.getvalue())

    def test_unidentified_records_and_incomplete_pagination_are_reported(self):
        output = io.StringIO()
        with (
            patch.object(scanner, "should_alert_malformed_cards", return_value=False),
            patch.object(scanner, "send_maintenance_alert") as alert,
            contextlib.redirect_stdout(output),
        ):
            scanner._report_scan_feed_quality(
                {
                    "unidentified_feed_record_count": 2,
                    "unidentified_feed_samples": ["dict", "missing-id"],
                    "pagination_complete": False,
                    "pagination_pages": 7,
                }
            )

        self.assertEqual([call.args[0] for call in alert.call_args_list], [
            "feed-records-without-stable-id",
            "pagination-incomplete",
        ])
        self.assertEqual(
            [call.kwargs["cooldown_seconds"] for call in alert.call_args_list],
            [3600, 1800],
        )
        self.assertIn("missing stable IDs: 2", output.getvalue())
        self.assertIn("incomplete after 7 pages", output.getvalue())

    def test_clean_complete_feed_does_not_send_maintenance_alerts(self):
        with (
            patch.object(scanner, "should_alert_malformed_cards", return_value=True),
            patch.object(scanner, "send_maintenance_alert") as alert,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            scanner._report_scan_feed_quality({"pagination_complete": True})

        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
