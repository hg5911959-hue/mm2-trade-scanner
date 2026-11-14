import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.validate_state import validate_state


class StateValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _write_valid_state(self):
        (self.root / "supreme_values.json").write_text(json.dumps({
            "schema_version": 4,
            "fetched_at": 1_800_000_000,
            "pages": {"0": {"items": [{"id": "1"}]}},
            "values": {"item": {"name": "Item", "value": 1}},
        }), encoding="utf-8")
        (self.root / "rblxvalue_request_budget.json").write_text(json.dumps({
            "limit": 60,
            "request_timestamps_epoch": [1_800_000_000],
        }), encoding="utf-8")
        (self.root / "rblxvalue_key_health.json").write_text(json.dumps({
            "key_fingerprint": "a" * 16,
            "last_attempt_at_epoch": 1_800_000_000,
            "last_success_at_epoch": 1_800_000_000,
        }), encoding="utf-8")
        with sqlite3.connect(self.root / "market_history.sqlite3") as db:
            db.executescript("""
                CREATE TABLE market_snapshots (id INTEGER PRIMARY KEY);
                CREATE TABLE market_values (id INTEGER PRIMARY KEY);
                CREATE TABLE listings (id INTEGER PRIMARY KEY);
                CREATE TABLE opportunities (id INTEGER PRIMARY KEY);
            """)

    def test_valid_state_passes_strict_success_validation(self):
        self._write_valid_state()
        self.assertEqual(validate_state(self.root, require_cache=True), [])

    def test_missing_cache_is_allowed_only_for_failed_or_cold_start_run(self):
        self.assertEqual(validate_state(self.root), [])
        self.assertIn("successful scan is missing supreme_values.json",
                      validate_state(self.root, require_cache=True))

    def test_corrupt_json_blocks_persistence(self):
        (self.root / "failure_state.json").write_text("{broken", encoding="utf-8")
        self.assertTrue(any("invalid JSON" in error for error in validate_state(self.root)))

    def test_boolean_cache_timestamp_is_rejected(self):
        self._write_valid_state()
        cache = json.loads((self.root / "supreme_values.json").read_text(encoding="utf-8"))
        cache["fetched_at"] = True
        (self.root / "supreme_values.json").write_text(json.dumps(cache), encoding="utf-8")
        self.assertTrue(any("fetched_at" in error for error in validate_state(self.root)))

    def test_optional_state_roots_must_be_objects(self):
        self._write_valid_state()
        for filename in (
            "metrics.json",
            "rblxvalue_request_budget.json",
            "rblxvalue_key_health.json",
        ):
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_text(encoding="utf-8") if path.exists() else None
                path.write_text("[]", encoding="utf-8")
                errors = validate_state(self.root, require_cache=True)
                self.assertTrue(errors, filename)
                if original is not None:
                    path.write_text(original, encoding="utf-8")
                else:
                    path.unlink()

    def test_boolean_metrics_counters_and_budget_limits_are_rejected(self):
        self._write_valid_state()
        (self.root / "metrics.json").write_text(json.dumps({
            "2026-11-14": {"scans": True}
        }), encoding="utf-8")
        (self.root / "rblxvalue_request_budget.json").write_text(json.dumps({
            "limit": True,
            "request_timestamps_epoch": [1_800_000_000],
        }), encoding="utf-8")
        errors = validate_state(self.root, require_cache=True)
        self.assertTrue(any("metrics contains an invalid counter" in error for error in errors))
        self.assertTrue(any("request budget limit is invalid" in error for error in errors))

    def test_boolean_pending_attempts_are_rejected(self):
        self._write_valid_state()
        (self.root / "pending_recheck_schedule.json").write_text(json.dumps({
            "version": 1,
            "schedule": {
                "ad-1": {
                    "attempts": True,
                    "next_check_at": 1_800_000_000,
                    "reason": "deferred_offer",
                }
            },
        }), encoding="utf-8")
        self.assertTrue(any(
            "pending recheck schedule counter" in error
            for error in validate_state(self.root, require_cache=True)
        ))

    def test_invalid_supreme_cache_blocks_persistence(self):
        (self.root / "supreme_values.json").write_text(json.dumps({
            "schema_version": 99, "fetched_at": "bad", "pages": {}, "values": {},
        }), encoding="utf-8")
        errors = validate_state(self.root)
        self.assertTrue(any("schema version" in error for error in errors))
        self.assertTrue(any("fetched_at" in error for error in errors))

    def test_corrupt_sqlite_database_blocks_persistence(self):
        (self.root / "market_history.sqlite3").write_text("not a database", encoding="utf-8")
        self.assertTrue(any("corrupt" in error for error in validate_state(self.root)))

    def test_private_discord_messages_are_rejected(self):
        (self.root / "discord_messages.json").write_text('{"messages":[]}', encoding="utf-8")
        self.assertTrue(any("private Discord" in error for error in validate_state(self.root)))

    def test_pending_retry_reason_must_be_a_known_value(self):
        self._write_valid_state()
        for reason in ("invented_reason", ["not", "a", "string"]):
            with self.subTest(reason=reason):
                (self.root / "pending_recheck_schedule.json").write_text(json.dumps({
                    "version": 1,
                    "schedule": {
                        "ad-1": {
                            "attempts": 0,
                            "next_check_at": 1_800_000_000,
                            "reason": reason,
                        }
                    },
                }), encoding="utf-8")
                errors = validate_state(self.root, require_cache=True)
                self.assertTrue(any("pending recheck schedule counter" in error for error in errors))

    def test_valid_pending_retry_reason_passes_state_validation(self):
        self._write_valid_state()
        (self.root / "pending_recheck_schedule.json").write_text(json.dumps({
            "version": 1,
            "schedule": {
                "ad-1": {
                    "attempts": 0,
                    "next_check_at": 1_800_000_000,
                    "reason": "deferred_offer",
                }
            },
        }), encoding="utf-8")
        self.assertEqual(validate_state(self.root, require_cache=True), [])

    def test_unfinished_atomic_write_blocks_persistence(self):
        (self.root / "seen_ads.json.tmp").write_text("{}", encoding="utf-8")
        self.assertTrue(any("temporary file" in error for error in validate_state(self.root)))


if __name__ == "__main__":
    unittest.main()
