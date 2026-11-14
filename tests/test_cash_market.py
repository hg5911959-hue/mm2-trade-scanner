import csv
import tempfile
import unittest
from pathlib import Path

from tools.cash_market import (
    calculate_cash_side, cash_profit_bounds, load_cash_price_catalog,
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

    def test_user_seed_catalogue_contains_all_190_supplied_rows_as_unverified(self):
        catalog = load_cash_price_catalog("data/cash_prices.csv")
        self.assertEqual(catalog["row_count"], 190)
        self.assertEqual(
            sum(row["status"] == "unverified" for row in catalog["items"].values()),
            190,
        )
        self.assertEqual(
            sum(row["status"] == "verified" for row in catalog["items"].values()),
            0,
        )

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

    def test_duplicate_normalized_names_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self._catalog([self._row("Chroma-Laser"), self._row("chroma laser")])

    def test_invalid_or_nonpositive_price_is_rejected(self):
        for price in ("0", "-1", "nan", "inf"):
            with self.subTest(price=price):
                with self.assertRaises(RuntimeError):
                    self._catalog([self._row(price=price)])

    def test_verified_row_requires_timestamp(self):
        with self.assertRaises(RuntimeError):
            self._catalog([self._row(verified="") | {"verified_at_utc": ""}])


if __name__ == "__main__":
    unittest.main()
