import json
import tempfile
from contextlib import ExitStack
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase

import telegram_market


NOW = 1_800_000_000.0


class TelegramMarketTests(IsolatedDataTestCase):
    def setUp(self):
        super().setUp()
        previous_manual_flag = telegram_market._MANUAL_IMPORTS_INGESTED
        telegram_market._MANUAL_IMPORTS_INGESTED = False
        self.addCleanup(setattr, telegram_market, "_MANUAL_IMPORTS_INGESTED", previous_manual_flag)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.offers = root / "cash_market_offers.json"
        self.inventory = root / "inventory.json"
        self.offset = root / "telegram_update_state.json"
        self.last_run = root / "telegram_last_run.json"
        self.history = root / "market_book_history.json"

    def _paths(self):
        stack = ExitStack()
        stack.enter_context(patch("telegram_market.OFFERS_FILE", self.offers))
        stack.enter_context(patch("telegram_market.INVENTORY_FILE", self.inventory))
        stack.enter_context(patch("telegram_market.UPDATES_FILE", self.offset))
        stack.enter_context(patch("telegram_market.LAST_RUN_FILE", self.last_run))
        stack.enter_context(patch("telegram_market.HISTORY_FILE", self.history))
        return stack

    def test_public_storage_is_blocked_without_explicit_confirmation(self):
        called = []
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=True,
                get_updates=lambda token, offset: called.append("poll") or [],
                send_message=lambda *args: called.append("send"),
                now=1_800_000_000,
            )
        self.assertEqual(count, 0)
        self.assertEqual(called, [])
        self.assertFalse(self.offset.exists())

    def test_unauthorized_chat_is_ignored_and_offset_is_advanced(self):
        sent = []
        updates = [{"update_id": 10, "message": {
            "message_id": 1, "chat": {"id": 999}, "text": "/help"
        }}]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=True, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda *args: sent.append(args), now=1_800_000_000,
            )
            saved = json.loads(self.offset.read_text(encoding="utf-8"))
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])
        self.assertEqual(saved["offset"], 11)

    def test_authorized_commands_receive_reply_and_persist_offset(self):
        sent = []
        updates = [{"update_id": 20, "message": {
            "message_id": 2, "chat": {"id": 123}, "text": "/help"
        }}]
        with self._paths(), patch("telegram_market._load_market", return_value={
            "book": {}, "direct": [], "chains": [], "game_values": {},
            "cache_age": None, "previous": {}, "offers_count": 0,
        }):
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
        self.assertEqual(count, 1)
        self.assertTrue(any("/p <item>" in message for message in sent))
        self.assertEqual(json.loads(self.offset.read_text(encoding="utf-8"))["offset"], 21)

    def test_forwarded_discord_post_is_parsed_and_stored_privately(self):
        sent = []
        updates = [{"update_id": 30, "message": {
            "message_id": 77, "date": 1_800_000_000, "chat": {"id": 123},
            "forward_origin": {"type": "user", "date": 1_799_999_000,
                               "sender_user": {"username": "market-seller"}},
            "text": "QUICK SELL\n1x Candy 2$/1\n2x Sweet 3$/1\nhttps://discord.com/channels/1/2/1593536046694400000",
        }}]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
            stored = json.loads(self.offers.read_text(encoding="utf-8"))
        self.assertEqual(count, 1)
        self.assertEqual(len(stored["offers"]), 2)
        self.assertEqual(stored["offers"][0]["side"], "sell")
        self.assertEqual(stored["offers"][0]["author"], "market-seller")
        self.assertEqual(stored["offers"][0]["posted_at"], "2027-01-15T07:43:20+00:00")
        self.assertIn("تم تحليل 2 عرضاً", sent[-1])
        self.assertFalse(self.history.exists())

    def test_repeated_forward_of_same_discord_permalink_does_not_duplicate_liquidity(self):
        sent = []
        permalink = "https://discord.com/channels/1/2/1593540240998400000"
        updates = []
        for update_id, message_id in ((32, 80), (33, 81)):
            updates.append({"update_id": update_id, "message": {
                "message_id": message_id, "date": 1_800_000_000, "chat": {"id": 123},
                "text": f"QUICK SELL\n1x Candy 2$/1\n2x Sweet 3$/1\n{permalink}",
            }})
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True,
                inventory_enabled=False, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
            stored = json.loads(self.offers.read_text(encoding="utf-8"))
        self.assertEqual(count, 2)
        self.assertEqual(len(stored["offers"]), 2)

    def test_pasted_offer_without_discord_timestamp_is_rejected(self):
        sent = []
        updates = [{"update_id": 31, "message": {
            "message_id": 78, "date": 1_800_000_000, "chat": {"id": 123},
            "text": "Selling\n1x Candy 2$/1",
        }}]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True,
                inventory_enabled=False, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
            stored = json.loads(self.offers.read_text(encoding="utf-8"))
        self.assertEqual(count, 1)
        self.assertEqual(stored["offers"], [])
        self.assertIn("timestamp_unknown", sent[-1])

    def test_queue_notice_is_sent_after_more_than_twelve_minutes(self):
        sent = []
        self.last_run.write_text(json.dumps({"started_at_epoch": 1_799_999_000}), encoding="utf-8")
        updates = [{"update_id": 40, "message": {
            "message_id": 3, "chat": {"id": 123}, "text": "/help"
        }}]
        with self._paths(), patch("telegram_market._load_market", return_value={
            "book": {}, "direct": [], "chains": [], "game_values": {},
            "cache_age": None, "previous": {}, "offers_count": 0,
        }):
            telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
        self.assertIn("قائمة الانتظار", sent[0])

    def test_csv_catalogue_is_only_a_dry_run_reference_fallback(self):
        catalogue = {
            "items": {
                "candy": {"name": "Candy", "cash_price_usd": 2.5,
                          "status": "unverified", "verified_at_utc": ""},
            }
        }
        with self._paths(), patch("telegram_market._load_offers", return_value=([], [])), \
             patch("tools.cash_market.load_cash_price_catalog", return_value=catalogue) as csv_loader, \
             patch.dict("os.environ", {"DRY_RUN": "true"}):
            snapshot = telegram_market._load_market(now=NOW)
        csv_loader.assert_called_once()
        self.assertEqual(snapshot["book"]["candy"]["median_market"], 2.5)
        self.assertIn("dry_run_csv_fallback", snapshot["book"]["candy"]["risk_flags"])
        self.assertIn("unverified_csv_estimate", snapshot["book"]["candy"]["risk_flags"])
        self.assertEqual(snapshot["direct"], [])
        self.assertEqual(snapshot["chains"], [])

    def test_live_market_never_uses_the_legacy_csv_fallback(self):
        with self._paths(), patch("telegram_market._load_offers", return_value=([], [])), \
             patch("tools.cash_market.load_cash_price_catalog") as csv_loader, \
             patch.dict("os.environ", {"DRY_RUN": "false"}):
            snapshot = telegram_market._load_market(now=NOW)
        csv_loader.assert_not_called()
        self.assertEqual(snapshot["book"], {})

    def test_market_price_command_reports_acceptance_values(self):
        snapshot = {
            "book": {"celestial set": {
                "item": "Celestial Set",
                "best_ask": {"unit_price_usd": 71},
                "best_bid": {"unit_price_usd": 63},
                "median_market": 67,
                "spread_percent": 11.94,
                "ask_quantity": 2, "bid_quantity": 2,
                "data_age_seconds": 60, "confidence": "medium",
                "risk_flags": [],
            }},
            "game_values": {}, "direct": [], "chains": [],
            "cache_age": None, "previous": {}, "offers_count": 4,
        }
        reply = telegram_market._command_reply("/p celestial set", snapshot,
                                               inventory_enabled=False)
        self.assertIn("أفضل بيع: $71.00", reply)
        self.assertIn("أفضل شراء: $63.00", reply)

    def test_authorized_manual_discord_imports_are_normalized_and_not_rawly_persisted(self):
        source = Path(self.temp.name) / "discord_messages.json"
        source.write_text(json.dumps({"messages": [{
            "message_id": "123456", "author": "discord-seller",
            "url": "https://discord.com/channels/1/2/123456",
            "created_at_utc": "2026-11-19T10:00:00+00:00",
            "content": "Selling\n1x Candy 2$/1",
        }]}), encoding="utf-8")
        with self._paths():
            telegram_market._MANUAL_IMPORTS_INGESTED = False
            first = telegram_market.ingest_manual_discord_imports(source)
            second = telegram_market.ingest_manual_discord_imports(source)
            stored = json.loads(self.offers.read_text(encoding="utf-8"))
        self.assertEqual(first, (1, 0))
        self.assertEqual(second, (0, 0))
        self.assertEqual(len(stored["offers"]), 1)
        self.assertEqual(stored["offers"][0]["message_id"], "discord:123456")
        self.assertEqual(stored["offers"][0]["side"], "sell")
        self.assertNotIn("Selling", self.offers.read_text(encoding="utf-8"))
        self.assertNotIn("source_text", stored["offers"][0])

    def test_inventory_add_and_remove_are_idempotent_for_replayed_update_ids(self):
        snapshot = {"book": {}, "previous": {}, "chains": [], "game_values": {}}
        with self._paths():
            first_add = telegram_market._command_reply(
                "/add Candy 2", snapshot, inventory_enabled=True, update_id=55
            )
            replay_add = telegram_market._command_reply(
                "/add Candy 2", snapshot, inventory_enabled=True, update_id=55
            )
            first_remove = telegram_market._command_reply(
                "/remove Candy 1", snapshot, inventory_enabled=True, update_id=56
            )
            replay_remove = telegram_market._command_reply(
                "/remove Candy 1", snapshot, inventory_enabled=True, update_id=56
            )
            stored = json.loads(self.inventory.read_text(encoding="utf-8"))
        self.assertIn("×2", first_add)
        self.assertIn("مسبقاً", replay_add)
        self.assertIn("مسبقاً", replay_remove)
        self.assertIn("تم تحديث مخزون", first_remove)
        self.assertEqual(stored["items"]["candy"]["quantity"], 1)
        self.assertEqual(stored["processed_update_ids"], [55, 56])

    def test_market_alerts_deduplicate_until_profit_changes_over_threshold(self):
        sent = []
        snapshot = {
            "direct": [{
                "type": "direct_arbitrage", "item_key": "candy", "item": "Candy",
                "buy": {"message_id": "private-buy-id", "unit_price_usd": 2, "url": "https://example.com/buy"},
                "sell": {"message_id": "private-sell-id", "unit_price_usd": 4, "url": "https://example.com/sell"},
                "quantity": 2, "profit_usd": 2.0, "profit_percent": 30.0,
            }],
            "chains": [],
        }
        with patch("telegram_market.MARKET_ALERTS_FILE", self.history):
            first = telegram_market.send_market_alerts(
                token="token", chat_id="123", snapshot=snapshot,
                send_message=lambda token, chat, text: sent.append(text), now=NOW,
            )
            snapshot["direct"][0]["buy"]["message_id"] = "new-telegram-forward-buy"
            snapshot["direct"][0]["sell"]["message_id"] = "new-telegram-forward-sell"
            second = telegram_market.send_market_alerts(
                token="token", chat_id="123", snapshot=snapshot,
                send_message=lambda token, chat, text: sent.append(text), now=NOW + 60,
            )
            snapshot["direct"][0]["profit_usd"] = 2.6
            third = telegram_market.send_market_alerts(
                token="token", chat_id="123", snapshot=snapshot,
                send_message=lambda token, chat, text: sent.append(text), now=NOW + 120,
            )
        self.assertEqual((first, second, third), (1, 0, 1))
        self.assertEqual(len(sent), 2)
        state = json.loads(self.history.read_text(encoding="utf-8"))
        self.assertNotIn("private-buy-id", self.history.read_text(encoding="utf-8"))
        self.assertEqual(len(state["opportunities"]), 1)

    def test_market_alert_limit_and_empty_credentials_fail_closed(self):
        sent = []
        rows = []
        for index in range(3):
            rows.append({
                "type": "direct_arbitrage", "item_key": f"item-{index}", "item": f"Item {index}",
                "buy": {"message_id": f"buy-{index}", "unit_price_usd": 1},
                "sell": {"message_id": f"sell-{index}", "unit_price_usd": 3},
                "quantity": 1, "profit_usd": 1 + index, "profit_percent": 50,
            })
        snapshot = {"direct": rows, "chains": []}
        with patch("telegram_market.MARKET_ALERTS_FILE", self.history):
            self.assertEqual(telegram_market.send_market_alerts(
                token="", chat_id="123", snapshot=snapshot,
                send_message=lambda *args: sent.append(args), now=NOW,
            ), 0)
            self.assertEqual(telegram_market.send_market_alerts(
                token="token", chat_id="123", snapshot=snapshot, max_alerts=1,
                send_message=lambda token, chat, text: sent.append(text), now=NOW,
            ), 1)
        self.assertEqual(len(sent), 1)

    def test_inventory_commands_are_disabled_by_default(self):
        snapshot = {"book": {}, "direct": [], "chains": [], "game_values": {},
                    "cache_age": None, "previous": {}, "offers_count": 0}
        reply = telegram_market._command_reply("/add Candy 2", snapshot,
                                               inventory_enabled=False)
        self.assertIn("معطّلة", reply)
        self.assertFalse(self.inventory.exists())

    def test_get_updates_never_accepts_a_different_chat_via_command_path(self):
        self.assertEqual(telegram_market._message_text({"caption": "Selling"}), "Selling")
        self.assertEqual(telegram_market._forward_author({
            "forward_origin": {"sender_chat": {"title": "Seller Server"}}
        }), "Seller Server")


if __name__ == "__main__":
    unittest.main()
