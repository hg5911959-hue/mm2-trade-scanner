from __future__ import annotations

import unittest

import scanner
from test_support import IsolatedDataTestCase


class SupremePageMergeTests(IsolatedDataTestCase):
    def test_source_b_is_supreme_value_and_provider_stability_is_preserved(self):
        values = {}
        numeric_count = scanner._merge_supreme_page_items(
            values,
            [
                {
                    "id": "bat-1",
                    "slug": "bat",
                    "name": "Bat",
                    "value_source_b": "125",
                    "value_source_a": "80",
                    "value": "999",
                    "category": "Weapons",
                    "type": "Knife",
                    "stability": "  Rising  ",
                },
                {
                    "id": "generic-only",
                    "slug": "generic-only",
                    "name": "Generic Only",
                    "value": 999,
                    "category": "Weapons",
                    "type": "Gun",
                    "stability": None,
                },
            ],
        )

        self.assertEqual(numeric_count, 1)
        bat = values[scanner.normalize_name("Bat")]
        self.assertEqual(bat["value"], 125.0)
        self.assertEqual(bat["value_min"], 125.0)
        self.assertEqual(bat["value_max"], 125.0)
        self.assertEqual(bat["secondary_value"], 80.0)
        self.assertEqual(bat["variants"][0]["rblxvalue_value"], 999.0)
        self.assertEqual(bat["variants"][0]["stability"], "Rising")
        generic = values[scanner.normalize_name("Generic Only")]
        self.assertIsNone(generic["value"])
        self.assertIsNone(generic["variants"][0]["value"])
        self.assertEqual(generic["variants"][0]["rblxvalue_value"], 999.0)

    def test_same_name_distinct_identities_never_form_a_synthetic_range(self):
        values = {}
        numeric_count = scanner._merge_supreme_page_items(
            values,
            [
                {
                    "id": "bat-knife",
                    "slug": "bat-knife",
                    "name": "Bat",
                    "value_source_b": 125,
                    "category": "Weapons",
                    "type": "Knife",
                },
                {
                    "id": "bat-gun",
                    "slug": "bat-gun",
                    "name": "Bat",
                    "value_source_b": 225,
                    "category": "Weapons",
                    "type": "Gun",
                },
            ],
        )

        record = values[scanner.normalize_name("Bat")]
        self.assertEqual(numeric_count, 2)
        self.assertEqual(len(record["variants"]), 2)
        self.assertIsNone(record["value"])
        self.assertIsNone(record["value_min"])
        self.assertIsNone(record["value_max"])
        self.assertEqual(
            {variant["value"] for variant in record["variants"]},
            {125.0, 225.0},
        )

    def test_replacing_same_identity_adjusts_numeric_count_without_duplicates(self):
        values = {}
        base = {
            "id": "bat-knife",
            "slug": "bat-knife",
            "name": "Bat",
            "category": "Weapons",
            "type": "Knife",
        }

        first = scanner._merge_supreme_page_items(values, [{**base, "value_source_b": None}])
        second = scanner._merge_supreme_page_items(values, [{**base, "value_source_b": 30}])
        third = scanner._merge_supreme_page_items(values, [{**base, "value_source_b": None}])

        record = values[scanner.normalize_name("Bat")]
        self.assertEqual((first, second, third), (0, 1, -1))
        self.assertEqual(len(record["variants"]), 1)
        self.assertIsNone(record["value"])


if __name__ == "__main__":
    unittest.main()
