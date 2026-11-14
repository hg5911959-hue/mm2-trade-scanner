"""Regression tests for scanner cash-alert safety gates."""

import unittest
from datetime import UTC, datetime

import scanner
from tools.cash_market import load_cash_price_catalog


class ScannerCashSafetyTests(unittest.TestCase):
    def test_legacy_estimate_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": True,
            "manual_review": False,
            "cash_profit_worst": 10_000.0,
            "profit_worst": 10_000.0,
            "margin": 500.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_manual_review_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False,
            "manual_review": True,
            "cash_profit_worst": 10_000.0,
            "margin": 500.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))


    def test_non_finite_cash_profit_is_never_alert_eligible(self):
        for profit in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(profit=profit):
                result = {
                    "cash_estimate_only": False,
                    "manual_review": False,
                    "cash_profit_worst": profit,
                    "margin": 1000.0,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_non_finite_cash_margin_is_never_alert_eligible(self):
        for margin in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(margin=margin):
                result = {
                    "cash_estimate_only": False,
                    "manual_review": False,
                    "cash_profit_worst": 1000.0,
                    "margin": margin,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_negative_cash_profit_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False,
            "manual_review": False,
            "cash_profit_worst": -1000.0,
            "margin": 1000.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_verified_cash_profit_can_pass_alert_gate(self):
        result = {
            "cash_estimate_only": False,
            "manual_review": False,
            "cash_price_basis": "verified manual USD resale catalogue",
            "cash_profit_worst": 100.0,
            "margin": 20.0,
            "give_total_max": 10.0,
        }
        self.assertTrue(scanner.qualifies_cash(result))

    def test_user_confirmed_cash_catalogue_items_resolve_through_scanner(self):
        catalog = load_cash_price_catalog("data/cash_prices.csv")
        # Keep this integration test deterministic when the production prices expire.
        now = datetime.now(UTC).isoformat()
        for row in catalog["items"].values():
            if row["status"] == "verified":
                row["verified_at_utc"] = now
        names = (
            "Spirit", "Harvester", "Bat", "Immortal", "Icebreaker", "Bloom",
            "Snowstorm", "Candy", "Watergun", "Evergun", "Celestial",
        )
        values = {
            name.casefold(): {
                "name": name,
                "variants": [{
                    "id": f"test-{index}",
                    "name": name,
                    "value": 1,
                    "stability": "Stable",
                }],
            }
            for index, name in enumerate(names)
        }
        listing = {
            "id": "cash-catalog-integration",
            "give_items": [{"name": name, "quantity": 1} for name in names],
            "receive_items": [{"name": name, "quantity": 1} for name in names],
        }
        result, missing = scanner.evaluate_cash_listing(
            listing, values, {}, catalog,
        )
        self.assertEqual(missing, [])
        self.assertIsNotNone(result)
        self.assertIn("verified manual USD resale catalogue", result["cash_price_basis"])

    def test_malformed_cash_profit_or_margin_fails_closed(self):
        for field, value in (
            ("cash_profit_worst", None),
            ("cash_profit_worst", "not-a-number"),
            ("cash_profit_worst", True),
            ("margin", None),
            ("margin", "not-a-number"),
            ("margin", False),
        ):
            with self.subTest(field=field, value=value):
                result = {
                    "cash_estimate_only": False,
                    "manual_review": False,
                    "cash_price_basis": "verified manual USD resale catalogue",
                    "cash_profit_worst": 100.0,
                    "margin": 20.0,
                    "give_total_max": 10.0,
                }
                result[field] = value
                self.assertFalse(scanner.qualifies_cash(result))

    def test_small_and_standard_profit_threshold_boundaries(self):
        base = {
            "manual_review": False,
            "cash_estimate_only": False,
            "cash_price_basis": "verified manual USD resale catalogue",
            "margin": 20.0,
            "give_total_max": 4.99,
        }
        self.assertTrue(scanner.qualifies_cash({**base, "cash_profit_worst": 0.25}))
        self.assertFalse(scanner.qualifies_cash({**base, "cash_profit_worst": 0.249}))
        standard = {**base, "give_total_max": 5.0}
        self.assertFalse(scanner.qualifies_cash({**standard, "cash_profit_worst": 0.499}))
        self.assertTrue(scanner.qualifies_cash({**standard, "cash_profit_worst": 0.50}))

    def test_non_dictionary_cash_result_fails_closed(self):
        for result in (None, [], "profit", 10):
            with self.subTest(result=result):
                self.assertFalse(scanner.qualifies_cash(result))

    def test_missing_or_untrusted_cash_provenance_fails_closed(self):
        base = {
            "manual_review": False,
            "cash_estimate_only": False,
            "cash_price_basis": "verified manual USD resale catalogue",
            "cash_profit_worst": 100.0,
            "margin": 20.0,
        }
        self.assertTrue(scanner.qualifies_cash(base))
        for field in (
            "manual_review", "cash_estimate_only", "cash_price_basis",
            "cash_profit_worst", "margin",
        ):
            result = dict(base)
            result.pop(field)
            with self.subTest(field=field):
                self.assertFalse(scanner.qualifies_cash(result))
        wrong_basis = dict(base, cash_price_basis="unverified legacy estimate only")
        self.assertFalse(scanner.qualifies_cash(wrong_basis))
        generic_only = dict(base, profit_worst=100.0)
        generic_only.pop("cash_profit_worst")
        self.assertFalse(scanner.qualifies_cash(generic_only))


if __name__ == "__main__":
    unittest.main()
