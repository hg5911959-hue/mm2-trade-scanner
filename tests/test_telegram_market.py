import json
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase

import telegram_market
from market_book import build_order_book


NOW = 1_800_000_000.0


class TelegramMarketTests(IsolatedDataTestCase):
    def test_telegram_redirects_are_rejected_before_token_forwarding(self):
        handler = telegram_market._RejectRedirectHandler()
        request = telegram_market.urllib.request.Request(
            "https://api.telegram.org/botplaceholder/getUpdates"
        )
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://attacker.example/collect"
        )
        self.assertIsNone(redirected)

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

    def test_group_chat_and_other_private_sender_are_rejected(self):
        sent = []
        updates = [
            {"update_id": 11, "message": {
                "message_id": 10, "chat": {"id": -100123, "type": "supergroup"},
                "from": {"id": 123}, "text": "/help",
            }},
            {"update_id": 12, "message": {
                "message_id": 11, "chat": {"id": 123, "type": "private"},
                "from": {"id": 456}, "text": "/help",
            }},
        ]
        with self._paths():
            count = telegram_market.process_telegram_updates(
                token="token", chat_id="123", market_enabled=True,
                inventory_enabled=True, private_state_confirmed=True,
                get_updates=lambda token, offset: updates,
                send_message=lambda *args: sent.append(args),
                now=1_800_000_000,
            )
            saved = json.loads(self.offset.read_text(encoding="utf-8"))
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])
        self.assertEqual(saved["offset"], 13)

    def test_authorized_commands_receive_reply_and_persist_offset(self):
        sent = []
        updates = [{"update_id": 20, "message": {
            "message_id": 2, "chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": "/help"
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
            "message_id": 77, "date": 1_800_000_000, "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
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
                "message_id": message_id, "date": 1_800_000_000, "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
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

    def test_pasted_discord_post_accepts_explicit_author_and_flags_unverified_identity(self):
        sent = []
        updates = [{"update_id": 34, "message": {
            "message_id": 82, "date": 1_800_000_000,
            "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
            "text": "Author: seller-alpha\nSELLING MM2\nCELESTIAL SET - 72$\n"
                    "https://discord.com/channels/1125462206151143464/1572655277130514472/1573076681072189520",
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
        self.assertEqual(len(stored["offers"]), 1)
        self.assertEqual(stored["offers"][0]["author"], "seller-alpha")
        self.assertIn("owner_supplied_author", stored["offers"][0]["flags"])
        self.assertEqual(stored["offers"][0]["posted_at"], "2026-11-19T20:45:07.119000+00:00")
        self.assertIn("تم تحليل 1 عرضاً", sent[-1])

    def test_discord_snowflake_timestamp_is_decoded_from_message_permalink(self):
        self.assertEqual(
            telegram_market._discord_posted_at_from_link(
                "https://discord.com/channels/1125462206151143464/1572655277130514472/1573076681072189520"
            ),
            "2026-11-19T20:45:07.119000+00:00",
        )

    def test_pasted_offer_without_discord_timestamp_is_rejected(self):
        sent = []
        updates = [{"update_id": 31, "message": {
            "message_id": 78, "date": 1_800_000_000, "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
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
            "message_id": 3, "chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": "/help"
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

    def test_stale_game_values_disable_chains_but_keep_direct_cash_signals(self):
        stamp = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()
        offers = [
            {"side": "sell", "item": "Candy", "item_key": "candy", "unit_price_usd": 1.0,
             "quantity": 2, "min_quantity": 1, "author": "seller-a", "message_id": "s1",
             "url": "https://discord.com/channels/1/2/101", "posted_at": stamp, "flags": []},
            {"side": "sell", "item": "Candy", "item_key": "candy", "unit_price_usd": 1.2,
             "quantity": 2, "min_quantity": 1, "author": "seller-b", "message_id": "s2",
             "url": "https://discord.com/channels/1/2/102", "posted_at": stamp, "flags": []},
            {"side": "buy", "item": "Candy", "item_key": "candy", "unit_price_usd": 4.0,
             "quantity": 2, "min_quantity": 1, "author": "buyer-a", "message_id": "b1",
             "url": "https://discord.com/channels/1/2/103", "posted_at": stamp, "flags": []},
            {"side": "buy", "item": "Candy", "item_key": "candy", "unit_price_usd": 3.8,
             "quantity": 2, "min_quantity": 1, "author": "buyer-b", "message_id": "b2",
             "url": "https://discord.com/channels/1/2/104", "posted_at": stamp, "flags": []},
        ]
        stale_cache = {"schema_version": 4, "fetched_at": NOW - 25 * 3600, "values": {}}
        with self._paths():
            with patch("telegram_market._load_offers", return_value=(offers, [])):
                with patch("scanner._read_values_cache_document", return_value=stale_cache):
                    snapshot = telegram_market._load_market(now=NOW)
        self.assertEqual(snapshot["game_values_status"], "stale")
        self.assertEqual(snapshot["game_values"], {})
        self.assertEqual(snapshot["chains"], [])
        self.assertEqual(len(snapshot["direct"]), 1)

    def test_appendix_acceptance_price_command_shows_celestial_71_ask_and_63_bid(self):
        key = telegram_market.normalize_key("Celestial Set")
        offers = [
            {"side": "sell", "item": "Celestial Set", "item_key": key,
             "unit_price_usd": 71.0, "quantity": 2, "min_quantity": 1,
             "author": "seller-a", "message_id": "sample-sell-1",
             "url": "https://discord.com/channels/1/2/101", "posted_at": NOW - 60, "flags": []},
            {"side": "sell", "item": "Celestial Set", "item_key": key,
             "unit_price_usd": 73.0, "quantity": 1, "min_quantity": 1,
             "author": "seller-b", "message_id": "sample-sell-2",
             "url": "https://discord.com/channels/1/2/102", "posted_at": NOW - 60, "flags": []},
            {"side": "buy", "item": "Celestial Set", "item_key": key,
             "unit_price_usd": 63.0, "quantity": 2, "min_quantity": 1,
             "author": "buyer-a", "message_id": "sample-buy-1",
             "url": "https://discord.com/channels/1/2/103", "posted_at": NOW - 60, "flags": []},
            {"side": "buy", "item": "Celestial Set", "item_key": key,
             "unit_price_usd": 61.0, "quantity": 1, "min_quantity": 1,
             "author": "buyer-b", "message_id": "sample-buy-2",
             "url": "https://discord.com/channels/1/2/104", "posted_at": NOW - 60, "flags": []},
        ]
        snapshot = {
            "book": build_order_book(offers, now=NOW),
            "game_values": {}, "game_values_status": "fresh",
        }
        reply = telegram_market._command_reply(
            "/p celestial set", snapshot, inventory_enabled=False
        )
        self.assertIn("أفضل بيع: $71.00", reply)
        self.assertIn("أفضل شراء: $63.00", reply)

    def test_demand_hints_are_displayed_without_becoming_a_bid_or_signal(self):
        hint = {
            "item": "Candy", "item_key": telegram_market.normalize_key("Candy"),
            "quantity": 684, "author": "buyer", "message_id": "hint-1",
            "url": "https://discord.com/channels/1/2/100", "posted_at": "",
        }
        with self._paths():
            with patch("telegram_market._load_offers", return_value=([], [hint])):
                with patch("scanner._read_values_cache_document", return_value=None):
                    snapshot = telegram_market._load_market(now=NOW)
        reply = telegram_market._command_reply("/p candy", snapshot, inventory_enabled=False)
        self.assertEqual(snapshot["book"], {})
        self.assertEqual(snapshot["direct"], [])
        self.assertEqual(snapshot["chains"], [])
        self.assertIn("طلب بلا سعر: x684", reply)
        self.assertNotIn("أفضل شراء: $", reply)

    def test_price_command_exposes_stale_missing_and_blocked_game_value_risks(self):
        market = {
            "best_ask": {"unit_price_usd": 2}, "best_bid": {"unit_price_usd": 1},
            "median_market": 1.5, "median_ask": 2, "median_bid": 1,
            "spread_percent": 66.7, "ask_quantity": 1, "bid_quantity": 1,
            "data_age_seconds": 60, "confidence": "medium", "risk_flags": [],
        }
        stale = telegram_market._format_item({"item": "Candy"}, market, None, "stale")
        missing = telegram_market._format_item(
            {"item": "Candy"}, market,
            {"value": 10, "stability": "", "data_age_seconds": 60}, "fresh",
        )
        blocked = telegram_market._format_item(
            {"item": "Candy"}, market,
            {"value": 10, "stability": "Untradeable", "data_age_seconds": 60}, "fresh",
        )
        self.assertIn("stale_game_values", stale)
        self.assertIn("missing_stability", missing)
        self.assertIn("untradeable", blocked)

    def test_buy_and_chain_commands_include_sources_and_risk_flags(self):
        snapshot = {
            "book": {}, "game_values": {}, "game_values_status": "fresh",
            "direct": [{
                "type": "direct_arbitrage", "item": "Candy", "item_key": "candy",
                "buy": {"unit_price_usd": 1, "url": "https://example.com/sell"},
                "sell": {"unit_price_usd": 4, "url": "https://example.com/buy"},
                "quantity": 1, "profit_usd": 2, "profit_percent": 50,
                "confidence": "high", "risk_flags": ["price_far_from_median"],
            }],
            "chains": [{
                "type": "swap_chain", "buy_item": "A", "swap_item": "B",
                "buy": {"unit_price_usd": 1}, "sell": {"unit_price_usd": 4},
                "quantity_buy": 2, "quantity_sell": 1, "profit_usd": 2,
                "profit_percent": 50, "confidence": "medium",
                "source_links": ["https://example.com/ask", "https://example.com/bid"],
                "risk_flags": ["owner_supplied_author"],
            }],
        }
        buy = telegram_market._command_reply("/buy", snapshot, inventory_enabled=False)
        chains = telegram_market._command_reply("/chains", snapshot, inventory_enabled=False)
        self.assertIn("https://example.com/sell", buy)
        self.assertIn("price_far_from_median", buy)
        self.assertIn("https://example.com/ask", chains)
        self.assertIn("owner_supplied_author", chains)

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

    def test_future_dated_rblxvalue_cache_disables_swap_value_resolution(self):
        offer = {
            "side": "sell", "item": "Candy", "item_key": "candy",
            "quantity": 1, "unit_price_usd": 1.0, "min_quantity": 1,
            "author": "seller", "posted_at": "2027-01-15T08:00:00+00:00",
        }
        future_cache = {
            "schema_version": 4, "fetched_at": NOW + 60,
            "values": {"candy": {"name": "Candy", "variants": [{
                "value": 10, "stability": "Stable",
            }]}},
        }
        with (
            self._paths(),
            patch("telegram_market._load_offers", return_value=([offer], [])),
            patch("scanner._read_values_cache_document", return_value=future_cache),
            patch("scanner.resolve_value_record") as resolve,
            patch.dict("os.environ", {"DRY_RUN": "false"}),
        ):
            snapshot = telegram_market._load_market(now=NOW)
        resolve.assert_not_called()
        self.assertIsNone(snapshot["cache_age"])
        self.assertEqual(snapshot["game_values"], {})
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
                "median_ask": 70, "median_bid": 64,
                "median_market": 67, "spread_percent": 11.94,
                "ask_quantity": 2, "bid_quantity": 2,
                "data_age_seconds": 60, "confidence": "medium",
                "risk_flags": [],
            }},
            "game_values": {"celestial set": {"value": 200, "data_age_seconds": 3600}},
            "direct": [], "chains": [],
            "cache_age": None, "previous": {}, "offers_count": 4,
        }
        reply = telegram_market._command_reply("/p celestial set", snapshot,
                                               inventory_enabled=False)
        self.assertIn("أفضل بيع: $71.00", reply)
        self.assertIn("أفضل شراء: $63.00", reply)
        self.assertIn("وسيط البيع: $70.00", reply)
        self.assertIn("وسيط الشراء: $64.00", reply)
        self.assertIn("عمر RBLXValue: 1 ساعة", reply)
        self.assertIn("$/Value: 0.3550", reply)

    def test_authorized_manual_discord_imports_are_normalized_and_not_rawly_persisted(self):
        source = Path(self.temp.name) / "discord_messages.json"
        source.write_text(json.dumps({"messages": [{
            "message_id": "123456", "author": "discord-seller",
            "guild_id": "1125462206151143464",
            "channel_id": "1572655277130514472", "channel_name": "cash-market",
            "url": "https://discord.com/channels/1/1572655277130514472/123456",
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
        self.assertEqual(stored["offers"][0]["source_type"], "discord_channel")
        self.assertEqual(stored["offers"][0]["source_channel_id"], "1572655277130514472")
        self.assertEqual(stored["offers"][0]["source_guild_id"], "1125462206151143464")
        self.assertEqual(stored["offers"][0]["source_channel_name"], "cash-market")
        self.assertEqual(stored["offers"][0]["url"], "https://discord.com/channels/1/1572655277130514472/123456")
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


    def test_missing_optional_csv_does_not_break_empty_discord_market_dry_run(self):
        with (
            self._paths(),
            patch("telegram_market._load_offers", return_value=([], [])),
            patch("tools.cash_market.load_cash_price_catalog", side_effect=FileNotFoundError("optional CSV missing")),
            patch.dict("os.environ", {"DRY_RUN": "true"}),
        ):
            snapshot = telegram_market._load_market(now=NOW)

        self.assertEqual(snapshot["book"], {})
        self.assertEqual(snapshot["direct"], [])
        self.assertEqual(snapshot["chains"], [])


if __name__ == "__main__":
    unittest.main()
