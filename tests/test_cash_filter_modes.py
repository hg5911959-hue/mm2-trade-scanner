"""Regression coverage for the default-off cash filter."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scanner
from tools import weekly_report
from test_support import IsolatedDataTestCase



class CashConfigurationValidationTests(IsolatedDataTestCase):
    def test_negative_and_non_finite_margin_thresholds_fail_at_startup(self):
        for value in ("-0.01", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                environment = os.environ.copy()
                environment["CASH_MIN_MARGIN_PERCENT"] = value
                result = subprocess.run(
                    [sys.executable, "-c", "import scanner"],
                    env=environment, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CASH_MIN_MARGIN_PERCENT must be finite and non-negative", result.stderr)

    def test_zero_margin_threshold_remains_valid(self):
        environment = os.environ.copy()
        environment["CASH_MIN_MARGIN_PERCENT"] = "0"
        result = subprocess.run(
            [sys.executable, "-c", "import scanner"],
            env=environment, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class CashFilterFlagTests(IsolatedDataTestCase):
    def test_flag_parser_true_values(self):
        for value in ("1", "true", "TRUE", "yes", "On"):
            with self.subTest(value=value):
                self.assertTrue(scanner.cash_filter_enabled(value))

    def test_flag_parser_false_and_garbage_values(self):
        for value in ("", "0", "false", "no", "off", "garbage", None):
            with self.subTest(value=value):
                self.assertFalse(scanner.cash_filter_enabled(value))


class ValueModeEvaluationTests(IsolatedDataTestCase):
    def test_value_profit_qualifies_and_message_has_no_cash_wording(self):
        listing = {
            "id": "value-test", "valuation_mode": "value",
            "give_items": [{"name": "Knife A", "quantity": 1}],
            "receive_items": [{"name": "Knife B", "quantity": 1}],
        }
        result = {
            "listing": listing, "give_total": 10, "give_lines": ["Knife A: 10"],
            "receive_total": 20, "receive_lines": ["Knife B: 20"],
            "difference": 10, "profit_worst": 10, "profit_best": 10,
            "margin": 100, "manual_review": False,
        }
        self.assertTrue(scanner.qualifies(result))
        message = scanner.format_trade_message(
            listing, result["give_total"], result["give_lines"],
            result["receive_total"], result["receive_lines"], result["difference"],
        ) + "\nPROFIT (game value): +10"
        self.assertIn("PROFIT (game value)", message)
        self.assertNotRegex(message.casefold(), r"cash|usd|\$")

    def test_fair_trade_never_qualifies(self):
        result = {"difference": 0, "profit_worst": 0, "margin": 0, "manual_review": False}
        self.assertFalse(scanner.qualifies(result))


class WeeklyReportValueModeTests(IsolatedDataTestCase):
    def test_weekly_report_does_not_load_cash_catalogue_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            response = type("Response", (), {
                "status": 200,
                "read": lambda self, limit: b'{"ok":true}',
                "__enter__": lambda self: self,
                "__exit__": lambda self, *args: None,
            })()
            output = io.StringIO()
            with (
                patch.object(weekly_report, "DATA_DIR", Path(directory)),
                patch.dict(os.environ, {
                    "CASH_FILTER": "false", "CASH_PRICES_CSV": "must-not-be-read",
                    "TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "123",
                }),
                patch.object(weekly_report, "load_cash_price_catalog",
                             side_effect=AssertionError("cash catalogue must not load")) as load,
                patch.object(weekly_report, "format_blockers",
                             side_effect=AssertionError("cash blockers must not load")),
                patch.object(weekly_report, "collect_preflight_checks",
                             return_value={"checks": []}),
                patch.object(weekly_report.TELEGRAM_OPENER, "open", return_value=response),
                contextlib.redirect_stdout(output),
            ):
                weekly_report.main()
                load.assert_not_called()
            self.assertIn("Cash prices: disabled (value-based mode)", output.getvalue())





class CashTelegramAttributionTests(IsolatedDataTestCase):
    def _deliver_and_read_payload(self, message):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read(self):
                return b'{"ok": true, "result": {"message_id": 123}}'

        with (
            patch.object(scanner, "BOT_TOKEN", "test-token"),
            patch.object(scanner, "CHAT_ID", "123"),
            patch.object(scanner, "_wait_for_telegram_rate_limit"),
            patch.object(scanner.TELEGRAM_OPENER, "open", return_value=Response()) as send,
        ):
            scanner.send_telegram(message)
        request = send.call_args.args[0]
        return json.loads(request.data.decode("utf-8"))

    def test_cash_trade_alert_uses_robase_attribution_not_rblxvalue(self):
        listing = {
            "id": "cash-alert", "source": "bloxswaps",
            "valuation_mode": "cash",
            "owner_url": "https://bloxswaps.com/users/1",
            "url": "https://bloxswaps.com/trade/1",
        }
        message = scanner.format_trade_message(
            listing, 5.02,
            ["Harvester x1 — $5.02 each = $5.02"],
            221.22,
            ["Gingerscope x1 — $260.26 each = $260.26"],
            216.20,
        )
        payload = self._deliver_and_read_payload(message)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("CASH PROFIT", payload["text"])
        self.assertIn("Prices: robase.gg · Item values: rblxvalue.com", payload["text"])
        self.assertNotIn("Data from rblxvalue.com", payload["text"])

    def test_cash_digest_uses_robase_attribution(self):
        listing = {
            "id": "cash-digest", "source": "bloxswaps",
            "valuation_mode": "cash", "url": "https://bloxswaps.com/trade/2",
        }
        message = scanner.format_opportunity_digest([{
            "listing": listing, "give_total": 5.02, "receive_total": 221.22,
            "give_lines": ["Harvester x1 — $5.02 each = $5.02"],
            "receive_lines": ["Gingerscope x1 — $260.26 each = $260.26"],
            "difference": 216.20, "cash_profit_worst": 216.20,
            "cash_profit_best": 216.20, "margin": 4302.79,
        }])
        payload = self._deliver_and_read_payload(message)
        self.assertIn("<b>OPPORTUNITIES</b>", payload["text"])
        self.assertIn("Prices: robase.gg · Item values: rblxvalue.com", payload["text"])
        self.assertNotIn("Data from rblxvalue.com", payload["text"])


class DisabledScanIntegrationTests(IsolatedDataTestCase):
    def test_qualifying_value_listing_sends_exactly_one_non_cash_alert(self):
        listing = {
            "id": "value-mode-alert", "source_ad_id": "value-mode-source",
            "source": "bloxswaps", "owner_url": "https://bloxswaps.com/users/1",
            "url": "https://bloxswaps.com/trade/1",
            "give_items": [{"name": "Knife A", "quantity": 1}],
            "receive_items": [{"name": "Knife B", "quantity": 1}],
        }
        now = datetime.now(timezone.utc).isoformat()
        stats = {
            "cold_start": False, "pending_remaining_ids": [], "unparsed_ad_ids": [],
            "addition_ids": [], "watch_remaining_ids": [], "watch_search_complete": True,
            "pagination_complete": True, "pagination_pages": 1, "pagination_api_posts": 0,
            "seen_boundary_reached": False, "candidate_count": 1, "malformed_count": 0,
            "html_malformed_count": 0, "html_parse_warning_count": 0,
            "html_parse_warning_ids": [], "malformed_samples": [], "owner_link_count": 1,
        }
        result = {
            "listing": listing, "give_total": 10, "give_lines": ["Knife A: 10"],
            "receive_total": 20, "receive_lines": ["Knife B: 20"],
            "difference": 10, "profit_worst": 10, "profit_best": 10,
            "margin": 100, "manual_review": False,
        }
        sent = []
        fake_history = type("FakeHistory", (), {
            "record_values_snapshot": lambda self, values: False,
            "record_listings": lambda self, rows: 0,
            "record_opportunity": lambda self, row: None,
        })()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(scanner, "CASH_FILTER", False))
            stack.enter_context(patch.object(scanner, "BOT_TOKEN", "test-token"))
            stack.enter_context(patch.object(scanner, "CHAT_ID", "123"))
            stack.enter_context(patch.dict(os.environ, {"DRY_RUN": "false"}))
            stack.enter_context(patch.object(scanner, "update_metrics"))
            stack.enter_context(patch.object(scanner, "load_supreme_values", return_value={}))
            load_catalog = stack.enter_context(patch.object(
                scanner, "load_cash_price_catalog",
                side_effect=AssertionError("cash catalogue must not load")))
            stack.enter_context(patch.object(scanner, "cash_catalog_snapshot_hash",
                side_effect=AssertionError("cash snapshot must not be read")))
            stack.enter_context(patch.object(scanner, "save_cash_catalog_snapshot",
                side_effect=AssertionError("cash snapshot must not be written")))
            stack.enter_context(patch.object(scanner, "alert_cash_price_runway",
                side_effect=AssertionError("cash runway alert must not run")))
            stack.enter_context(patch.object(scanner, "load_aliases", return_value={}))
            stack.enter_context(patch.object(scanner, "load_seen_ads", return_value={"value-mode-source"}))
            stack.enter_context(patch.object(scanner, "load_pending_ids", return_value={}))
            stack.enter_context(patch.object(scanner, "load_pending_recheck_schedule", return_value={}))
            stack.enter_context(patch.object(scanner, "load_watch_state", return_value={}))
            stack.enter_context(patch.object(scanner, "load_first_seen_ads", return_value={"value-mode-source": now}))
            stack.enter_context(patch.object(scanner, "fetch_bloxswaps_all",
                return_value=([listing], stats, ["value-mode-source"])))
            stack.enter_context(patch.object(scanner, "record_pending_recheck_attempts", return_value={}))
            stack.enter_context(patch("discord_parser.load_discord_listings", return_value=([], 0)))
            stack.enter_context(patch.object(scanner, "load_alerted", return_value=[]))
            stack.enter_context(patch.object(scanner, "load_uncertain_state", return_value=({}, [])))
            stack.enter_context(patch.object(scanner, "evaluate_listing", return_value=(result, [])))
            stack.enter_context(patch.object(scanner, "send_telegram", side_effect=lambda message: sent.append(message)))
            maintenance_alert = stack.enter_context(patch.object(scanner, "send_maintenance_alert"))
            stack.enter_context(patch.object(scanner, "compare_secondary_values", return_value=""))
            stack.enter_context(patch.object(scanner, "record_success_if_complete"))
            stack.enter_context(patch.object(scanner, "append_history"))
            stack.enter_context(patch.object(scanner, "update_watch_entry"))
            stack.enter_context(patch.object(scanner, "save_alerted"))
            stack.enter_context(patch.object(scanner, "save_uncertain_ids"))
            stack.enter_context(patch.object(scanner, "save_seen_ads"))
            stack.enter_context(patch.object(scanner, "write_step_summary"))
            stack.enter_context(patch.object(scanner, "ALERT_DELAY_SECONDS", 0))
            stack.enter_context(patch("market_history.MarketHistory", return_value=fake_history))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                scanner.scan_once(include_backlog_rechecks=False)
            load_catalog.assert_not_called()
            maintenance_alert.assert_not_called()
        self.assertIn("[mode] mode=value", output.getvalue())
        self.assertIn("[summary] mode=value", output.getvalue())
        self.assertEqual(len(sent), 1)
        self.assertIn("PROFIT (game value)", sent[0])
        self.assertNotRegex(sent[0].casefold(), r"cash|usd|\\$")


if __name__ == "__main__":
    unittest.main()
