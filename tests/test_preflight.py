import os
import json
import tempfile
import unittest
from test_support import IsolatedDataTestCase
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tools.preflight import collect_preflight_checks, send_daily_notice, main


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


    def test_daily_notice_failure_is_not_recorded_and_can_retry(self):
        with patch("tools.preflight.TELEGRAM_OPENER.open", side_effect=OSError("network down")):
            sent = send_daily_notice(
                ["API key warning"], data_dir=self.root,
                env={"TELEGRAM_BOT_TOKEN": "placeholder", "TELEGRAM_CHAT_ID": "123"},
                now=self.now,
            )
        self.assertFalse(sent)
        self.assertFalse((self.root / "maintenance_alerts.json").exists())

    def test_daily_notice_is_recorded_only_after_successful_delivery(self):
        class Response:
            status = 200
            def read(self, limit):
                return b'{"ok":true}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("tools.preflight.TELEGRAM_OPENER.open", return_value=Response()):
            sent = send_daily_notice(
                ["API key warning"], data_dir=self.root,
                env={"TELEGRAM_BOT_TOKEN": "placeholder", "TELEGRAM_CHAT_ID": "123"},
                now=self.now,
            )
        self.assertTrue(sent)
        state = json.loads((self.root / "maintenance_alerts.json").read_text(encoding="utf-8"))
        self.assertEqual(state["preflight-daily"], self.now.timestamp())

    def test_preflight_telegram_redirect_handler_rejects_redirects(self):
        from tools.telegram_transport import RejectTelegramRedirects
        import urllib.request

        handler = RejectTelegramRedirects()
        request = urllib.request.Request(
            "https://api.telegram.org/botplaceholder/sendMessage", data=b"chat_id=123"
        )
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "https://attacker.example/collect"
        ))


    def test_dry_run_suppresses_preflight_telegram_notice(self):
        with (
            patch.dict(os.environ, {"PREFLIGHT_SEND_NOTICES": "false"}),
            patch("tools.preflight.collect_preflight_checks", return_value={"checks": ["test warning"]}),
            patch("tools.preflight.send_daily_notice") as notice,
        ):
            self.assertEqual(main(["--warn"]), 0)
        notice.assert_not_called()

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
