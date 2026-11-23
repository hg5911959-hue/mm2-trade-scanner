import os
import json
import tempfile
import unittest
from test_support import IsolatedDataTestCase
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tools.preflight import collect_preflight_checks, send_daily_notice, send_budget_warning, main, check_telegram_connection


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


    def test_telegram_connection_check_verifies_bot_and_chat_without_sending(self):
        class Response:
            status = 200
            def __init__(self, payload):
                self.payload = payload
            def read(self, limit):
                return json.dumps(self.payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch(
            "tools.preflight.TELEGRAM_OPENER.open",
            side_effect=[Response({"ok": True, "result": {"id": 1}}),
                         Response({"ok": True, "result": {"id": 123}})],
        ) as opener:
            self.assertEqual(check_telegram_connection(env=self.env), [])
        self.assertEqual(opener.call_count, 2)
        self.assertIn("/getMe", opener.call_args_list[0].args[0].full_url)
        self.assertIn("/getChat?chat_id=123", opener.call_args_list[1].args[0].full_url)

    def test_telegram_connection_check_reports_invalid_token_or_chat(self):
        class Response:
            status = 200
            def read(self, limit):
                return b'{"ok":false,"description":"Unauthorized"}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("tools.preflight.TELEGRAM_OPENER.open", return_value=Response()) as opener:
            checks = check_telegram_connection(env=self.env)
        self.assertEqual(checks, ["Telegram Bot API rejected the bot token"])
        self.assertEqual(opener.call_count, 1)

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

    def test_rblxvalue_budget_warning_triggers_at_80_percent_of_rolling_limit(self):
        budget = {
            "schema_version": 1,
            "window": "rolling_24_hours",
            "limit": 60,
            "request_timestamps_epoch": [self.now.timestamp() - 3600] * 48,
        }
        (self.root / "rblxvalue_request_budget.json").write_text(
            json.dumps(budget), encoding="utf-8"
        )
        result = collect_preflight_checks(
            self.root, env=self.env, now=self.now, state_checker=lambda: True
        )
        self.assertEqual(
            result["rblxvalue_budget_warning"],
            {"used": 48, "limit": 60, "threshold": 48},
        )

    def test_rblxvalue_budget_warning_does_not_trigger_below_80_percent(self):
        budget = {
            "limit": 60,
            "request_timestamps_epoch": [self.now.timestamp() - 3600] * 47,
        }
        (self.root / "rblxvalue_request_budget.json").write_text(
            json.dumps(budget), encoding="utf-8"
        )
        result = collect_preflight_checks(
            self.root, env=self.env, now=self.now, state_checker=lambda: True
        )
        self.assertIsNone(result["rblxvalue_budget_warning"])

    def test_rblxvalue_budget_notice_is_sent_once_per_cooldown(self):
        class Response:
            status = 200
            def read(self, limit):
                return b'{"ok":true}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        warning = {"used": 48, "limit": 60, "threshold": 48}
        env = {"TELEGRAM_BOT_TOKEN": "placeholder", "TELEGRAM_CHAT_ID": "123"}
        with patch("tools.preflight.TELEGRAM_OPENER.open", return_value=Response()) as opener:
            self.assertTrue(send_budget_warning(warning, data_dir=self.root, env=env, now=self.now))
            self.assertFalse(send_budget_warning(warning, data_dir=self.root, env=env, now=self.now))
        self.assertEqual(opener.call_count, 1)
        state = json.loads((self.root / "maintenance_alerts.json").read_text(encoding="utf-8"))
        self.assertEqual(state["rblxvalue-budget-80pct"], self.now.timestamp())

    def test_main_dispatches_budget_warning_and_dry_run_suppresses_it(self):
        warning = {"used": 48, "limit": 60, "threshold": 48}
        result = {"checks": [], "rblxvalue_budget_warning": warning}
        with (
            patch("tools.preflight.collect_preflight_checks", return_value=result),
            patch.dict(os.environ, {"PREFLIGHT_SEND_NOTICES": "true"}),
            patch("tools.preflight.send_budget_warning") as send_warning,
        ):
            self.assertEqual(main(["--warn"]), 0)
        send_warning.assert_called_once_with(warning)

        with (
            patch("tools.preflight.collect_preflight_checks", return_value=result),
            patch.dict(os.environ, {"PREFLIGHT_SEND_NOTICES": "false"}),
            patch("tools.preflight.send_budget_warning") as send_warning,
        ):
            self.assertEqual(main(["--warn"]), 0)
        send_warning.assert_not_called()


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
