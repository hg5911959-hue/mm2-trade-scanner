import unittest
from test_support import IsolatedDataTestCase
from datetime import UTC, datetime

from tools.next_to_verify import rank_blockers


class NextToVerifyTests(IsolatedDataTestCase):
    def test_rank_and_cumulative_unlock_use_listing_cooccurrence_fixture(self):
        metrics = {
            "2026-11-13": {
                "cash_blockers": {"spirit": 4, "bat": 3, "candy": 2},
                "cash_blocker_groups": {"bat|spirit": 2, "candy|spirit": 2, "bat": 1},
            }
        }
        catalog = {"items": {
            "spirit": {"cash_price_usd": 8.0, "verified_at_utc": "2026-11-13T00:00:00Z"},
            "bat": {"cash_price_usd": 2.0, "verified_at_utc": "2026-11-12T00:00:00Z"},
        }}
        ranked, total = rank_blockers(
            metrics, catalog, now=datetime(2026, 11, 14, tzinfo=UTC), limit=15
        )
        self.assertEqual(total, 5)
        self.assertEqual([row["name"] for row in ranked], ["spirit", "bat", "candy"])
        self.assertAlmostEqual(ranked[0]["unlocked_percent"], 0.0)
        self.assertAlmostEqual(ranked[1]["unlocked_percent"], 60.0)
        self.assertEqual(ranked[0]["price"], 8.0)
        self.assertAlmostEqual(ranked[0]["age_days"], 1.0)

    def test_duplicate_robase_names_are_not_reported_as_unique_prices(self):
        metrics = {"2026-11-13": {"cash_blockers": {"bat": 2}, "cash_blocker_groups": {"bat": 2}}}
        catalog = {"items": {"bat": {"cash_price_usd": 2.0}}, "duplicate_name_keys": ["bat"]}
        ranked, _ = rank_blockers(metrics, catalog, now=datetime(2026, 11, 14, tzinfo=UTC))
        self.assertEqual(ranked[0]["status"], "ambiguous")
        self.assertIsNone(ranked[0]["price"])

    def test_format_blockers_labels_robase_prices_and_ambiguous_names(self):
        metrics = {"2026-11-13": {"cash_blockers": {"spirit": 2, "bat": 1}, "cash_blocker_groups": {"bat|spirit": 1, "spirit": 1}}}
        catalog = {
            "items": {"spirit": {"cash_price_usd": 8.0, "verified_at_utc": "2026-11-13T00:00:00Z"}},
            "duplicate_name_keys": ["bat"],
        }
        from tools.next_to_verify import format_blockers
        report = format_blockers(metrics, catalog, now=datetime(2026, 11, 14, tzinfo=UTC))
        self.assertIn("ROBASE $8.00", report)
        self.assertIn("ambiguous ROBASE name", report)

    def test_no_blocker_data_returns_empty_ranking(self):
        ranked, total = rank_blockers({}, {"items": {}})
        self.assertEqual((ranked, total), ([], 0))


if __name__ == "__main__":
    unittest.main()
