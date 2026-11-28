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
        self.assertTrue(scanner.scan_checkpoint_requires_cold_start(now + 301, now=now))


    def test_long_scan_window_uses_a_bounded_finish_buffer(self):
        self.assertEqual(scanner.minimum_cycle_start_window(540), 67.5)
        self.assertTrue(scanner.should_start_scan_cycle(67.5, 540))
        self.assertFalse(scanner.should_start_scan_cycle(67.4, 540))

    def test_backlog_recheck_is_scheduled_every_fourth_cycle(self):
        self.assertFalse(scanner.should_run_backlog_recheck(1, every=4))
        self.assertFalse(scanner.should_run_backlog_recheck(2, every=4))
        self.assertFalse(scanner.should_run_backlog_recheck(3, every=4))
        self.assertTrue(scanner.should_run_backlog_recheck(4, every=4))

    def test_short_scan_window_keeps_a_proportional_buffer(self):
        self.assertEqual(scanner.minimum_cycle_start_window(240), 60.0)
        self.assertEqual(scanner.minimum_cycle_start_window(60), 30.0)


if __name__ == "__main__":
    unittest.main()
