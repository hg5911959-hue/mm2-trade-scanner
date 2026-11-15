"""Regression coverage for the default-off cash filter."""
import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scanner
from tools import weekly_report
from test_support import IsolatedDataTestCase


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
            response = type("Response", (), {"status": 200, "__enter__": lambda self: self,
                                              "__exit__": lambda self, *args: None})()
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
                patch.object(weekly_report.urllib.request, "urlopen", return_value=response),
                contextlib.redirect_stdout(output),
            ):
                weekly_report.main()
                load.assert_not_called()
            self.assertIn("Cash prices: disabled (value-based mode)", output.getvalue())



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
