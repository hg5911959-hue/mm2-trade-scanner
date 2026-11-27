from __future__ import annotations

from datetime import datetime, timezone

from test_support import IsolatedDataTestCase
from tools.retry_after import retry_after_seconds


class RetryAfterTests(IsolatedDataTestCase):
    def test_delta_seconds_are_parsed(self):
        self.assertEqual(retry_after_seconds("3"), 3.0)
        self.assertEqual(retry_after_seconds("1.5"), 1.5)

    def test_negative_delta_seconds_are_clamped_to_zero(self):
        self.assertEqual(retry_after_seconds("-4"), 0.0)

    def test_http_date_is_parsed_against_an_injected_clock(self):
        now = datetime(2015, 10, 21, 7, 27, 55, tzinfo=timezone.utc)
        self.assertEqual(
            retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=now),
            5.0,
        )

    def test_past_http_date_returns_zero(self):
        now = datetime(2015, 10, 21, 7, 28, 10, tzinfo=timezone.utc)
        self.assertEqual(
            retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=now),
            0.0,
        )

    def test_empty_invalid_and_non_finite_headers_return_none(self):
        self.assertIsNone(retry_after_seconds(""))
        self.assertIsNone(retry_after_seconds("not-a-date"))
        self.assertIsNone(retry_after_seconds("nan"))
        self.assertIsNone(retry_after_seconds("inf"))
