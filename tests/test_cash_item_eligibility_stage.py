from __future__ import annotations

import unittest
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class CashItemEligibilityTests(IsolatedDataTestCase):
    def setUp(self):
        super().setUp()
        self.item = {
            "name": "Bat",
            "catalog_slug": "bat",
            "catalog_type": "knife",
            "quantity": 2,
            "quantity_min": 2,
            "quantity_max": 2,
            "quantity_source": "explicit",
        }
        self.mapping = {
            "rblx_slug": "bat",
            "type": "knife",
            "category": "weapons",
            "robase_slug": "bat",
            "robase_name": "Bat",
            "robase_identity_source": "exact_slug",
        }
        self.cash_record = {
            "name": "Bat",
            "status": "verified",
            "price_display": "$5.00",
        }
        self.selected = {
            "id": "bat-1",
            "slug": "bat",
            "name": "Bat",
            "type": "knife",
            "category": "weapons",
            "stability": "Rising",
            "secondary_value": 80,
            "raw_source_value": 125,
            "demand": 6,
            "rarity": 4,
        }

    def _prepare(self, *, item=None, selected=None, cash_record=None, fresh=True):
        record = {"variants": [self.selected if selected is None else selected]}
        with (
            patch.object(scanner, "resolve_value_record", return_value=(record, "slug", [])),
            patch.object(scanner, "cash_price_is_fresh", return_value=fresh),
            patch.object(scanner, "_supreme_numeric_value", return_value=(125.0, "numeric")),
            patch.object(scanner, "_rblxvalue_blended_value", return_value=90.0),
        ):
            return scanner._prepare_cash_listing_item(
                self.item if item is None else item,
                {},
                {},
                {"bat": self.mapping},
                {"bat": self.cash_record if cash_record is None else cash_record},
            )

    def test_valid_item_returns_exact_identity_and_reference_metadata(self):
        prepared, reason = self._prepare()

        self.assertIsNone(reason)
        self.assertEqual(prepared["name"], "Bat")
        self.assertEqual(prepared["display_name"], "Bat [weapons/knife]")
        self.assertEqual(prepared["quantity_min"], 2)
        self.assertEqual(prepared["quantity_max"], 2)
        self.assertEqual(prepared["stability"], "Rising")
        self.assertEqual(prepared["supreme_value"], 125.0)
        self.assertEqual(prepared["rblx_value"], 90.0)
        self.assertEqual(prepared["mm2values_value"], 80.0)
        self.assertEqual(prepared["robase_price_display"], "$5.00")

    def test_missing_catalog_slug_and_non_weapon_are_rejected_early(self):
        missing_slug, reason = self._prepare(item={**self.item, "catalog_slug": ""})
        self.assertIsNone(missing_slug)
        self.assertIn("catalogue slug missing", reason)

        pet, reason = self._prepare(item={**self.item, "catalog_type": "pet"})
        self.assertIsNone(pet)
        self.assertIn("not a ROBASE weapon", reason)

    def test_identity_and_type_mismatches_fail_closed(self):
        wrong_identity, reason = self._prepare(
            selected={**self.selected, "slug": "other"}
        )
        self.assertIsNone(wrong_identity)
        self.assertIn("identity mismatch", reason)

        wrong_type, reason = self._prepare(
            selected={**self.selected, "type": "gun"}
        )
        self.assertIsNone(wrong_type)
        self.assertIn("type/rarity mismatch", reason)

    def test_stale_price_untradeable_item_and_uncertain_quantity_are_rejected(self):
        stale, reason = self._prepare(fresh=False)
        self.assertIsNone(stale)
        self.assertIn("not freshly verified", reason)

        untradeable, reason = self._prepare(
            selected={**self.selected, "stability": "Untradeable"}
        )
        self.assertIsNone(untradeable)
        self.assertIn("untradeable", reason)

        uncertain, reason = self._prepare(
            item={**self.item, "quantity_source": "uncertain"}
        )
        self.assertIsNone(uncertain)
        self.assertIn("quantity not confirmed exactly", reason)


if __name__ == "__main__":
    unittest.main()
