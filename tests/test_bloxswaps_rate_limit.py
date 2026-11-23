import json
import time
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
from urllib.request import Request

import scanner
from test_support import IsolatedDataTestCase


class BloxSwapsRateLimitTests(IsolatedDataTestCase):
    def test_http_429_stops_without_retries_and_reports_retry_after(self):
        calls = []

        def rate_limited(req, timeout):
            calls.append(req)
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                {
                    "Retry-After": "37",
                    "Server": "cloudflare",
                    "CF-Ray": "test-ray",
                    "Content-Type": "text/html",
                },
                BytesIO(b"rate limited"),
            )

        request = Request("https://bloxswaps.com/trade-mm2")
        with (
            patch("scanner.urllib.request.urlopen", side_effect=rate_limited),
            patch("scanner.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError,
                r"HTTP 429.*Retry-After=37s.*Server=cloudflare.*CF-Ray=test-ray",
            ):
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_rate_limit_stops_the_polling_window_without_another_cycle(self):
        failure = scanner.BloxSwapsRateLimitError("BloxSwaps page: HTTP 429 rate limited")
        with (
            patch.object(scanner, "scan_once", side_effect=failure) as scan,
            patch.object(scanner, "should_start_scan_cycle", return_value=True),
            patch("scanner.time.monotonic", return_value=0.0),
            patch("scanner.time.sleep") as sleep,
            patch.object(scanner.rblxvalue_guard, "log_request_count"),
            patch.object(scanner, "update_metrics"),
            patch.object(scanner, "record_failure") as record_failure,
        ):
            with self.assertRaises(scanner.BloxSwapsRateLimitError):
                scanner.run_scan_loop(duration_seconds=120, interval_seconds=30)

        self.assertEqual(scan.call_count, 1)
        sleep.assert_not_called()
        record_failure.assert_not_called()

    def test_http_429_without_retry_after_fails_closed_immediately(self):
        calls = []

        def rate_limited(req, timeout):
            calls.append(req)
            raise HTTPError(
                req.full_url, 429, "too many requests", {}, BytesIO(b"rate limited")
            )

        request = Request("https://bloxswaps.com/trade-mm2")
        with patch("scanner.urllib.request.urlopen", side_effect=rate_limited):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError, "no Retry-After header"
            ):
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertEqual(len(calls), 1)

    def test_http_429_with_invalid_retry_after_fails_closed_without_leaking_body(self):
        def rate_limited(req, timeout):
            raise HTTPError(
                req.full_url,
                429,
                "too many requests",
                {"Retry-After": "not-a-date", "Server": "cloudflare"},
                BytesIO(b"private response body"),
            )

        request = Request("https://bloxswaps.com/trade-mm2")
        with patch("scanner.urllib.request.urlopen", side_effect=rate_limited):
            with self.assertRaisesRegex(
                scanner.BloxSwapsRateLimitError, "invalid Retry-After header"
            ) as raised:
                scanner.request_with_retry(request, 10, "BloxSwaps page")

        self.assertNotIn("private response body", str(raised.exception))


    def test_item_catalogue_cache_discards_prices_and_keeps_identity_only(self):
        path = Path("data/bloxswaps_items.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        item_id = "identity-item-1"
        raw_item = {
            "id": item_id, "slug": "laser-k-2026", "displayName": "Laser",
            "chroma": False, "rarity": "GODLY", "type": "Knife",
            "tradable": True, "year": 2026, "currentPrices": {"sellPrice": "9.99"},
            "manualPrice": False, "appAvailableQuantity": 3, "image": "https://cdn.example/item.webp",
        }
        path.write_text(json.dumps({
            "fetched_at": time.time(), "items": {item_id: raw_item},
        }), encoding="utf-8")
        with patch.object(scanner, "BLOXSWAPS_ITEMS_FILE", path):
            result = scanner.fetch_bloxswaps_items_map()
        self.assertEqual(result[item_id]["displayName"], "Laser")
        self.assertTrue(result[item_id]["tradable"])
        self.assertNotIn("currentPrices", result[item_id])
        self.assertNotIn("manualPrice", result[item_id])
        self.assertNotIn("image", result[item_id])
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("currentPrices", persisted["items"][item_id])


    def test_api_only_mode_uses_public_trade_posts_endpoint_without_html_request(self):
        post = {
            "id": "trade-api-001",
            "user": {
                "robloxUserId": "123456",
                "robloxUsername": "trader",
                "robloxDisplayName": "Trader",
            },
            "lastRefreshedAt": datetime.now(timezone.utc).isoformat(),
            "tradePostItems": [
                {"itemDetailsId": "item-offered", "quantity": 2, "tradeRole": "OFFERED"},
                {"itemDetailsId": "item-requested", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": [],
        }
        item_map = {
            "item-offered": {
                "displayName": "Offered Item", "slug": "offered",
                "rarity": "GODLY", "type": "Knife",
            },
            "item-requested": {
                "displayName": "Requested Item", "slug": "requested",
                "rarity": "GODLY", "type": "Knife",
            },
        }
        with (
            patch.dict("os.environ", {"BLOXSWAPS_API_ONLY": "true"}),
            patch.object(scanner, "fetch_bloxswaps") as html_fetch,
            patch.object(
                scanner, "bloxswaps_api_get_json",
                return_value={"items": [post], "meta": {"cursor": None}},
            ) as api,
            patch.object(scanner, "fetch_bloxswaps_items_map", return_value=item_map),
        ):
            listings, stats, discovered = scanner.fetch_bloxswaps_all(set())

        html_fetch.assert_not_called()
        api.assert_called_once_with("/trade-posts", {"pageSize": scanner.BLOXSWAPS_PAGE_SIZE})
        self.assertEqual(discovered, ["trade-api-001"])
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["receive_items"][0]["name"], "Offered Item")
        self.assertEqual(listings[0]["give_items"][0]["name"], "Requested Item")
        self.assertEqual(listings[0]["receive_items"][0]["quantity"], 2)
        self.assertEqual(stats["pagination_source"], "BloxSwaps API only")
        self.assertTrue(stats["pagination_complete"])
        self.assertFalse(listings[0].get("html_card_parse_warning", False))
        self.assertIn("https://bloxswaps.com/trade-mm2?", listings[0]["url"])


if __name__ == "__main__":
    unittest.main()
