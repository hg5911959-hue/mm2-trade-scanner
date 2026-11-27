from __future__ import annotations

from test_support import IsolatedDataTestCase
from tools.bloxswaps_api import api_post_is_single_sided, api_trade_post_to_listing


def strict_quantity(value):
    if isinstance(value, bool):
        raise ValueError("boolean quantity")
    parsed = int(value)
    if parsed != value:
        raise ValueError("fractional quantity")
    return parsed if parsed > 0 else None


class BloxSwapsApiAdapterTests(IsolatedDataTestCase):
    def test_single_sided_posts_are_non_actionable(self):
        self.assertTrue(api_post_is_single_sided({
            "tradePostItems": [{"tradeRole": "OFFERED"}],
        }))
        self.assertFalse(api_post_is_single_sided({
            "tradePostItems": [
                {"tradeRole": "OFFERED"},
                {"tradeRole": "REQUESTED"},
            ],
        }))
        self.assertFalse(api_post_is_single_sided({"tradePostItems": []}))

    def test_structured_post_maps_catalog_identity_and_exact_quantities(self):
        post = {
            "id": "post-117",
            "user": {
                "robloxUserId": "123",
                "robloxUsername": "trader",
                "robloxDisplayName": "Trader",
            },
            "tradePostItems": [
                {"itemDetailsId": "candy-id", "quantity": 117, "tradeRole": "OFFERED"},
                {"itemDetailsId": "knife-id", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": [],
            "lastRefreshedAt": "2026-11-27T00:00:00Z",
        }
        item_map = {
            "candy-id": {
                "displayName": "Candy", "slug": "candy",
                "rarity": "COMMON", "type": "Knife", "chroma": False,
            },
            "knife-id": {
                "displayName": "Knife", "slug": "knife",
                "rarity": "GODLY", "type": "Knife", "chroma": False,
            },
        }
        listing = api_trade_post_to_listing(
            post,
            item_map,
            strict_quantity=strict_quantity,
            max_item_quantity=9999,
            stable_listing_id=lambda *args: "stable-id",
            legacy_stable_listing_id=lambda *args: "legacy-id",
        )
        self.assertEqual(listing["id"], "stable-id")
        self.assertEqual(listing["legacy_id"], "legacy-id")
        self.assertEqual(listing["receive_items"][0]["quantity"], 117)
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "structured")
        self.assertEqual(listing["receive_items"][0]["catalog_slug"], "candy")
        self.assertEqual(listing["give_items"][0]["quantity"], 1)

    def test_invalid_structured_quantity_remains_uncertain(self):
        post = {
            "id": "post-invalid",
            "tradePostItems": [
                {"itemDetailsId": "a", "quantity": 1.7, "tradeRole": "OFFERED"},
                {"itemDetailsId": "b", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
        }
        listing = api_trade_post_to_listing(
            post,
            {"a": {"displayName": "Candy"}, "b": {"displayName": "Knife"}},
            strict_quantity=strict_quantity,
            max_item_quantity=9999,
            stable_listing_id=lambda *args: "stable",
            legacy_stable_listing_id=lambda *args: "legacy",
        )
        self.assertIsNone(listing["receive_items"][0]["quantity"])
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "uncertain")
