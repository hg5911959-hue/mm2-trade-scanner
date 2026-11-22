"""Regression tests for strict ROBASE cash-alert safety gates."""

import unittest
from datetime import UTC, datetime

from test_support import IsolatedDataTestCase

import scanner


class ScannerCashSafetyTests(IsolatedDataTestCase):
    def test_value_alert_gate_rejects_non_dictionary_results(self):
        for result in (None, [], "profit", 10):
            with self.subTest(result=result):
                self.assertFalse(scanner.qualifies(result))

    def test_value_alert_gate_rejects_non_finite_metrics(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for field in ("profit_worst", "margin"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value):
                    self.assertFalse(scanner.qualifies({**base, field: value}))

    def test_value_alert_gate_rejects_malformed_metrics(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for field, value in (("profit_worst", None), ("profit_worst", "bad"),
                             ("margin", None), ("margin", "bad")):
            with self.subTest(field=field, value=value):
                self.assertFalse(scanner.qualifies({**base, field: value}))

    def test_value_alert_gate_rejects_invalid_thresholds(self):
        base = {"manual_review": False, "cash_estimate_only": False,
                "profit_worst": 100.0, "margin": 20.0}
        for kwargs in ({"min_diff": float("nan")}, {"min_diff": float("inf")},
                       {"min_percent": float("nan")}, {"min_percent": float("inf")},
                       {"min_diff": -1.0}, {"min_percent": -1.0}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(scanner.qualifies(base, **kwargs))

    def test_value_alert_gate_accepts_finite_verified_positive_trade(self):
        result = {"manual_review": False, "cash_estimate_only": False,
                  "profit_worst": 10.0, "margin": 5.0}
        self.assertTrue(scanner.qualifies(result, min_diff=1.0, min_percent=5.0))

    def test_legacy_estimate_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": True, "manual_review": False,
            "cash_profit_worst": 10000.0, "margin": 500.0, "give_total_max": 10.0,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_manual_review_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False, "manual_review": True,
            "cash_profit_worst": 10000.0, "margin": 500.0, "give_total_max": 10.0,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_non_finite_cash_profit_is_never_alert_eligible(self):
        for profit in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(profit=profit):
                result = {
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
                    "cash_profit_worst": profit, "margin": 1000.0, "give_total_max": 10.0,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_non_finite_cash_margin_is_never_alert_eligible(self):
        for margin in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(margin=margin):
                result = {
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
                    "cash_profit_worst": 1000.0, "margin": margin, "give_total_max": 10.0,
                }
                self.assertFalse(scanner.qualifies_cash(result))

    def test_negative_cash_profit_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False, "manual_review": False,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
            "cash_profit_worst": -1000.0, "margin": 1000.0, "give_total_max": 10.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_verified_robase_cash_profit_can_pass_alert_gate(self):
        result = {
            "cash_estimate_only": False, "manual_review": False,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
            "cash_profit_worst": 1.0, "margin": 0.01, "give_total_max": 10000.0,
        }
        self.assertTrue(scanner.qualifies_cash(result))

    def test_robase_items_resolve_through_exact_rblxvalue_slugs(self):
        names = (
            "Spirit", "Harvester", "Bat", "Immortal", "Icebreaker", "Bloom",
            "Snowstorm", "Candy", "Watergun", "Evergun", "Celestial",
        )
        now = datetime.now(UTC).isoformat()
        values = {}
        cash_items_by_slug = {}
        cash_items_by_name = {}
        rblx_exact_map = {}
        give_items = []
        receive_items = []
        for index, name in enumerate(names):
            slug = name.casefold().replace(" ", "-")
            values[name.casefold()] = {
                "name": name, "slug": slug, "value_source_b": index + 1,
                "stability": "Stable", "category": "Godly", "type": "Knife",
            }
            cash_row = {
                "name": name, "cash_price_usd": 5.0, "price_low_usd": 5.0,
                "price_high_usd": 5.0, "price_display": "$5.00",
                "status": "verified", "verified_at_utc": now,
                "source": "https://robase.gg/item/" + slug, "robase_slug": slug,
                "robase_category": "Godly",
            }
            cash_items_by_slug[slug] = cash_row
            cash_items_by_name[scanner.normalize_cash_name(name)] = cash_row
            rblx_exact_map[slug] = {
                "bloxswaps_slug": slug, "robase_slug": slug,
                "robase_name": name, "robase_identity_source": "test",
                "rblx_slug": slug, "category": "godly", "type": "knife",
            }
            item = {
                "name": name, "catalog_slug": slug, "catalog_rarity": "Godly",
                "catalog_type": "Knife", "catalog_chroma": False,
                "quantity": 1, "quantity_min": 1, "quantity_max": 1,
                "quantity_source": "structured",
            }
            give_items.append(dict(item))
            receive_items.append(dict(item))
        listing = {
            "id": "robase-catalog-integration", "source": "bloxswaps",
            "give_items": give_items, "receive_items": receive_items,
        }
        result, missing = scanner.evaluate_cash_listing(
            listing, values, {}, {
                "items": cash_items_by_name,
                "items_by_slug": cash_items_by_slug,
                "rblx_exact_map": rblx_exact_map,
            },
        )
        self.assertEqual(missing, [])
        self.assertIsNotNone(result)
        self.assertEqual(
            result["cash_price_basis"],
            "verified ROBASE displayed seller-proceeds price",
        )
        self.assertEqual(result["cash_sell_haircut_pct"], 0.0)

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
                    "cash_estimate_only": False, "manual_review": False,
                    "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
                    "cash_profit_worst": 100.0, "margin": 20.0, "give_total_max": 10.0,
                }
                result[field] = value
                self.assertFalse(scanner.qualifies_cash(result))

    def test_profit_threshold_is_exactly_one_dollar_for_every_trade_size(self):
        base = {
            "manual_review": False, "cash_estimate_only": False,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
            "margin": 0.01, "give_total_max": 4.99,
        }
        self.assertTrue(scanner.qualifies_cash({**base, "cash_profit_worst": 1.00}))
        self.assertFalse(scanner.qualifies_cash({**base, "cash_profit_worst": 0.99}))
        standard = {**base, "give_total_max": 500.0}
        self.assertTrue(scanner.qualifies_cash({**standard, "cash_profit_worst": 1.00}))
        self.assertFalse(scanner.qualifies_cash({**standard, "cash_profit_worst": 0.999}))

    def test_non_dictionary_cash_result_fails_closed(self):
        for result in (None, [], "profit", 10):
            with self.subTest(result=result):
                self.assertFalse(scanner.qualifies_cash(result))

    def test_missing_or_untrusted_cash_provenance_fails_closed(self):
        base = {
            "manual_review": False, "cash_estimate_only": False,
            "cash_price_basis": "verified ROBASE displayed seller-proceeds price",
            "cash_profit_worst": 100.0, "margin": 20.0, "give_total_max": 10.0,
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
