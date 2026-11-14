import unittest

import scanner


class CashBlockerAggregationTests(unittest.TestCase):
    def test_counts_each_normalized_item_once_per_listing(self):
        counts = {}
        groups = {}
        blocked = scanner.accumulate_cash_blockers(
            ["Spirit (cash price missing)", "SPIRIT (cash price expired)",
             "Bat (cash price unverified)", "Other (quantity invalid)"],
            counts, groups,
        )
        self.assertEqual(blocked, {"spirit", "bat"})
        self.assertEqual(counts, {"spirit": 1, "bat": 1})
        self.assertEqual(groups, {"bat|spirit": 1})


if __name__ == "__main__":
    unittest.main()
