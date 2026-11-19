"""Regression tests for scanner cash-alert safety gates."""

import csv
import unittest
from test_support import IsolatedDataTestCase
from datetime import UTC, datetime

import scanner
from tools.cash_market import load_cash_price_catalog


class ScannerCashSafetyTests(IsolatedDataTestCase):
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
        names = (
            "Spirit", "Harvester", "Bat", "Immortal", "Icebreaker", "Bloom",
            "Snowstorm", "Candy", "Watergun", "Evergun", "Celestial",
        )
        now = datetime.now(UTC).isoformat()
        path = self.test_data_root / "cash_prices.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "name", "cash_price_usd", "tier", "status",
                "verified_at_utc", "source", "notes",
            ])
            writer.writeheader()
            for name in names:
                writer.writerow({
                    "name": name, "cash_price_usd": "5", "tier": "test",
                    "status": "verified", "verified_at_utc": now,
                    "source": "synthetic_test_fixture", "notes": "",
                })
        catalog = load_cash_price_catalog(path)
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
            "give_total_max": 10.0,
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


    def test_discord_only_mode_refuses_non_dry_run(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"DISCORD_ONLY": "true", "DRY_RUN": "false"}):
            with self.assertRaisesRegex(RuntimeError, "only with DRY_RUN=true"):
                scanner.scan_once()

    def test_rblxvalue_key_verification_exits_before_full_scanner_run(self):
        from unittest.mock import patch

        with (
            patch.dict("os.environ", {"RBLXVALUE_FORCE_KEY_CHECK": "true"}),
            patch.object(scanner, "install_redacting_streams"),
            patch.object(scanner.rblxvalue_guard, "reset_run_counter"),
            patch.object(
                scanner, "api_get_json_conditional",
                return_value=({"items": [{"id": "test-item"}]}, None, False),
            ) as api_check,
            patch.object(scanner.rblxvalue_guard, "log_request_count") as log_count,
            patch.object(scanner, "run_scan_loop") as full_scan,
        ):
            self.assertEqual(scanner.main(), 0)

        api_check.assert_called_once_with("/items", {"limit": 1, "offset": 0})
        log_count.assert_called_once_with()
        full_scan.assert_not_called()


    def test_discord_only_cash_dry_run_skips_supreme_and_bloxswaps(self):
        from unittest.mock import patch

        with (
            patch.dict("os.environ", {
                "DRY_RUN": "true",
                "DISCORD_ONLY": "true",
                "MARKET_BOOK_ENABLED": "true",
                "PRIVATE_STATE_ENABLED": "true",
                "TELEGRAM_COMMANDS_ENABLED": "false",
                "INVENTORY_ENABLED": "false",
            }),
            patch.object(scanner, "update_metrics"),
            patch.object(scanner, "write_step_summary"),
            patch.object(scanner, "load_supreme_values") as load_values,
            patch.object(scanner, "fetch_bloxswaps_all") as fetch_bloxswaps,
            patch("telegram_market.ingest_manual_discord_imports", return_value=(1, 0)),
            patch("telegram_market._load_market", return_value={"book": {}, "direct": [], "chains": []}),
        ):
            scanner.scan_once()

        load_values.assert_not_called()
        fetch_bloxswaps.assert_not_called()


    def test_scan_loop_stops_immediately_when_rblx_budget_is_exhausted(self):
        from unittest.mock import patch

        budget_error = RuntimeError("[rblxvalue-budget-exhausted] rolling 24-hour budget reached")
        with (
            patch.object(scanner, "scan_once", side_effect=budget_error) as scan_once,
            patch.object(scanner, "record_failure") as record_failure,
            patch.object(scanner.rblxvalue_guard, "log_request_count") as log_count,
            patch.object(scanner, "update_metrics"),
        ):
            with self.assertRaisesRegex(RuntimeError, "budget-exhausted"):
                scanner.run_scan_loop(duration_seconds=600, interval_seconds=30)

        scan_once.assert_called_once()
        record_failure.assert_called_once_with(budget_error)
        log_count.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
