import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import watchdog


class WatchdogRepositoryTests(unittest.TestCase):
    def test_active_run_lookup_uses_github_repository_environment(self):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {"workflow_runs": [{"id": 123, "created_at": now}]}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/mm2-test"}), patch(
            "watchdog.scanner.request_with_retry",
            return_value=json.dumps(payload).encode("utf-8"),
        ) as request:
            age, run = watchdog.latest_active_scan_age_minutes("test-token")
        self.assertIsNotNone(age)
        self.assertEqual(run["id"], 123)
        self.assertIn("example/mm2-test", request.call_args.args[0].full_url)

    def test_missing_repository_environment_fails_clearly(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_REPOSITORY"):
                watchdog.latest_active_scan_age_minutes("test-token")


if __name__ == "__main__":
    unittest.main()


class WatchdogPollingWindowTests(unittest.TestCase):
    def test_stale_threshold_matches_faster_scan_schedule(self):
        self.assertFalse(watchdog.should_alert(19.9))
        self.assertTrue(watchdog.should_alert(20))
        self.assertTrue(watchdog.should_alert(24.9))
        self.assertFalse(watchdog.should_alert(25))


class WatchdogActiveRunGraceTests(unittest.TestCase):
    def test_suppresses_alert_while_scan_and_state_persistence_can_still_be_running(self):
        self.assertTrue(watchdog.should_suppress_stale_success(26.9, {"id": 1}))
        self.assertFalse(watchdog.should_suppress_stale_success(27, {"id": 1}))

    def test_does_not_suppress_when_no_active_scan_exists(self):
        self.assertFalse(watchdog.should_suppress_stale_success(None, None))
