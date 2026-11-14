import unittest
from datetime import UTC, datetime

from tools.next_to_verify import rank_blockers


class NextToVerifyTests(unittest.TestCase):
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
        self.assertAlmostEqual(ranked[0]["unlocked_percent"], 40.0)
        self.assertAlmostEqual(ranked[1]["unlocked_percent"], 60.0)
        self.assertEqual(ranked[0]["price"], 8.0)
        self.assertAlmostEqual(ranked[0]["age_days"], 1.0)

    def test_no_blocker_data_returns_empty_ranking(self):
        ranked, total = rank_blockers({}, {"items": {}})
        self.assertEqual((ranked, total), ([], 0))


if __name__ == "__main__":
    unittest.main()
