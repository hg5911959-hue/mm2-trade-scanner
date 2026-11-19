import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase

import telegram_market


class TelegramMarketTests(IsolatedDataTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.offers = root / "cash_market_offers.json"
        self.inventory = root / "inventory.json"
        self.offset = root / "telegram_update_state.json"
        self.last_run = root / "telegram_last_run.json"
        self.history = root / "market_book_history.json"

    def _paths(self):
        return (
            patch("telegram_market.OFFERS_FILE", self.offers),
            patch("telegram_market.INVENTORY_FILE", self.inventory),
            patch("telegram_market.UPDATES_FILE", self.offset),
            patch("telegram_market.LAST_RUN_FILE", self.last_run),
            patch("telegram_market.HISTORY_FILE", self.history),
        )

    def test_unauthorized_chat_is_ignored_and_offset_is_advanced(self):
        sent = []
        updates = [{"update_id": 10, "message": {
            "message_id": 1, "chat": {"id": 999}, "text": "/help"
        }}]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=True,
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
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False,
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
            "text": "QUICK SELL\n1x Candy 2$/1\n2x Sweet 3$/1",
        }}]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
            stored = json.loads(self.offers.read_text(encoding="utf-8"))
        self.assertEqual(count, 1)
        self.assertEqual(len(stored["offers"]), 2)
        self.assertEqual(stored["offers"][0]["side"], "sell")
        self.assertEqual(stored["offers"][0]["author"], "market-seller")
        self.assertEqual(stored["offers"][0]["posted_at"], "2026-12-09T15:43:20+00:00")
        self.assertIn("تم تحليل 2 عرضاً", sent[-1])
        self.assertFalse(self.history.exists())

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
                token="token", chat_id="123", market_enabled=True, inventory_enabled=False,
                get_updates=lambda token, offset: updates,
                send_message=lambda token, chat, text: sent.append(text),
                now=1_800_000_000,
            )
        self.assertIn("قائمة الانتظار", sent[0])

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
