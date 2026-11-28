from __future__ import annotations

import unittest
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class OpportunityMessageFormattingTests(IsolatedDataTestCase):
    def test_cash_message_does_not_add_value_mode_only_warnings(self):
        listing = {
            "id": "cash-1",
            "valuation_mode": "cash",
            "give_items": [{"name": "Bat", "quantity": 6}],
            "receive_items": [],
        }
        opportunity = {
            "listing": listing,
            "give_total": 5.0,
            "give_lines": ["Bat x1 | $5"],
            "receive_total": 7.0,
            "receive_lines": ["Knife x1 | $7"],
            "difference": 2.0,
            "margin": 99.0,
            "manual_review": False,
            "receive_demand_score": None,
            "receive_stability_score": None,
        }
        with (
            patch.object(scanner, "format_trade_message", return_value="BASE MESSAGE"),
            patch.object(scanner, "ignored_item_names") as ignored,
            patch.object(scanner, "compare_secondary_values") as secondary,
        ):
            message = scanner._build_opportunity_telegram_message(opportunity, {}, {})

        self.assertEqual(message, "BASE MESSAGE")
        self.assertNotIn("MARGIN", message)
        self.assertNotIn("quantity 5+", message)
        ignored.assert_not_called()
        secondary.assert_not_called()

    def test_value_message_keeps_manual_quality_and_verification_warnings(self):
        listing = {
            "id": "value-1",
            "valuation_mode": "value",
            "give_items": [{"name": "Stacked Knife", "quantity": 6}],
            "receive_items": [{"name": "Unvalued Item", "quantity": 1}],
        }
        opportunity = {
            "listing": listing,
            "give_total": 5.0,
            "give_lines": ["Stacked Knife x6"],
            "receive_total": 10.0,
            "receive_lines": ["Unvalued Item x1"],
            "difference": 5.0,
            "profit_worst": 5.0,
            "profit_best": 7.0,
            "margin": 100.0,
            "manual_review": True,
            "receive_demand_score": 8.25,
            "receive_stability_score": 7.5,
        }
        with (
            patch.object(scanner, "format_trade_message", return_value="BASE MESSAGE"),
            patch.object(scanner, "ignored_item_names", return_value=["Unvalued Item"]),
            patch.object(scanner, "compare_secondary_values", return_value="secondary differs"),
        ):
            message = scanner._build_opportunity_telegram_message(opportunity, {"market": "test"}, {})

        self.assertIn("MANUAL CHECK", message)
        self.assertIn("POSSIBLE PROFIT RANGE", message)
        self.assertIn("BASE MESSAGE", message)
        self.assertIn("MARGIN: 100.00%", message)
        self.assertIn("GET QUALITY: Demand 8.25/10 | Stability 7.50/9", message)
        self.assertIn("quantity 5+", message)
        self.assertIn("not valued (no numeric Supreme value): Unvalued Item", message)
        self.assertIn("unusually high profit", message)
        self.assertIn("SECOND-SOURCE CHECK (MM2Values): secondary differs", message)


if __name__ == "__main__":
    unittest.main()
