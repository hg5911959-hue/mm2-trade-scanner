import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner


class PendingRecheckScheduleTests(unittest.TestCase):
    def test_legacy_pending_ids_without_schedule_are_due_immediately(self):
        pending = {"old-ad": 100.0}
        self.assertEqual(scanner.due_pending_ids_for_scan(pending, {}, now=1000), pending)

    def test_future_scheduled_ids_are_not_due(self):
        pending = {"old-ad": 100.0, "due-ad": 200.0}
        schedule = {
            "old-ad": {"attempts": 3, "next_check_at": 1500.0},
            "due-ad": {"attempts": 1, "next_check_at": 900.0},
        }
        self.assertEqual(
            scanner.due_pending_ids_for_scan(pending, schedule, now=1000),
            {"due-ad": 200.0},
        )


    def test_pending_rechecks_are_capped_and_newest_never_checked_are_prioritized(self):
        pending = {f"ad-{i}": float(i) for i in range(5)}
        selected = scanner.due_pending_ids_for_scan(pending, {}, now=1000, limit=2)
        self.assertEqual(list(selected), ["ad-4", "ad-3"])

    def test_never_checked_pending_items_are_prioritized_over_retried_items(self):
        pending = {"retried": 1.0, "never-checked-a": 2.0, "never-checked-b": 3.0}
        schedule = {
            "retried": {"attempts": 1, "next_check_at": 900.0},
        }
        selected = scanner.due_pending_ids_for_scan(pending, schedule, now=1000, limit=2)
        self.assertEqual(set(selected), {"never-checked-a", "never-checked-b"})

    def test_backlog_recheck_runs_only_on_configured_cycle(self):
        self.assertEqual(
            [cycle for cycle in range(1, 9) if scanner.should_run_backlog_recheck(cycle, every=4)],
            [4, 8],
        )
        self.assertFalse(scanner.should_run_backlog_recheck(0, every=4))

    def test_value_snapshot_refresh_can_force_a_bounded_pending_recheck(self):
        pending = {"older": 100.0, "newer": 200.0}
        schedule = {
            "older": {"attempts": 3, "next_check_at": 5000.0},
            "newer": {"attempts": 2, "next_check_at": 6000.0},
        }
        selected = scanner.due_pending_ids_for_scan(
            pending, schedule, now=1000, limit=1, force=True
        )
        self.assertEqual(list(selected), ["newer"])

    def test_zero_limit_means_all_due_items(self):
        pending = {f"ad-{i}": float(i) for i in range(5)}
        selected = scanner.due_pending_ids_for_scan(pending, {}, now=1000, limit=0)
        self.assertEqual(len(selected), 5)

    def test_backoff_escalates_and_caps_at_six_hours(self):
        pending = {"ad": 100.0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending_recheck_schedule.json"
            with patch.object(scanner, "PENDING_RECHECK_SCHEDULE_FILE", path):
                schedule = scanner.record_pending_recheck_attempts({"ad": 100.0}, pending, {}, now=1000)
                self.assertEqual(schedule["ad"], {"attempts": 1, "next_check_at": 1300})
                schedule = scanner.record_pending_recheck_attempts({"ad": 100.0}, pending, schedule, now=1300)
                self.assertEqual(schedule["ad"], {"attempts": 2, "next_check_at": 2200})
                schedule = scanner.record_pending_recheck_attempts({"ad": 100.0}, pending, schedule, now=2200)
                self.assertEqual(schedule["ad"], {"attempts": 3, "next_check_at": 4000})
                for now in (4000, 7600, 11200):
                    schedule = scanner.record_pending_recheck_attempts({"ad": 100.0}, pending, schedule, now=now)
                self.assertEqual(schedule["ad"]["attempts"], 6)
                self.assertEqual(schedule["ad"]["next_check_at"], 32800)
                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["schedule"]["ad"]["attempts"], 6)

    def test_resolved_ids_are_removed_and_new_pending_ids_get_initial_delay(self):
        pending = {"still-pending": 1.0, "new-pending": 2.0}
        schedule = {
            "resolved": {"attempts": 4, "next_check_at": 1000.0},
            "still-pending": {"attempts": 2, "next_check_at": 1000.0},
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(scanner, "PENDING_RECHECK_SCHEDULE_FILE", Path(directory) / "schedule.json"):
                updated = scanner.record_pending_recheck_attempts(
                    {"resolved": 1.0}, pending, schedule, now=2000
                )
        self.assertNotIn("resolved", updated)
        self.assertEqual(updated["new-pending"], {"attempts": 0, "next_check_at": 2300})
        self.assertEqual(updated["still-pending"], {"attempts": 2, "next_check_at": 1000})

    def test_schedule_loader_rejects_corrupt_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text('{"version":1,"schedule":{"bad":{"attempts":-1,"next_check_at":100}}}', encoding="utf-8")
            with patch.object(scanner, "PENDING_RECHECK_SCHEDULE_FILE", path):
                with self.assertRaisesRegex(RuntimeError, "attempt count"):
                    scanner.load_pending_recheck_schedule()


if __name__ == "__main__":
    unittest.main()
