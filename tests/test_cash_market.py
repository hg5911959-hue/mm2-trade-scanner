import csv
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.cash_market import (
    calculate_cash_side,
    cash_profit_bounds,
    load_cash_price_catalog,
    normalize_cash_name,
)


class CashMarketTests(unittest.TestCase):
    def _catalog(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cash.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "name", "cash_price_usd", "tier", "status",
                "verified_at_utc", "source", "notes",
            ])
            writer.writeheader()
            writer.writerows(rows)
        return load_cash_price_catalog(path)

    def _row(self, name="Chroma Laser", price="10", status="verified", verified="2026-11-14T00:00:00Z"):
        return {
            "name": name, "cash_price_usd": price, "tier": "test",
            "status": status, "verified_at_utc": verified if status == "verified" else "",
            "source": "manual", "notes": "",
        }

    def test_name_normalization_handles_apostrophes_and_case(self):
        self.assertEqual(normalize_cash_name("Traveler’s Axe"), normalize_cash_name("TRAVELER'S AXE"))

    def test_legacy_catalogue_rows_remain_unverified_except_user_confirmed_sales(self):
        catalog = load_cash_price_catalog("data/cash_prices.csv")
        self.assertEqual(catalog["row_count"], 190)
        self.assertEqual(
            sum(row["status"] == "unverified" for row in catalog["items"].values()),
            179,
        )
        self.assertEqual(
            sum(row["status"] == "verified" for row in catalog["items"].values()),
            11,
        )

    def test_user_confirmed_selling_prices_are_loaded_with_provenance(self):
        catalog = load_cash_price_catalog("data/cash_prices.csv")
        expected = {
            "Spirit": 8.00, "Harvester": 5.50, "Bat": 2.80,
            "Immortal": 2.80, "Icebreaker": 1.10, "Bloom": 7.00,
            "Snowstorm": 4.00, "Candy": 2.20, "Watergun": 2.60,
            "Evergun": 65.00, "Celestial": 34.00,
        }
        for name, price in expected.items():
            with self.subTest(name=name):
                row = catalog["items"][normalize_cash_name(name)]
                self.assertEqual(row["cash_price_usd"], price)
                self.assertEqual(row["status"], "verified")
                self.assertEqual(row["source"], "user_confirmed_selling_prices")
                self.assertIn("not independently cross-checked", row["notes"])

    def test_verified_catalogue_prices_can_be_used(self):
        catalog = self._catalog([self._row()])
        low, high, lines, missing = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "receive", 15,
        )
        self.assertAlmostEqual(low, 8.5)
        self.assertEqual(low, high)
        self.assertEqual(missing, [])
        self.assertIn("$8.50", lines[0])

    def test_13_day_old_verified_price_is_accepted(self):
        now = datetime(2026, 11, 14, tzinfo=UTC)
        catalog = self._catalog([self._row(verified=(now - timedelta(days=13)).isoformat())])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now,
        )
        self.assertEqual(result[3], [])
        self.assertEqual(result[0], 10.0)

    def test_15_day_old_verified_price_is_rejected_as_expired(self):
        now = datetime(2026, 11, 14, tzinfo=UTC)
        catalog = self._catalog([self._row(verified=(now - timedelta(days=15)).isoformat())])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now,
        )
        self.assertIn("cash price expired", result[3][0])

    def test_future_verified_timestamp_is_rejected(self):
        now = datetime(2026, 11, 14, tzinfo=UTC)
        catalog = self._catalog([self._row(verified=(now + timedelta(minutes=1)).isoformat())])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now,
        )
        self.assertIn("cash price expired", result[3][0])

    def test_unverified_legacy_prices_are_available_only_in_explicit_estimate_mode(self):
        catalog = self._catalog([self._row(price="10", status="unverified")])
        item = [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}]
        strict = calculate_cash_side(item, catalog, "receive", 15)
        self.assertEqual((strict[0], strict[1], strict[2]), (0.0, 0.0, []))
        estimate = calculate_cash_side(
            item, catalog, "receive", 15, allow_unverified_estimates=True,
            estimate_uncertainty_pct=30,
        )
        self.assertAlmostEqual(estimate[0], 5.95)
        self.assertAlmostEqual(estimate[1], 11.05)
        self.assertIn("ESTIMATE", estimate[2][0])
        self.assertEqual(estimate[3], [])

    def test_estimate_mode_does_not_use_disabled_rows(self):
        catalog = self._catalog([self._row(price="10", status="disabled")])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser"}], catalog, "receive",
            allow_unverified_estimates=True,
        )
        self.assertEqual(result[:3], (0.0, 0.0, []))
        self.assertIn("unverified", result[3][0])

    def test_estimate_uncertainty_is_validated(self):
        catalog = self._catalog([self._row(status="unverified")])
        for uncertainty in (-1, 100, float("nan"), float("inf")):
            with self.subTest(uncertainty=uncertainty), self.assertRaises(ValueError):
                calculate_cash_side(
                    [{"canonical_name": "Chroma Laser"}], catalog, "give",
                    allow_unverified_estimates=True,
                    estimate_uncertainty_pct=uncertainty,
                )

    def test_unverified_prices_never_enter_cash_profit(self):
        catalog = self._catalog([self._row(status="unverified")])
        low, high, lines, missing = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "receive", 15,
        )
        self.assertEqual((low, high, lines), (0.0, 0.0, []))
        self.assertIn("cash price unverified", missing[0])

    def test_missing_cash_price_fails_closed(self):
        catalog = self._catalog([])
        result = calculate_cash_side(
            [{"canonical_name": "Unknown", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 15,
        )
        self.assertIn("cash price missing", result[3][0])

    def test_quantity_bounds_produce_conservative_side_bounds(self):
        catalog = self._catalog([self._row(price="10")])
        low, high, _lines, missing = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 2}],
            catalog, "receive", 20,
        )
        self.assertEqual((low, high), (8.0, 16.0))
        self.assertEqual(missing, [])

    def test_cash_profit_uses_worst_case_not_supreme_value(self):
        self.assertEqual(cash_profit_bounds((5, 8), (12, 15)), (4, 10))

    def test_fractional_quantity_is_rejected_instead_of_truncated(self):
        catalog = self._catalog([self._row(price="10")])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1.5, "quantity_max": 2}],
            catalog, "give", 0,
        )
        self.assertIn("quantity invalid", result[3][0])

    def test_boolean_quantity_is_rejected(self):
        catalog = self._catalog([self._row(price="10")])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": True, "quantity_max": 1}],
            catalog, "give", 0,
        )
        self.assertIn("quantity invalid", result[3][0])

    def test_integral_float_quantity_is_accepted_without_rounding(self):
        catalog = self._catalog([self._row(price="10")])
        result = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 2.0, "quantity_max": 2.0}],
            catalog, "give", 0,
        )
        self.assertEqual((result[0], result[1]), (20.0, 20.0))
        self.assertEqual(result[3], [])

    def test_cash_profit_rejects_non_finite_or_negative_bounds(self):
        for bounds in (((float("nan"), 1), (2, 3)),
                       ((-1, 1), (2, 3)),
                       ((0, float("inf")), (2, 3))):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                cash_profit_bounds(*bounds)

    def test_cash_profit_rejects_inverted_bounds(self):
        with self.assertRaises(ValueError):
            cash_profit_bounds((8, 5), (12, 15))

    def test_duplicate_normalized_names_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self._catalog([self._row("Chroma-Laser"), self._row("chroma laser")])

    def test_duplicate_csv_headers_are_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cash.csv"
        path.write_text(
            "name,name,cash_price_usd,tier,status,verified_at_utc,source,notes\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate columns"):
            load_cash_price_catalog(path)

    def test_missing_csv_row_fields_are_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cash.csv"
        path.write_text(
            "name,cash_price_usd,tier,status,verified_at_utc,source,notes\n"
            "Item,10,test,unverified,,manual\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "Missing CSV field"):
            load_cash_price_catalog(path)

    def test_extra_csv_row_fields_are_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cash.csv"
        path.write_text(
            "name,cash_price_usd,tier,status,verified_at_utc,source,notes\n"
            "Item,10,test,unverified,,manual,note,unexpected\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "Extra CSV fields"):
            load_cash_price_catalog(path)

    def test_invalid_or_nonpositive_price_is_rejected(self):
        for price in ("0", "-1", "nan", "inf"):
            with self.subTest(price=price), self.assertRaises(RuntimeError):
                self._catalog([self._row(price=price)])

    def test_verified_row_requires_timestamp(self):
        with self.assertRaises(RuntimeError):
            self._catalog([self._row(verified="") | {"verified_at_utc": ""}])

    def test_verified_row_requires_utc_iso_timestamp(self):
        for timestamp in ("not-a-date", "2026-11-14T12:00:00+01:00"):
            with self.subTest(timestamp=timestamp), self.assertRaises(RuntimeError):
                self._catalog([self._row(verified=timestamp)])



if __name__ == "__main__":
    unittest.main()
