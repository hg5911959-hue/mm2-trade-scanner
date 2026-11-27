from __future__ import annotations

from test_support import IsolatedDataTestCase
from tools.html_tree import PageTree
from tools.listing_identity import (
    identity_items,
    legacy_alert_state,
    legacy_stable_listing_id,
    listing_identifier,
    stable_listing_id,
)


def normalize(value):
    return " ".join(str(value).casefold().split())


class ListingIdentityTests(IsolatedDataTestCase):
    def test_identity_items_aggregate_duplicates_and_preserve_uncertainty(self):
        rows = [
            {"name": "Chroma Laser", "quantity": 2},
            {"name": "chroma laser", "quantity": 3},
            {"name": "Chroma Gemstone", "quantity": None, "quantity_source": "uncertain"},
        ]
        self.assertEqual(
            identity_items(rows, normalize),
            [("chroma gemstone", None), ("chroma laser", 5)],
        )

    def test_source_ad_id_is_stable_when_item_contents_change(self):
        first = [{"name": "Knife A", "quantity": 1}]
        second = [{"name": "Knife A", "quantity": 4}]
        url = "https://bloxswaps.com/fr/trade-mm2?ad=post-123&page=1"
        first_id = stable_listing_id(
            "https://bloxswaps.com/fr/users/1", first, [], url,
            normalize_name=normalize,
        )
        second_id = stable_listing_id(
            "https://bloxswaps.com/fr/users/1", second, [], url,
            normalize_name=normalize,
        )
        self.assertEqual(first_id, second_id)

    def test_legacy_identity_keeps_item_quantities_for_state_migration(self):
        rows_a = [{"name": "Knife A", "quantity": 1}]
        rows_b = [{"name": "Knife A", "quantity": 2}]
        url = "https://bloxswaps.com/fr/trade-mm2?sort=latest"
        first = legacy_stable_listing_id(
            "https://bloxswaps.com/fr/users/1", rows_a, [], url,
            normalize_name=normalize,
        )
        second = legacy_stable_listing_id(
            "https://bloxswaps.com/fr/users/1", rows_b, [], url,
            normalize_name=normalize,
        )
        self.assertNotEqual(first, second)

    def test_listing_identifier_reads_source_data_attributes(self):
        tree = PageTree()
        tree.feed('<article><div data-ad-id="ad-987"><img alt="Knife"></div></article>')
        self.assertEqual(listing_identifier(tree.root), "ad-987")

    def test_legacy_delivery_state_is_classified_for_one_time_migration(self):
        listing = {"id": "new-id", "legacy_id": "old-id"}
        self.assertEqual(legacy_alert_state(listing, {"old-id"}, set()), "alerted")
        self.assertEqual(legacy_alert_state(listing, set(), {"old-id"}), "uncertain")
        self.assertEqual(legacy_alert_state(listing, set(), set()), "")
        self.assertEqual(
            legacy_alert_state({"id": "same", "legacy_id": "same"}, {"same"}, set()),
            "",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
