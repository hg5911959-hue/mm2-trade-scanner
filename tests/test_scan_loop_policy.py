from __future__ import annotations

from test_support import IsolatedDataTestCase
from tools.scan_loop_policy import (
    minimum_cycle_start_window,
    should_run_backlog_recheck,
    should_start_scan_cycle,
)


class ScanLoopPolicyTests(IsolatedDataTestCase):
    def test_short_session_reserves_one_quarter_with_floor(self):
        self.assertEqual(minimum_cycle_start_window(0), 0.1)
        self.assertEqual(minimum_cycle_start_window(20), 5.0)

    def test_medium_session_reserves_one_quarter_with_thirty_second_floor(self):
        self.assertEqual(minimum_cycle_start_window(60), 30.0)
        self.assertEqual(minimum_cycle_start_window(300), 75.0)

    def test_long_session_reserves_between_one_hundred_twenty_and_one_hundred_eighty_seconds(self):
        self.assertEqual(minimum_cycle_start_window(301), 120.0)
        self.assertEqual(minimum_cycle_start_window(720), 180.0)
        self.assertEqual(minimum_cycle_start_window(3600), 180.0)

    def test_cycle_start_requires_the_full_finish_buffer(self):
        self.assertFalse(should_start_scan_cycle(29.9, 60))
        self.assertTrue(should_start_scan_cycle(30, 60))
        self.assertTrue(should_start_scan_cycle(180, 720))

    def test_backlog_recheck_runs_only_on_positive_cadence_boundaries(self):
        self.assertFalse(should_run_backlog_recheck(0, 4))
        self.assertFalse(should_run_backlog_recheck(3, 4))
        self.assertTrue(should_run_backlog_recheck(4, 4))
        self.assertTrue(should_run_backlog_recheck(8, 4))

    def test_backlog_cadence_is_never_zero(self):
        self.assertTrue(should_run_backlog_recheck(1, 0))
        self.assertTrue(should_run_backlog_recheck(1, -5))
