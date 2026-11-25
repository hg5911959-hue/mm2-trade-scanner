import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_support import IsolatedDataTestCase

from tools.cash_market import (
    calculate_cash_side,
    cash_profit_bounds,
    load_cash_price_catalog,
    load_robase_price_catalog,
    normalize_cash_name,
    strict_quantity,
)


class CashMarketTests(IsolatedDataTestCase):
    def _catalog(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cash.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "name", "cash_price_usd", "tier", "status",
                "verified_at_utc", "source", "notes", "price_low_usd", "price_high_usd",
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

    def test_synthetic_fixture_has_verified_and_unverified_rows(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "cash_prices_test.csv"
        catalog = load_cash_price_catalog(fixture)
        self.assertEqual(catalog["row_count"], 2)
        self.assertEqual(catalog["verified_count"], 1)
        self.assertEqual(
            sum(row["status"] == "unverified" for row in catalog["items"].values()), 1
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
        self.assertNotIn("LOW SAMPLE", lines[0])

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

    def test_price_ranges_apply_conservative_bounds_on_both_sides(self):
        catalog = self._catalog([self._row(price="8") | {
            "price_low_usd": "7", "price_high_usd": "9"
        }])
        give = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0,
        )
        receive = calculate_cash_side(
            [{"canonical_name": "Chroma Laser", "quantity_min": 1, "quantity_max": 1}],
            catalog, "receive", 15,
        )
        self.assertEqual((give[0], give[1]), (7.0, 9.0))
        self.assertAlmostEqual(receive[0], 5.95)
        self.assertAlmostEqual(receive[1], 7.65)
        self.assertIn("haircut 15%", receive[2][0])

    def test_old_csv_header_without_optional_range_columns_remains_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.csv"
            path.write_text(
                "name,cash_price_usd,tier,status,verified_at_utc,source,notes\n"
                "Chroma Laser,8,test,verified,2026-11-14T00:00:00Z,manual,\n",
                encoding="utf-8",
            )
            catalog = load_cash_price_catalog(path)
            row = catalog["items"][normalize_cash_name("Chroma Laser")]
            self.assertEqual((row["price_low_usd"], row["price_high_usd"]), (8.0, 8.0))

    def test_invalid_price_ranges_are_rejected(self):
        for low, high in (("9", "10"), ("7", "7"), ("nan", "10"), ("7", "inf")):
            with self.subTest(low=low, high=high), self.assertRaises(RuntimeError):
                self._catalog([self._row(price="8") | {
                    "price_low_usd": low, "price_high_usd": high
                }])

    def test_tiered_haircut_boundaries(self):
        from tools.cash_market import haircut_for_price, parse_haircut_tiers
        tiers = parse_haircut_tiers("0-2:30,2-10:20,10-50:15,50-:10")
        self.assertEqual(haircut_for_price(2.00, tiers), 20)
        self.assertEqual(haircut_for_price(10.00, tiers), 15)
        self.assertEqual(haircut_for_price(50.00, tiers), 10)

    def test_tiered_haircut_rejects_gaps_overlaps_and_bad_percent(self):
        from tools.cash_market import parse_haircut_tiers
        for raw in ("0-2:20,3-:10", "0-3:20,2-:10", "0-:100", "1-:10", "0-2:10,2-3:10"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_haircut_tiers(raw)

    def test_cash_profit_uses_worst_case_not_supreme_value(self):
        self.assertEqual(cash_profit_bounds((5, 8), (12, 15)), (4, 10))

    def test_full_bloxswaps_stack_quantity_limit_is_supported(self):
        self.assertEqual(strict_quantity(9999), 9999)
        with self.assertRaises(ValueError):
            strict_quantity(10000)

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


 
    def _robase_snapshot(self, now):
        checked = now.isoformat().replace("+00:00", "Z")
        rows = []
        for i in range(222):
            rows.append({
                "name": f"Item {i}",
                "robase_slug": f"item-{i}",
                "robase_category": "Godly",
                "cash_price_usd": 2.5,
                "price_display": "$2.50",
                "sold_7d": 1 if i == 1 else (0 if i == 0 else 10),
                "sold_30d": 1 if i == 0 else 20,
                "price_window_days": 30 if i == 0 else 7,
                "status": "verified",
                "source": f"https://robase.gg/item/item-{i}",
                "last_checked_at_utc": checked,
                "price_updated_at_utc": checked,
                "bloxswaps_value": 100,
                "bloxswaps_copies": 1000,
                "bloxswaps_holders": 500,
            })
        return {
            "schema_version": 4,
            "source": "https://robase.gg/",
            "fetched_at_utc": checked,
            "price_basis": "seller_proceeds_after_marketplace_fees",
            "catalog_expected_count": 222,
            "catalog_complete": True,
            "items": rows,
        }

    def test_robase_catalog_uses_all_fresh_item_pages_even_when_sales_sample_is_low(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        snapshot = self._robase_snapshot(now)
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        catalog = load_robase_price_catalog(path, now=now + timedelta(minutes=10))
        self.assertEqual(catalog["row_count"], 222)
        self.assertEqual(catalog["verified_count"], 222)
        row = catalog["items"][normalize_cash_name("Item 1")]
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["price_display"], "$2.50")
        self.assertEqual(row["bloxswaps_value"], 100)
        self.assertEqual(row["price_window_days"], 7)
        self.assertEqual(row["price_sample_count"], 1)

    def test_low_sample_robase_prices_are_labeled_without_changing_cash_value(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        path.write_text(json.dumps(self._robase_snapshot(now)), encoding="utf-8")
        catalog = load_robase_price_catalog(path, now=now + timedelta(minutes=10))

        low, high, lines, missing = calculate_cash_side(
            [{"canonical_name": "Item 0", "robase_slug": "item-0", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now + timedelta(minutes=10),
        )
        self.assertEqual((low, high), (2.5, 2.5))
        self.assertEqual(missing, [])
        self.assertIn("[LOW SAMPLE 1/30d]", lines[0])

        normal = calculate_cash_side(
            [{"canonical_name": "Item 2", "robase_slug": "item-2", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now + timedelta(minutes=10),
        )
        self.assertEqual(normal[0], 2.5)
        self.assertNotIn("LOW SAMPLE", normal[2][0])

    def test_robase_catalog_rejects_any_stale_item_page(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        snapshot = self._robase_snapshot(now)
        snapshot["items"][1]["last_checked_at_utc"] = (now - timedelta(minutes=21)).isoformat().replace("+00:00", "Z")
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "not all freshly verified"):
            load_robase_price_catalog(path, now=now)

    def test_robase_catalog_rejects_incomplete_catalogue_and_duplicate_slugs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        snapshot = self._robase_snapshot(now)
        snapshot["catalog_complete"] = False
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "catalogue is incomplete"):
            load_robase_price_catalog(path, now=now)
        snapshot["catalog_complete"] = True
        snapshot["items"][1]["robase_slug"] = snapshot["items"][0]["robase_slug"]
        snapshot["items"][1]["source"] = snapshot["items"][0]["source"]
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Duplicate ROBASE source slug"):
            load_robase_price_catalog(path, now=now)

    def test_robase_snapshot_rejects_naive_or_non_utc_check_timestamps(self):
        now = datetime(2026, 11, 21, tzinfo=UTC)
        for timestamp in ("2026-11-21T00:00:00", "2026-11-21T01:00:00+01:00"):
            with self.subTest(timestamp=timestamp), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "robase.json"
                snapshot = self._robase_snapshot(now)
                snapshot["items"][0]["last_checked_at_utc"] = timestamp
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "verification timestamp"):
                    load_robase_price_catalog(path, now=now)

    def test_robase_snapshot_rejects_invalid_sales_sample_counts(self):
        now = datetime(2026, 11, 21, tzinfo=UTC)
        for field, value in (("sold_7d", True), ("sold_7d", 1.5), ("sold_30d", -1), ("sold_30d", "2")):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "robase.json"
                snapshot = self._robase_snapshot(now)
                snapshot["items"][0][field] = value
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "sales sample counts"):
                    load_robase_price_catalog(path, now=now)

    def test_robase_snapshot_rejects_naive_current_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "robase.json"
            now = datetime(2026, 11, 21, tzinfo=UTC)
            path.write_text(json.dumps(self._robase_snapshot(now)), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Current time"):
                load_robase_price_catalog(path, now=datetime(2026, 11, 21))

    def test_robase_catalog_preserves_duplicate_names_by_unique_slug(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        snapshot = self._robase_snapshot(now)
        snapshot["items"][0]["name"] = "Latte"
        snapshot["items"][1]["name"] = "Latte"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        catalog = load_robase_price_catalog(path, now=now)
        key = normalize_cash_name("Latte")
        self.assertEqual(catalog["row_count"], 222)
        self.assertEqual(len(catalog["items_by_slug"]), 222)
        self.assertIn(key, catalog["duplicate_name_keys"])
        self.assertNotIn(key, catalog["items"])

    def test_cash_side_uses_exact_robase_slug_for_duplicate_names(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "robase.json"
        now = datetime(2026, 11, 21, tzinfo=UTC)
        snapshot = self._robase_snapshot(now)
        snapshot["items"][0].update({"name": "Latte", "robase_slug": "latte-gun", "source": "https://robase.gg/item/latte-gun", "cash_price_usd": 3.03, "price_display": "$3.03"})
        snapshot["items"][1].update({"name": "Latte", "robase_slug": "latte-knife", "source": "https://robase.gg/item/latte-knife", "cash_price_usd": 3.30, "price_display": "$3.30"})
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        catalog = load_robase_price_catalog(path, now=now)
        result = calculate_cash_side(
            [{"canonical_name": "Latte", "robase_slug": "latte-knife", "quantity_min": 1, "quantity_max": 1}],
            catalog, "give", 0, now=now,
        )
        self.assertEqual(result[0], 3.30)
        self.assertEqual(result[1], 3.30)
        self.assertEqual(result[3], [])


if __name__ == "__main__":
    unittest.main()
