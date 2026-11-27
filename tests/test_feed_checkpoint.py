import unittest

from tools.feed_checkpoint import (
    parse_feed_timestamp,
    scan_checkpoint_requires_cold_start,
)


class FeedCheckpointTests(unittest.TestCase):
    def test_zulu_timestamp_is_parsed_as_utc(self):
        self.assertEqual(parse_feed_timestamp("2033-05-18T03:33:20Z"), 2_000_000_000)

    def test_naive_timestamp_is_treated_as_utc(self):
        self.assertEqual(parse_feed_timestamp("2033-05-18T03:33:20"), 2_000_000_000)

    def test_offset_timestamp_is_normalized_to_utc(self):
        self.assertEqual(
            parse_feed_timestamp("2033-05-18T05:33:20+02:00"),
            2_000_000_000,
        )

    def test_missing_and_invalid_timestamps_are_not_guessed(self):
        self.assertIsNone(parse_feed_timestamp(None))
        self.assertIsNone(parse_feed_timestamp(""))
        self.assertIsNone(parse_feed_timestamp("not-a-date"))

    def test_missing_checkpoint_requires_cold_start(self):
        self.assertTrue(scan_checkpoint_requires_cold_start(None, now=2_000_000_000))

    def test_checkpoint_older_than_configured_gap_requires_cold_start(self):
        now = 2_000_000_000
        self.assertTrue(
            scan_checkpoint_requires_cold_start(
                now - 86_401, now=now, max_success_gap_seconds=86_400
            )
        )

    def test_recent_checkpoint_keeps_incremental_mode(self):
        now = 2_000_000_000
        self.assertFalse(
            scan_checkpoint_requires_cold_start(
                now - 86_399, now=now, max_success_gap_seconds=86_400
            )
        )

    def test_checkpoint_more_than_five_minutes_in_future_is_rejected(self):
        now = 2_000_000_000
        self.assertTrue(scan_checkpoint_requires_cold_start(now + 301, now=now))

    def test_small_clock_skew_is_tolerated(self):
        now = 2_000_000_000
        self.assertFalse(
            scan_checkpoint_requires_cold_start(now + 300, now=now)
        )

    def test_non_finite_or_non_positive_checkpoint_is_rejected(self):
        now = 2_000_000_000
        for value in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertTrue(scan_checkpoint_requires_cold_start(value, now=now))


if __name__ == "__main__":
    unittest.main()
