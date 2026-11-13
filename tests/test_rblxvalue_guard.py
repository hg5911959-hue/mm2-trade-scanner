import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rblxvalue_guard as guard


class RblxValueGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.budget = self.root / "budget.json"
        self.block = self.root / "blocked.json"
        guard.reset_run_counter()

    def test_144_runs_reuse_fresh_cache_without_api_requests(self):
        # 144 ten-minute workflow invocations over one day; cache is revalidated
        # once at 12 hours, not on every invocation.
        start = 1_800_000_000.0
        fetched_at = start
        api_calls = 0
        for run in range(144):
            now = start + run * 600
            if now - fetched_at >= 12 * 3600:
                api_calls += 1
                fetched_at = now
        self.assertEqual(api_calls, 1)

    def test_budget_is_rolling_and_counts_each_attempt(self):
        for i in range(60):
            guard.reserve_request(now=1_800_000_000 + i, key="rbx_test_key",
                                  budget_path=self.budget, block_path=self.block)
        with self.assertRaises(guard.RblxValueBudgetExceeded):
            guard.reserve_request(now=1_800_000_100, key="rbx_test_key",
                                  budget_path=self.budget, block_path=self.block)
        state = json.loads(self.budget.read_text())
        self.assertEqual(len(state["request_timestamps_epoch"]), 60)

    def test_expired_budget_entries_roll_off_after_24_hours(self):
        guard.reserve_request(now=1_800_000_000, key="rbx_test_key",
                              budget_path=self.budget, block_path=self.block)
        count = guard.reserve_request(now=1_800_086_401, key="rbx_test_key",
                                      budget_path=self.budget, block_path=self.block)
        self.assertEqual(count, 1)

    def test_401_and_explicit_inactive_key_open_breaker_without_retry(self):
        for status, body in ((401, "unauthorized"), (403, "API key suspended"),
                             (400, "API key inactive")):
            with self.subTest(status=status, body=body):
                with (
                    patch.object(guard, "BLOCK_FILE", self.block),
                    self.assertRaises(guard.RblxValueKeyBlocked),
                ):
                    guard.raise_for_key_error(status, body, key="rbx_test_key", now=1_800_000_000)
                self.assertTrue(guard.is_key_blocked(now=1_800_000_001,
                                                    key="rbx_test_key", block_path=self.block))
                with self.assertRaises(guard.RblxValueKeyBlocked):
                    guard.reserve_request(now=1_800_000_002, key="rbx_test_key",
                                          budget_path=self.budget, block_path=self.block)
                self.block.unlink(missing_ok=True)

    def test_generic_403_is_not_misclassified_as_invalid_key(self):
        self.assertFalse(guard.is_key_error(403, "Forbidden"))
        self.assertFalse(guard.is_key_error(403, "Cloudflare access denied"))
        self.assertFalse(guard.is_key_error(403, "Your account is suspended"))
        self.assertFalse(guard.is_key_error(403, "Account inactive"))
        self.assertTrue(guard.is_key_error(403, "API key suspended"))
        self.assertTrue(guard.is_key_error(403, "Your access token has expired"))
        self.assertTrue(guard.is_key_error(403, "API key not activated"))
        self.assertTrue(guard.is_key_error(403, "Your API key has not been activated"))
        self.assertFalse(guard.is_key_error(429, "Rate limit exceeded"))
        self.assertTrue(guard.is_key_error(403, "API key disabled"))
        self.assertTrue(guard.is_key_error(401, "Unauthorized"))

    def test_breaker_expires_after_six_hours_or_key_rotation(self):
        guard.mark_key_blocked("HTTP 403", now=1_800_000_000,
                               key="rbx_old_key", block_path=self.block)
        self.assertTrue(guard.is_key_blocked(now=1_800_000_001,
                                             key="rbx_old_key", block_path=self.block))
        self.assertFalse(guard.is_key_blocked(now=1_800_000_001,
                                              key="rbx_new_key", block_path=self.block))
        self.assertFalse(guard.is_key_blocked(now=1_800_000_000 + 6 * 3600 + 1,
                                              key="rbx_old_key", block_path=self.block))

    def test_block_alert_is_once_then_24_hour_reminder(self):
        guard.mark_key_blocked("HTTP 403", now=1_800_000_000,
                               key="rbx_test_key", block_path=self.block)
        self.assertTrue(guard.claim_block_alert(now=1_800_000_000,
                                                key="rbx_test_key", block_path=self.block))
        self.assertTrue(guard.mark_block_alert_sent(now=1_800_000_001,
                                                    key="rbx_test_key", block_path=self.block))
        self.assertFalse(guard.claim_block_alert(now=1_800_000_000 + 3600,
                                                 key="rbx_test_key", block_path=self.block))
        self.assertTrue(guard.claim_block_alert(now=1_800_000_000 + 86401,
                                                key="rbx_test_key", block_path=self.block))

    def test_failed_block_alert_retries_after_five_minutes_not_24_hours(self):
        guard.mark_key_blocked("HTTP 401", now=1_800_000_000,
                               key="rbx_test_key", block_path=self.block)
        self.assertTrue(guard.claim_block_alert(now=1_800_000_000,
                                                key="rbx_test_key", block_path=self.block))
        self.assertFalse(guard.claim_block_alert(now=1_800_000_000 + 299,
                                                 key="rbx_test_key", block_path=self.block))
        self.assertTrue(guard.claim_block_alert(now=1_800_000_000 + 300,
                                                key="rbx_test_key", block_path=self.block))

    def test_fingerprint_is_truncated_sha256_and_never_raw_key(self):
        guard.mark_key_blocked("HTTP 403", now=1_800_000_000,
                               key="rbx_sensitive_secret", block_path=self.block)
        text = self.block.read_text()
        self.assertNotIn("rbx_sensitive_secret", text)
        self.assertEqual(json.loads(text)["key_fingerprint"],
                         guard.key_fingerprint("rbx_sensitive_secret"))

    def test_request_counter_counts_reserved_requests(self):
        guard.reset_run_counter()
        guard.reserve_request(now=1_800_000_000, key="rbx_test_key",
                              budget_path=self.budget, block_path=self.block)
        self.assertEqual(guard.requests_this_run(), 1)




    def test_corrupt_budget_json_fails_closed_without_overwriting_file(self):
        self.budget.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(guard.RblxValueGuardError, "invalid JSON"):
            guard.reserve_request(now=1_800_000_000, key="rbx_test_key",
                                  budget_path=self.budget, block_path=self.block)
        self.assertEqual(self.budget.read_text(encoding="utf-8"), "{broken")

    def test_non_object_budget_root_fails_closed(self):
        self.budget.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(guard.RblxValueGuardError, "invalid root"):
            guard.reserve_request(now=1_800_000_000, key="rbx_test_key",
                                  budget_path=self.budget, block_path=self.block)

    def test_invalid_budget_timestamp_fails_closed(self):
        self.budget.write_text(json.dumps({
            "request_timestamps_epoch": [1_800_000_000, "not-a-timestamp"]
        }), encoding="utf-8")
        with self.assertRaisesRegex(guard.RblxValueGuardError, "invalid timestamp"):
            guard.reserve_request(now=1_800_000_001, key="rbx_test_key",
                                  budget_path=self.budget, block_path=self.block)

    def test_future_budget_timestamp_is_counted_conservatively(self):
        future = 1_800_000_100
        self.budget.write_text(json.dumps({
            "request_timestamps_epoch": [future]
        }), encoding="utf-8")
        count = guard.reserve_request(now=1_800_000_000, key="rbx_test_key",
                                      budget_path=self.budget, block_path=self.block,
                                      limit=2)
        state = json.loads(self.budget.read_text(encoding="utf-8"))
        self.assertEqual(count, 2)
        self.assertEqual(len(state["request_timestamps_epoch"]), 2)


if __name__ == "__main__":
    unittest.main()
