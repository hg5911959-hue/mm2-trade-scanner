import unittest
from datetime import UTC, datetime

from test_support import IsolatedDataTestCase

from tools.cash_market import calculate_cash_side
from tools.daily_chart_shadow import build_daily_chart_shadow_catalog


class DailyChartShadowTests(IsolatedDataTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
        self.live = {
            "items_by_slug": {
                "bat": {"name": "Bat", "cash_price_usd": 9.0, "price_low_usd": 9.0, "price_high_usd": 9.0,
                        "price_display": "$9.00", "tier": "robase", "status": "verified",
                        "verified_at_utc": "2026-11-25T11:59:00+00:00", "price_sample_count": 25,
                        "price_window_days": 7, "robase_slug": "bat"},
                "heat": {"name": "Heat", "cash_price_usd": 8.0, "price_low_usd": 8.0, "price_high_usd": 8.0,
                         "price_display": "$8.00", "tier": "robase", "status": "verified",
                         "verified_at_utc": "2026-11-25T11:59:00+00:00", "price_sample_count": 20,
                         "price_window_days": 7, "robase_slug": "heat"},
                "pet": {"name": "Pet", "cash_price_usd": 5.0, "price_low_usd": 5.0, "price_high_usd": 5.0,
                        "price_display": "$5.00", "tier": "robase", "status": "verified",
                        "verified_at_utc": "2026-11-25T11:59:00+00:00", "price_sample_count": 12,
                        "price_window_days": 7, "robase_slug": "pet"},
            },
            "items": {},
        }

    def matrix(self):
        return {
            "bat": {"robase_slug": "bat", "date": "2026-11-24", "all_marketplaces_sold": 142,
                    "cash_price_usd": 3.7252331, "eligible_for_cash_alerts": True, "status": "verified_daily_close"},
            "heat": {"robase_slug": "heat", "date": "2026-11-24", "all_marketplaces_sold": 2,
                     "cash_price_usd": 7.2, "eligible_for_cash_alerts": False, "status": "low_sample_daily_close"},
        }

    def test_only_daily_eligible_price_replaces_live_rolling_price(self):
        shadow, stats = build_daily_chart_shadow_catalog(
            self.live, self.matrix(), now=self.now, expected_count=2
        )
        self.assertEqual(shadow["items_by_slug"]["bat"]["cash_price_usd"], 3.7252331)
        self.assertEqual(shadow["items_by_slug"]["bat"]["price_sample_count"], 142)
        self.assertIsNone(shadow["items_by_slug"]["heat"]["cash_price_usd"])
        self.assertEqual(shadow["items_by_slug"]["heat"]["status"], "unverified")
        self.assertIsNone(shadow["items_by_slug"]["pet"]["cash_price_usd"])
        self.assertEqual(shadow["items_by_slug"]["pet"]["status"], "unverified")
        self.assertEqual(stats["eligible"], 1)
        self.assertEqual(stats["unavailable_or_low_sample"], 1)
        self.assertEqual(self.live["items_by_slug"]["bat"]["cash_price_usd"], 9.0)

    def test_low_sample_has_no_rolling_price_fallback(self):
        shadow, _ = build_daily_chart_shadow_catalog(
            self.live, self.matrix(), now=self.now, expected_count=2
        )
        item = [{"name": "Heat", "canonical_name": "Heat", "robase_slug": "heat",
                 "quantity_min": 1, "quantity_max": 1}]
        _low, _high, _lines, missing = calculate_cash_side(
            item, shadow, "give", 0, max_age_days=1, now=self.now
        )
        self.assertTrue(missing)
        self.assertIn("low sample", missing[0])

    def test_eligible_daily_price_is_used_by_existing_profit_calculator(self):
        shadow, _ = build_daily_chart_shadow_catalog(
            self.live, self.matrix(), now=self.now, expected_count=2
        )
        item = [{"name": "Bat", "canonical_name": "Bat", "robase_slug": "bat",
                 "quantity_min": 1, "quantity_max": 1}]
        low, high, _lines, missing = calculate_cash_side(
            item, shadow, "give", 0, max_age_days=1, now=self.now
        )
        self.assertFalse(missing)
        self.assertAlmostEqual(low, 3.7252331)
        self.assertAlmostEqual(high, 3.7252331)

    def test_incomplete_daily_matrix_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly 2"):
            build_daily_chart_shadow_catalog(
                self.live, {"bat": self.matrix()["bat"]}, now=self.now, expected_count=2
            )


if __name__ == "__main__":
    unittest.main()
