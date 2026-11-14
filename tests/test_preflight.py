import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.preflight import collect_preflight_checks


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 14, tzinfo=UTC)
        rows = [
            "name,cash_price_usd,tier,status,verified_at_utc,source,notes",
        ]
        for index in range(8):
            rows.append(
                "Item" + str(index) + ",1,test,verified,"
                + (self.now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
                + ",manual,"
            )
        (self.root / "cash_prices.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        (self.root / "supreme_values.json").write_text(json.dumps({
            "fetched_at": self.now.timestamp(),
        }), encoding="utf-8")
        (self.root / "api_key_blocked.json").write_text(json.dumps({
            "permanent": False, "blocked_until_epoch": self.now.timestamp() - 1,
        }), encoding="utf-8")
        self.env = {"TELEGRAM_BOT_TOKEN": "not-a-real-token", "TELEGRAM_CHAT_ID": "123",
                    "CASH_MIN_VERIFIED_ROWS": "8", "CASH_MAX_AGE_DAYS": "14"}

    def _checks(self, **kwargs):
        return collect_preflight_checks(
            self.root, env=kwargs.pop("env", self.env), now=self.now,
            state_checker=kwargs.pop("state_checker", lambda: True), **kwargs,
        )["checks"]

    def test_healthy_fixture_passes(self):
        self.assertEqual(self._checks(), [])

    def test_each_required_preflight_failure_is_reported(self):
        self.assertTrue(any("fresh verified cash prices" in x for x in self._checks(env={**self.env, "CASH_MIN_VERIFIED_ROWS": "9"})))
        (self.root / "api_key_blocked.json").write_text(json.dumps({"permanent": True}), encoding="utf-8")
        self.assertTrue(any("API key is blocked" in x for x in self._checks()))
        (self.root / "api_key_blocked.json").write_text(json.dumps({"permanent": False}), encoding="utf-8")
        (self.root / "supreme_values.json").write_text(json.dumps({"fetched_at": (self.now - timedelta(hours=25)).timestamp()}), encoding="utf-8")
        self.assertTrue(any("older than 24 hours" in x for x in self._checks()))
        (self.root / "supreme_values.json").write_text(json.dumps({"fetched_at": self.now.timestamp()}), encoding="utf-8")
        self.assertTrue(any("Telegram credentials" in x for x in self._checks(env={})))
        self.assertTrue(any("state branch is unreadable" in x for x in self._checks(state_checker=lambda: False)))


if __name__ == "__main__":
    unittest.main()
