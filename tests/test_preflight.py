import json
import tempfile
import unittest
from test_support import IsolatedDataTestCase
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.preflight import collect_preflight_checks


class PreflightTests(IsolatedDataTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 14, tzinfo=UTC)
        checked = self.now.isoformat().replace("+00:00", "Z")
        snapshot = {
            "schema_version": 4,
            "source": "https://robase.gg/",
            "fetched_at_utc": checked,
            "price_basis": "seller_proceeds_after_marketplace_fees",
            "catalog_expected_count": 222,
            "catalog_complete": True,
            "items": [
                {
                    "name": f"Item {index}", "robase_slug": f"item-{index}", "robase_category": "Godly",
                    "cash_price_usd": 1.0, "price_display": "$1.00",
                    "sold_7d": 10, "sold_30d": 20, "status": "verified",
                    "source": f"https://robase.gg/item/item-{index}",
                    "last_checked_at_utc": checked, "price_updated_at_utc": checked,
                }
                for index in range(222)
            ],
        }
        (self.root / "robase_prices.json").write_text(json.dumps(snapshot), encoding="utf-8")
        (self.root / "supreme_values.json").write_text(json.dumps({
            "fetched_at": self.now.timestamp(),
        }), encoding="utf-8")
        (self.root / "api_key_blocked.json").write_text(json.dumps({
            "permanent": False, "blocked_until_epoch": self.now.timestamp() - 1,
        }), encoding="utf-8")
        self.env = {"TELEGRAM_BOT_TOKEN": "not-a-real-token", "TELEGRAM_CHAT_ID": "123",
                    "CASH_MIN_VERIFIED_ROWS": "8", "CASH_MAX_AGE_DAYS": "1", "CASH_FILTER": "true"}

    def _checks(self, **kwargs):
        return collect_preflight_checks(
            self.root, env=kwargs.pop("env", self.env), now=self.now,
            state_checker=kwargs.pop("state_checker", lambda: True), **kwargs,
        )["checks"]

    def test_healthy_fixture_passes(self):
        self.assertEqual(self._checks(), [])

    def test_each_required_preflight_failure_is_reported(self):
        self.assertTrue(any("fresh verified ROBASE cash prices" in x for x in self._checks(env={**self.env, "CASH_MIN_VERIFIED_ROWS": "223"})))
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
