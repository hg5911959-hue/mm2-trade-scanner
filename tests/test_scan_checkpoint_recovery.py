import unittest

import scanner
from test_support import IsolatedDataTestCase


class ScanCheckpointRecoveryTests(IsolatedDataTestCase):
    def test_missing_checkpoint_requires_bounded_cold_start(self):
        self.assertTrue(scanner.scan_checkpoint_requires_cold_start(None, now=2_000_000_000))

    def test_checkpoint_older_than_one_day_requires_bounded_cold_start(self):
        now = 2_000_000_000
        self.assertTrue(
            scanner.scan_checkpoint_requires_cold_start(
                now - scanner.MAX_SUCCESS_GAP_SECONDS - 1,
                now=now,
            )
        )

    def test_recent_checkpoint_keeps_incremental_feed_mode(self):
        now = 2_000_000_000
        self.assertFalse(
            scanner.scan_checkpoint_requires_cold_start(
                now - scanner.MAX_SUCCESS_GAP_SECONDS + 1,
                now=now,
            )
        )

    def test_future_checkpoint_is_rejected_as_stale(self):
        now = 2_000_000_000
        self.assertTrue(scanner.scan_checkpoint_requires_cold_start(now + 60, now=now))


if __name__ == "__main__":
    unittest.main()
