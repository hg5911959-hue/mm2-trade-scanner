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
                contextlib.redirect_stdout(io.StringIO()),
            ):
                weekly_report.main()
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
