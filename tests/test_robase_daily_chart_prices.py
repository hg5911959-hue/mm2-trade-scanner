import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.robase_daily_chart_prices import (
    FIELDS,
    load_daily_prices,
    load_previous_day_prices,
    previous_completed_day,
)


class DailyChartPriceLookupTests(unittest.TestCase):
    def _write(self, rows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "history.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_previous_day_uses_algeria_calendar_date_not_utc_date(self):
        now = datetime(2026, 11, 25, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(previous_completed_day(now), "2026-11-25")

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            previous_completed_day(datetime(2026, 11, 25, 12, 0))

    def test_previous_day_returns_exact_all_marketplaces_price(self):
        path = self._write([
            {"robase_slug": "bat", "item_id": "628", "item_name": "Bat", "date": "2026-11-24", "all_marketplaces_sold": "142", "all_marketplaces_price_after_fee": "3.7252331"},
            {"robase_slug": "heat", "item_id": "1", "item_name": "Heat", "date": "2026-11-24", "all_marketplaces_sold": "0", "all_marketplaces_price_after_fee": ""},
        ])
        now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        target, rows = load_previous_day_prices(path, {"bat", "heat"}, now=now)
        self.assertEqual(target, "2026-11-24")
        self.assertEqual(rows["bat"]["cash_price_usd"], 3.7252331)
        self.assertEqual(rows["bat"]["all_marketplaces_sold"], 142)
        self.assertIsNone(rows["heat"]["cash_price_usd"])
        self.assertEqual(rows["heat"]["status"], "unavailable_no_priced_sales")

    def test_missing_weapon_fails_closed(self):
        path = self._write([
            {"robase_slug": "bat", "item_id": "628", "item_name": "Bat", "date": "2026-11-24", "all_marketplaces_sold": "142", "all_marketplaces_price_after_fee": "3.7252331"},
        ])
        with self.assertRaisesRegex(ValueError, "incomplete daily weapon coverage"):
            load_daily_prices(path, "2026-11-24", {"bat", "heat"})

    def test_duplicate_weapon_day_fails_closed(self):
        row = {"robase_slug": "bat", "item_id": "628", "item_name": "Bat", "date": "2026-11-24", "all_marketplaces_sold": "142", "all_marketplaces_price_after_fee": "3.7252331"}
        path = self._write([row, row])
        with self.assertRaisesRegex(ValueError, "duplicate weapon/date"):
            load_daily_prices(path, "2026-11-24", {"bat"})


if __name__ == "__main__":
    unittest.main()
