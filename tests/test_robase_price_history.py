import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_support import IsolatedDataTestCase
from tools.robase_price_history import record_daily_snapshot


def make_snapshot(*, fetched_at="2026-11-23T12:00:00Z", count=222):
    items = []
    for index in range(count):
        items.append({
            "robase_slug": f"item-{index:03d}",
            "name": f"Item {index}",
            "cash_price_usd": None if index == 0 else 1.25 + index,
            "price_display": "" if index == 0 else "$" + f"{1.25 + index:.2f}",
            "price_window_days": None if index == 0 else 7,
            "selected_marketplace": None if index == 0 else "chromas.gg",
            "selected_marketplace_sold_7d": 0 if index == 0 else (1 if index == 1 else 10),
            "status": "verified",
        })
    return {
        "schema_version": 6,
        "source": "https://robase.gg/",
        "fetched_at_utc": fetched_at,
        "price_basis": "chromas_gg_7d_price",
        "catalog_expected_count": count,
        "catalog_complete": True,
        "items": items,
    }


class RobaseDailyHistoryTests(IsolatedDataTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "robase_price_history.json"
        self.now = datetime(2026, 11, 23, 12, 5, tzinfo=timezone.utc)

    def test_records_complete_daily_snapshot_and_preserves_sample_quality(self):
        result = record_daily_snapshot(make_snapshot(), self.path, now=self.now)
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["item_count"], 222)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["price_basis"], "chromas_gg_7d_price")
        self.assertEqual(len(document["days"]), 1)
        day = document["days"][0]
        self.assertEqual(day["date_utc"], "2026-11-23")
        self.assertEqual(day["item_count"], 222)
        rows = {row["robase_slug"]: row for row in day["items"]}
        self.assertEqual(rows["item-000"]["sample_quality"], "no_chromas_7d_sales")
        self.assertEqual(rows["item-001"]["sample_quality"], "low_sample_7d")
        self.assertEqual(rows["item-002"]["sample_quality"], "7d_sample")
        self.assertIsNone(rows["item-000"]["price_usd"])
        self.assertIsNone(rows["item-000"]["price_window_days"])
        self.assertEqual(rows["item-000"]["sold_7d"], 0)
        self.assertEqual(rows["item-001"]["price_window_days"], 7)

    def test_same_utc_day_is_idempotent_and_does_not_overwrite_first_capture(self):
        first = make_snapshot()
        record_daily_snapshot(first, self.path, now=self.now)
        later_now = datetime(2026, 11, 23, 23, 50, tzinfo=timezone.utc)
        later = make_snapshot(fetched_at="2026-11-23T23:45:00Z")
        result = record_daily_snapshot(later, self.path, now=later_now)
        self.assertEqual(result["status"], "already_recorded")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(document["days"]), 1)
        self.assertEqual(document["days"][0]["captured_at_utc"], "2026-11-23T12:00:00Z")

    def test_rejects_incomplete_catalogue_and_wrong_price_basis(self):
        incomplete = make_snapshot()
        incomplete["catalog_complete"] = False
        with self.assertRaisesRegex(ValueError, "incomplete"):
            record_daily_snapshot(incomplete, self.path, now=self.now)
        wrong_basis = make_snapshot()
        wrong_basis["price_basis"] = "buyer_price"
        with self.assertRaisesRegex(ValueError, "price basis"):
            record_daily_snapshot(wrong_basis, self.path, now=self.now)
        self.assertFalse(self.path.exists())

    def test_rejects_stale_or_future_day_snapshot(self):
        stale = make_snapshot(fetched_at="2026-11-22T23:59:00Z")
        with self.assertRaisesRegex(ValueError, "current UTC date"):
            record_daily_snapshot(stale, self.path, now=self.now)
        future = make_snapshot(fetched_at="2026-11-23T13:00:00Z")
        with self.assertRaisesRegex(ValueError, "future"):
            record_daily_snapshot(future, self.path, now=self.now)

    def test_rejects_duplicate_slugs_invalid_prices_and_low_catalogue_count(self):
        duplicate = make_snapshot()
        duplicate["items"][1]["robase_slug"] = duplicate["items"][0]["robase_slug"]
        with self.assertRaisesRegex(ValueError, "duplicated"):
            record_daily_snapshot(duplicate, self.path, now=self.now)
        invalid_price = make_snapshot()
        invalid_price["items"][2]["cash_price_usd"] = float("nan")
        with self.assertRaisesRegex(ValueError, "price is invalid"):
            record_daily_snapshot(invalid_price, self.path, now=self.now)
        too_small = make_snapshot(count=10)
        with self.assertRaisesRegex(ValueError, "complete item catalogue"):
            record_daily_snapshot(too_small, self.path, now=self.now)

    def test_legacy_history_is_reset_instead_of_mixing_other_market_or_30d_prices(self):
        legacy = {
            "schema_version": 1,
            "source": "https://robase.gg/",
            "price_basis": "seller_proceeds_after_marketplace_fees",
            "days": [{"date_utc": "2026-11-22", "items": [{"price_usd": 123.0}]}],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        result = record_daily_snapshot(make_snapshot(), self.path, now=self.now)
        self.assertEqual(result["status"], "recorded")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["price_basis"], "chromas_gg_7d_price")
        self.assertEqual(document["legacy_days_discarded"], 1)
        self.assertEqual(len(document["days"]), 1)
        self.assertEqual(document["days"][0]["items"][0]["sample_quality"], "no_chromas_7d_sales")

    def test_rejects_corrupt_existing_history_without_overwriting_it(self):
        self.path.write_text('{"schema_version":999}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema or source"):
            record_daily_snapshot(make_snapshot(), self.path, now=self.now)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"schema_version":999}')


if __name__ == "__main__":
    unittest.main()
