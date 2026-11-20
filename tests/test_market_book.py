import unittest
import json
import tempfile
from test_support import IsolatedDataTestCase
from pathlib import Path

from market_book import (
    build_order_book,
    find_direct_arbitrage,
    find_swap_chains,
    inventory_advice,
    save_private_price_history,
)


NOW = 1_800_000_000.0


def offer(side, item, price, quantity, author, message_id):
    return {
        "side": side, "item": item, "item_key": item.casefold(),
        "unit_price_usd": price, "quantity": quantity, "author": author,
        "message_id": message_id, "url": f"https://discord.com/channels/1/2/{message_id}",
        "posted_at": NOW - 300,
    }


class MarketBookTests(IsolatedDataTestCase):
    def test_order_book_and_direct_arbitrage_respect_quantity_fees_and_margin(self):
        offers = [
            offer("sell", "Candy", 5.0, 3, "seller-a", "s1"),
            offer("sell", "Candy", 6.0, 2, "seller-b", "s2"),
            offer("buy", "Candy", 8.0, 2, "buyer-a", "b1"),
            offer("buy", "Candy", 7.0, 5, "buyer-b", "b2"),
        ]
        book = build_order_book(offers, now=NOW)
        row = book["candy"]
        self.assertEqual(row["best_ask"]["unit_price_usd"], 5.0)
        self.assertEqual(row["best_bid"]["unit_price_usd"], 8.0)
        self.assertEqual(row["ask_quantity"], 5)
        self.assertEqual(row["bid_quantity"], 7)
        self.assertEqual(row["confidence"], "high")
        result = find_direct_arbitrage(book, transfer_fee_usd=0.5,
                                       safety_margin_percent=5,
                                       min_profit_usd=0.5, min_profit_percent=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["quantity"], 2)
        self.assertAlmostEqual(result[0]["profit_usd"], 4.7)

    def test_direct_arbitrage_requires_independent_author_confidence(self):
        ask = offer("sell", "Candy", 1, 5, "", "ask")
        bid = offer("buy", "Candy", 5, 5, "", "bid")
        ask["author"] = ""
        bid["author"] = ""
        book = build_order_book([ask, bid], now=NOW)
        self.assertEqual(book["candy"]["confidence"], "low")
        self.assertEqual(find_direct_arbitrage(book), [])

    def test_author_identity_is_case_insensitive_for_confidence(self):
        rows = [
            offer("sell", "Candy", 1.0, 2, "SellerName", "ask"),
            offer("buy", "Candy", 5.0, 2, " sellername ", "bid"),
        ]
        row = build_order_book(rows, now=NOW)["candy"]
        self.assertEqual(row["author_count"], 1)
        self.assertEqual(row["confidence"], "low")
        self.assertIn("single_author", row["risk_flags"])
        self.assertEqual(find_direct_arbitrage({"candy": row}), [])

    def test_no_arbitrage_when_best_bid_is_below_best_ask(self):
        book = build_order_book([
            offer("sell", "Celestial Set", 71, 2, "seller-a", "s1"),
            offer("sell", "Celestial Set", 73, 1, "seller-b", "s2"),
            offer("buy", "Celestial Set", 63, 2, "buyer-a", "b1"),
            offer("buy", "Celestial Set", 61, 1, "buyer-b", "b2"),
        ], now=NOW)
        self.assertEqual(find_direct_arbitrage(book), [])

    def test_stale_offers_are_retained_only_as_risk_metadata(self):
        stale = offer("sell", "Candy", 1, 1, "seller", "old")
        stale["posted_at"] = NOW - 72 * 3600
        book = build_order_book([stale], now=NOW)
        self.assertIn("candy", book)
        self.assertIsNone(book["candy"]["best_ask"])
        self.assertIn("stale_offer", book["candy"]["risk_flags"])
        self.assertEqual(book["candy"]["stale_offer_count"], 1)

    def test_stale_offers_are_excluded_from_prices_and_single_author_is_low_confidence(self):
        stale = offer("sell", "Candy", 1, 1, "seller", "old")
        stale["posted_at"] = NOW - 49 * 3600
        book = build_order_book([stale], now=NOW)
        self.assertIsNone(book["candy"]["best_ask"])
        self.assertIn("stale_offer", book["candy"]["risk_flags"])
        fresh = offer("sell", "Candy", 1, 1, "seller", "fresh")
        row = build_order_book([fresh], now=NOW)["candy"]
        self.assertEqual(row["confidence"], "low")
        self.assertIn("single_author", row["risk_flags"])

    def test_direct_arbitrage_respects_buyer_and_seller_minimum_quantities(self):
        ask = offer("sell", "Candy", 1, 10, "seller", "ask")
        bid = offer("buy", "Candy", 10, 5, "buyer", "bid")
        ask["min_quantity"] = 8
        book = build_order_book([ask, bid], now=NOW)
        self.assertEqual(find_direct_arbitrage(book), [])

    def test_swap_chain_propagates_market_risk_flags(self):
        offers = [
            offer("sell", "A", 2.0, 2, "seller-a", "a1"),
            offer("sell", "A", 2.2, 2, "seller-b", "a2"),
            offer("buy", "B", 10.0, 1, "buyer-a", "b1"),
            offer("buy", "B", 9.5, 1, "buyer-b", "b2"),
        ]
        offers[0]["flags"] = ["owner_supplied_author"]
        book = build_order_book(offers, now=NOW)
        values = {
            "a": {"value": 100, "stability": "Stable", "confidence": "high", "data_age_seconds": 60},
            "b": {"value": 100, "stability": "Stable", "confidence": "high", "data_age_seconds": 60},
        }
        chains = find_swap_chains(book, values, transfer_fee_usd=0.1,
                                  min_profit_usd=0, min_profit_percent=0)
        self.assertTrue(chains)
        self.assertIn("owner_supplied_author", chains[0]["risk_flags"])

    def test_swap_chain_rejects_received_bid_with_minimum_above_one(self):
        ask = offer("sell", "A", 5, 2, "seller-a", "ask")
        ask_other = offer("sell", "A", 5.2, 2, "seller-b", "ask-other")
        bid = offer("buy", "B", 10, 10, "buyer-a", "bid")
        bid_other = offer("buy", "B", 9.5, 10, "buyer-b", "bid-other")
        bid["min_quantity"] = 2
        book = build_order_book([ask, ask_other, bid, bid_other], now=NOW)
        values = {
            "a": {"value": 100, "stability": "Stable", "confidence": "high", "data_age_seconds": 60},
            "b": {"value": 200, "stability": "Stable", "confidence": "high", "data_age_seconds": 60},
        }
        self.assertEqual(find_swap_chains(book, values), [])

    def test_inventory_does_not_claim_sellability_below_buyers_minimum(self):
        ask = offer("sell", "Candy", 50, 1, "seller", "ask")
        bid = offer("buy", "Candy", 50, 50, "buyer", "bid")
        bid["min_quantity"] = 20
        book = build_order_book([ask, bid], now=NOW)
        advice = inventory_advice({"candy": {"item": "Candy", "quantity": 5}}, book)
        self.assertEqual(advice[0]["verdict"], "HOLD")
        self.assertIn("below_buyer_minimum", advice[0]["risk_flags"])

    def test_swap_chain_supports_n_to_one_and_rejects_stale_or_unsafe_values(self):
        offers = [
            offer("sell", "A", 2.0, 2, "seller-a", "a1"),
            offer("sell", "A", 2.2, 2, "seller-b", "a2"),
            offer("buy", "B", 10.0, 1, "buyer-a", "b1"),
            offer("buy", "B", 9.5, 1, "buyer-b", "b2"),
        ]
        book = build_order_book(offers, now=NOW)
        values = {
            "a": {"value": 100, "stability": "Stable", "confidence": "medium", "data_age_seconds": 60},
            "b": {"value": 200, "stability": "Stable", "confidence": "high", "data_age_seconds": 60},
        }
        chains = find_swap_chains(book, values, transfer_fee_usd=0.5,
                                  min_profit_usd=0.5, min_profit_percent=1)
        self.assertTrue(any(row["quantity_buy"] == 2 and row["quantity_sell"] == 1 for row in chains))
        self.assertAlmostEqual(chains[0]["ask_to_value_ratio"], 0.02)
        stale_values = {**values, "b": {**values["b"], "data_age_seconds": 25 * 3600}}
        self.assertEqual(find_swap_chains(book, stale_values), [])
        unsafe_values = {**values, "b": {**values["b"], "stability": "Volatile"}}
        self.assertEqual(find_swap_chains(book, unsafe_values), [])

    def test_inventory_advice_and_upgrade_verdict(self):
        book = build_order_book([
            offer("sell", "Candy", 5, 4, "seller-a", "s1"),
            offer("sell", "Candy", 5.2, 2, "seller-b", "s2"),
            offer("buy", "Candy", 4.5, 4, "buyer-a", "b1"),
            offer("buy", "Candy", 4.4, 2, "buyer-b", "b2"),
        ], now=NOW)
        advice = inventory_advice(
            {"candy": {"item": "Candy", "quantity": 2}}, book,
            swap_opportunities=[{"buy_item": "candy"}],
        )
        self.assertEqual(advice[0]["verdict"], "UPGRADE VIA SWAP")
        self.assertEqual(advice[0]["suggested_listing_price"], 4.99)

    def test_inventory_flags_falling_market_and_suggests_sell_now(self):
        book = build_order_book([
            offer("sell", "Candy", 4, 2, "seller-a", "s1"),
            offer("sell", "Candy", 4.2, 2, "seller-b", "s2"),
            offer("buy", "Candy", 3.8, 2, "buyer-a", "b1"),
            offer("buy", "Candy", 3.7, 2, "buyer-b", "b2"),
        ], now=NOW)
        previous = {"candy": {"median_market": 5.0}}
        advice = inventory_advice({"candy": {"item": "Candy", "quantity": 1}},
                                  book, previous_snapshot=previous)
        self.assertEqual(advice[0]["verdict"], "SELL NOW")
        self.assertIn("price_falling", advice[0]["risk_flags"])

    def test_malformed_offer_minimum_is_ignored(self):
        malformed = offer("buy", "Candy", 2, 5, "buyer", "bad-min")
        malformed["min_quantity"] = 0
        fractional = offer("buy", "Candy", 2, 5, "buyer", "fractional")
        fractional["quantity"] = 1.5
        self.assertEqual(build_order_book([malformed, fractional], now=NOW), {})

    def test_non_finite_thresholds_fail_closed(self):
        with self.assertRaises(ValueError):
            build_order_book([], now=NOW, max_age_hours=float("nan"))
        with self.assertRaises(ValueError):
            find_swap_chains({}, {}, transfer_fee_usd=float("nan"))
        with self.assertRaises(ValueError):
            inventory_advice({}, {}, undercut_usd=float("nan"))

    def test_owner_supplied_author_labels_never_inflate_confidence(self):
        ask = offer("sell", "Candy", 5.0, 2, "claimed-seller", "ask")
        bid = offer("buy", "Candy", 8.0, 2, "claimed-buyer", "bid")
        ask["flags"] = ["owner_supplied_author"]
        bid["flags"] = ["owner_supplied_author"]

        row = build_order_book([ask, bid], now=NOW)["candy"]

        self.assertEqual(row["author_count"], 0)
        self.assertEqual(row["confidence"], "low")
        self.assertIsNone(row["best_ask"])
        self.assertIsNone(row["best_bid"])
        self.assertIn("owner_supplied_author", row["risk_flags"])
        self.assertEqual(find_direct_arbitrage({"candy": row}), [])

    def test_owner_supplied_author_is_not_counted_alongside_verified_authors(self):
        offers = [
            offer("sell", "Candy", 5.0, 2, "seller-a", "ask-a"),
            offer("sell", "Candy", 5.2, 2, "seller-b", "ask-b"),
            offer("buy", "Candy", 8.0, 2, "claimed-buyer", "bid"),
        ]
        offers[2]["flags"] = ["owner_supplied_author"]

        row = build_order_book(offers, now=NOW)["candy"]

        self.assertEqual(row["author_count"], 2)
        self.assertEqual(row["confidence"], "medium")
        self.assertIsNone(row["best_bid"])
        self.assertEqual(row["ask_author_count"], 2)
        self.assertEqual(row["bid_author_count"], 0)

    def test_outlier_and_flooded_offers_cannot_create_arbitrage(self):
        offers = [
            offer("sell", "Candy", 5.0, 2, "seller-a", "ask-a"),
            offer("sell", "Candy", 5.2, 2, "seller-b", "ask-b"),
            offer("buy", "Candy", 4.8, 2, "buyer-a", "bid-a"),
            offer("buy", "Candy", 4.7, 2, "buyer-b", "bid-b"),
            offer("buy", "Candy", 100.0, 2, "outlier-buyer", "outlier"),
            offer("sell", "Candy", 1.0, 100, "flood-seller", "flood"),
        ]

        row = build_order_book(offers, now=NOW)["candy"]

        self.assertEqual(row["best_ask"]["unit_price_usd"], 5.0)
        self.assertEqual(row["best_bid"]["unit_price_usd"], 4.8)
        self.assertIn("price_far_from_median", row["risk_flags"])
        self.assertIn("market_flooding", row["risk_flags"])
        self.assertEqual(find_direct_arbitrage({"candy": row}), [])

    def test_sell_before_drop_bid_is_not_actionable(self):
        offers = [
            offer("sell", "Candy", 5.0, 2, "seller-a", "ask-a"),
            offer("sell", "Candy", 5.2, 2, "seller-b", "ask-b"),
            offer("buy", "Candy", 8.0, 2, "warning-buyer", "warning"),
            offer("buy", "Candy", 4.8, 2, "buyer-a", "bid-a"),
            offer("buy", "Candy", 4.7, 2, "buyer-b", "bid-b"),
        ]
        offers[2]["source_text"] = "Sell before it drops"

        row = build_order_book(offers, now=NOW)["candy"]

        self.assertEqual(row["best_bid"]["unit_price_usd"], 4.8)
        self.assertIn("sell_before_drop", row["risk_flags"])

    def test_malformed_flag_container_is_normalized_without_crashing(self):
        row = offer("sell", "Candy", 1.0, 100, "seller", "flood")
        row["flags"] = "not-a-list"
        offers = [
            row,
            offer("sell", "Candy", 5.0, 2, "seller-a", "ask-a"),
            offer("sell", "Candy", 5.2, 2, "seller-b", "ask-b"),
            offer("buy", "Candy", 4.8, 2, "buyer-a", "bid-a"),
            offer("buy", "Candy", 4.7, 2, "buyer-b", "bid-b"),
        ]

        book = build_order_book(offers, now=NOW)

        self.assertIn("market_flooding", book["candy"]["risk_flags"])
        self.assertIsInstance(row["flags"], list)


    def test_private_price_history_is_atomic_rolling_and_compact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-history.json"
            snapshot = {"candy": {"best_ask": {"unit_price_usd": 5},
                                  "best_bid": {"unit_price_usd": 4},
                                  "median_market": 4.5, "author_count": 3}}
            save_private_price_history(path, snapshot, now=NOW)
            save_private_price_history(path, snapshot, now=NOW + 15 * 86400)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["snapshots"]), 1)
            self.assertEqual(saved["snapshots"][0]["items"]["candy"]["median_market"], 4.5)
            self.assertFalse(list(Path(directory).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
