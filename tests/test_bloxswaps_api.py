from __future__ import annotations

from unittest.mock import patch

from test_support import IsolatedDataTestCase
import scanner
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
            stable_listing_id=lambda *args: "stable",
            legacy_stable_listing_id=lambda *args: "legacy",
        )
        self.assertIsNone(listing["receive_items"][0]["quantity"])
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "uncertain")

    def test_unidentified_api_feed_records_are_counted_as_malformed_candidates(self):
        recent = "2026-11-27T17:00:00Z"
        payload = {
            "items": [
                {"tradePostItems": [], "lastRefreshedAt": recent},
                {
                    "id": "valid-post",
                    "user": {"robloxUserId": "123", "robloxUsername": "trader"},
                    "tradePostItems": [
                        {"itemDetailsId": "give-id", "quantity": 1, "tradeRole": "OFFERED"},
                        {"itemDetailsId": "receive-id", "quantity": 1, "tradeRole": "REQUESTED"},
                    ],
                    "requestedWildcards": [],
                    "lastRefreshedAt": recent,
                },
            ],
            "meta": {"cursor": None},
        }
        listing = {"id": "stable-valid-post", "source_ad_id": "valid-post"}
        item_map = {
            "give-id": {"displayName": "Give", "slug": "give", "type": "Knife"},
            "receive-id": {"displayName": "Receive", "slug": "receive", "type": "Knife"},
        }

        with (
            patch.dict("os.environ", {"BLOXSWAPS_API_ONLY": "true"}),
            patch("scanner.bloxswaps_api_get_json", return_value=payload),
            patch("scanner.fetch_bloxswaps_items_map", return_value=item_map),
            patch("scanner._last_successful_scan_timestamp", return_value=None),
            patch("scanner._api_trade_post_to_listing", return_value=listing),
        ):
            listings, stats, _ = scanner.fetch_bloxswaps_all(
                seen_ad_ids=set(), pending_ad_ids=[], watch_ad_ids=[]
            )

        self.assertEqual([row["id"] for row in listings], ["stable-valid-post"])
        self.assertEqual(stats["candidate_count"], 2)
        self.assertEqual(stats["parsed_candidate_count"], 1)
        self.assertEqual(stats["malformed_candidate_count"], 1)
        self.assertEqual(stats["unidentified_feed_record_count"], 1)
        self.assertTrue(stats["pagination_complete"])

    def test_api_adapter_preserves_a_single_string_wildcard_as_one_token(self):
        post = {
            "id": "post-wildcard-string",
            "user": {"robloxUserId": "123", "robloxUsername": "trader"},
            "tradePostItems": [
                {"itemDetailsId": "a", "quantity": 1, "tradeRole": "OFFERED"},
                {"itemDetailsId": "b", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": "Offres",
        }
        item_map = {
            "a": {"displayName": "A", "slug": "a", "rarity": "GODLY", "type": "Knife"},
            "b": {"displayName": "B", "slug": "b", "rarity": "GODLY", "type": "Knife"},
        }
        listing = api_trade_post_to_listing(
            post, item_map, strict_quantity=strict_quantity,
            stable_listing_id=lambda *args: "stable",
            legacy_stable_listing_id=lambda *args: "legacy",
        )
        self.assertEqual(listing["requested_wildcards"], ["Offres"])
