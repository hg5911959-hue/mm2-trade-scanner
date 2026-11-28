from __future__ import annotations

import unittest
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class RobaseRblxvalueCrosswalkEntryTests(IsolatedDataTestCase):
    def setUp(self):
        super().setUp()
        self.row = {
            "name": "Bat",
            "robase_slug": "bat",
            "robase_category": "legendary",
        }
        self.details = {
            "displayName": "Bat",
            "slug": "bat",
            "rarity": "legendary",
            "type": "knife",
            "chroma": False,
        }
        self.selected = {
            "name": "Bat",
            "slug": "bat",
            "type": "knife",
            "category": scanner.ROBASE_CATEGORY_TO_RBLX.get("legendary", "legendary"),
            "value_source_b": 100,
            "raw_source_value": 100,
            "secondary_value": 80,
            "stability": "Rising",
            "demand": 6,
        }

    def test_valid_crosswalk_entry_preserves_exact_identity_and_source_fields(self):
        with (
            patch.object(
                scanner,
                "resolve_value_record",
                return_value=({"variants": [self.selected]}, "slug", []),
            ),
            patch.object(scanner, "_supreme_numeric_value", return_value=(100.0, "numeric")),
            patch.object(scanner, "_rblxvalue_blended_value", return_value=90.0),
        ):
            slug, entry, failure, label = scanner._build_robase_rblxvalue_mapping_entry(
                self.row, self.details, "exact_slug", {}, {}
            )

        self.assertEqual(slug, "bat")
        self.assertIsNone(failure)
        self.assertIsNone(label)
        self.assertEqual(entry["robase_identity_source"], "exact_slug")
        self.assertEqual(entry["rblx_slug"], "bat")
        self.assertEqual(entry["value"], 100.0)
        self.assertEqual(entry["rblxvalue_value"], 90.0)
        self.assertEqual(entry["supreme_value"], 100.0)
        self.assertEqual(entry["mm2values_value"], 80.0)
        self.assertEqual(entry["stability"], "Rising")

    def test_ambiguous_value_identity_is_reported_as_ambiguous(self):
        with patch.object(
            scanner,
            "resolve_value_record",
            return_value=(None, "ambiguous", ["bat-one", "bat-two"]),
        ):
            slug, entry, failure, label = scanner._build_robase_rblxvalue_mapping_entry(
                self.row, self.details, "exact_slug", {}, {}
            )

        self.assertEqual(slug, "bat")
        self.assertIsNone(entry)
        self.assertEqual(failure, "ambiguous")
        self.assertEqual(label, "Bat [bat]")

    def test_metadata_mismatch_is_not_accepted_as_an_exact_crosswalk(self):
        wrong = {**self.selected, "type": "gun"}
        with patch.object(
            scanner,
            "resolve_value_record",
            return_value=({"variants": [wrong]}, "slug", []),
        ):
            slug, entry, failure, label = scanner._build_robase_rblxvalue_mapping_entry(
                self.row, self.details, "exact_slug", {}, {}
            )

        self.assertEqual(slug, "bat")
        self.assertIsNone(entry)
        self.assertEqual(failure, "missing")
        self.assertEqual(label, "Bat [bat: metadata mismatch]")


if __name__ == "__main__":
    unittest.main()
