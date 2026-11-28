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

    def test_long_session_uses_a_sixty_to_ninety_second_baseline_reserve(self):
        self.assertEqual(minimum_cycle_start_window(301), 60.0)
        self.assertEqual(minimum_cycle_start_window(540), 67.5)
        self.assertEqual(minimum_cycle_start_window(720), 90.0)
        self.assertEqual(minimum_cycle_start_window(3600), 90.0)

    def test_long_session_reserve_expands_after_a_slow_cycle(self):
        self.assertEqual(minimum_cycle_start_window(540, 25), 67.5)
        self.assertEqual(minimum_cycle_start_window(540, 40), 90.0)
        self.assertEqual(minimum_cycle_start_window(540, 80), 170.0)

    def test_cycle_start_requires_the_full_adaptive_finish_buffer(self):
        self.assertFalse(should_start_scan_cycle(29.9, 60))
        self.assertTrue(should_start_scan_cycle(30, 60))
        self.assertFalse(should_start_scan_cycle(169, 540, 80))
        self.assertTrue(should_start_scan_cycle(170, 540, 80))

    def test_backlog_recheck_runs_only_on_positive_cadence_boundaries(self):
        self.assertFalse(should_run_backlog_recheck(0, 4))
        self.assertFalse(should_run_backlog_recheck(3, 4))
        self.assertTrue(should_run_backlog_recheck(4, 4))
        self.assertTrue(should_run_backlog_recheck(8, 4))

    def test_backlog_cadence_is_never_zero(self):
        self.assertTrue(should_run_backlog_recheck(1, 0))
        self.assertTrue(should_run_backlog_recheck(1, -5))
