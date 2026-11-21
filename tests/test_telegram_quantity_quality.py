import unittest
import json
from io import BytesIO
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class ExtendedStackQuantityTests(IsolatedDataTestCase):
    def test_quantity_markers_above_one_hundred_are_supported(self):
        self.assertEqual(scanner._quantity_marker("x117"), 117)
        self.assertEqual(scanner.split_item_label("Candy x117"), ("Candy", 117))
        self.assertEqual(scanner._explicit_stack_quantity("117x"), 117)

    def test_api_trade_post_preserves_quantity_117(self):
        post = {
            "id": "post-117",
            "user": {"robloxUserId": "123", "robloxUsername": "trader"},
            "tradePostItems": [
                {"itemDetailsId": "candy-id", "quantity": 117, "tradeRole": "OFFERED"},
                {"itemDetailsId": "knife-id", "quantity": 1, "tradeRole": "REQUESTED"},
            ],
            "requestedWildcards": [],
        }
        item_map = {
            "candy-id": {"displayName": "Candy", "slug": "candy", "rarity": "COMMON", "type": "Knife"},
            "knife-id": {"displayName": "Knife", "slug": "knife", "rarity": "GODLY", "type": "Knife"},
        }
        listing = scanner._api_trade_post_to_listing(post, item_map)
        self.assertEqual(listing["receive_items"][0]["quantity"], 117)
        self.assertEqual(listing["receive_items"][0]["quantity_source"], "structured")

    def test_quantities_above_safety_cap_remain_untrusted(self):
        self.assertIsNone(scanner._quantity_marker("x10000"))
        self.assertEqual(scanner.MAX_ITEM_QUANTITY, 9999)


class TelegramFormattingTests(IsolatedDataTestCase):
    def test_trade_alert_is_html_formatted_and_shows_quantity_one(self):
        listing = {
            "owner_url": "https://bloxswaps.com/fr/users/123",
            "url": "https://bloxswaps.com/trade-mm2?ad=abc&name=trader",
        }
        message = scanner.format_trade_message(
            listing,
            290,
            ["• Harvester [ancient / gun] x1 — 290 each = 290 | Stability (RBLXValue): Stable | Demand: 4/10 | Rarity: 2/10"],
            400,
            ["• Xenoknife [godly / knife] x1 — 400 each = 400 | Stability (RBLXValue): Stable | Demand: 4/10 | Rarity: 3/10"],
            110,
        )
        self.assertIsInstance(message, scanner.TelegramHTML)
        self.assertIn("💎 MM2 TRADE <b>ALERT</b>", message)
        self.assertIn("<code>×1</code>", message)
        self.assertIn("Ancient · Gun", message)
        self.assertIn("Stability: Stable", message)
        self.assertIn('<a href="https://bloxswaps.com/trade-mm2?ad=abc&amp;name=trader">Open trade</a>', message)
        self.assertIn("PROFIT (game value): +110", message)

    def test_send_telegram_uses_html_parse_mode_only_for_trusted_html(self):
        payloads = []
        def fake_open(req, timeout):
            payloads.append(json.loads(req.data.decode("utf-8")))
            return BytesIO(b'{"ok": true}')
        with patch("scanner.BOT_TOKEN", "test-token"), patch("scanner.CHAT_ID", "123"), patch(
            "scanner.urllib.request.urlopen", side_effect=fake_open
        ), patch("scanner._wait_for_telegram_rate_limit"):
            scanner.send_telegram(scanner.TelegramHTML("<b>safe</b>"))
            scanner.send_telegram("plain <unsafe> text")
        self.assertEqual(payloads[0]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", payloads[1])
        self.assertTrue(payloads[1]["text"].startswith("plain <unsafe> text"))


if __name__ == "__main__":
    unittest.main()
