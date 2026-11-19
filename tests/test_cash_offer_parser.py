import unittest
from test_support import IsolatedDataTestCase
from pathlib import Path

from cash_offer_parser import parse_cash_post
from market_book import build_order_book, find_direct_arbitrage


ROOT = Path(__file__).resolve().parents[1]


class CashOfferParserTests(IsolatedDataTestCase):
    def test_appendix_fixture_extracts_expected_prices_and_sides(self):
        fixture = ROOT / "tests" / "fixtures" / "discord_cash_posts_sample.txt"
        result = parse_cash_post(fixture.read_text(encoding="utf-8"), author="seller", message_id="sample-1")
        offers = result["offers"]
        self.assertEqual(len(offers), 22)
        by_item_side = {(row["item"], row["side"]): row for row in offers}
        self.assertEqual(by_item_side[("Chroma Snowcannon", "sell")]["unit_price_usd"], 125.0)
        self.assertEqual(by_item_side[("Icecream", "sell")]["quantity"], 500)
        self.assertEqual(by_item_side[("Icecream", "sell")]["unit_price_usd"], 2.25)
        self.assertEqual(by_item_side[("Celestial Set", "buy")]["unit_price_usd"], 63.0)
        self.assertEqual(by_item_side[("Celestial Set", "buy")]["quantity"], 2)
        self.assertEqual(by_item_side[("Ever Set", "buy")]["quantity"], 5)
        self.assertEqual(by_item_side[("Mortal Set", "sell")]["unit_price_usd"], 4.5)
        self.assertEqual(by_item_side[("Barrel Axe", "sell")]["flags"], ["no_currency_symbol"])
        self.assertEqual(by_item_side[("Icecream", "buy")]["min_quantity"], 20)
        self.assertEqual(result["demand_hints"][0]["item"], "Embersickle")
        reasons = {row["reason"] for row in result["rejected"]}
        self.assertIn("unresolved_name", reasons)

    def test_appendix_sample_has_no_direct_arbitrage(self):
        fixture = ROOT / "tests" / "fixtures" / "discord_cash_posts_sample.txt"
        result = parse_cash_post(fixture.read_text(encoding="utf-8"), author="sample", message_id="sample")
        now = 1_800_000_000
        for index, row in enumerate(result["offers"]):
            row["posted_at"] = now - index
            row["author"] = f"author-{index}"
        book = build_order_book(result["offers"], now=now)
        self.assertEqual(find_direct_arbitrage(book), [])

    def test_buyer_warning_is_preserved_as_risk_flag(self):
        result = parse_cash_post(
            "Buying\n10x Candy 2$/1\nSell before it drops",
            author="buyer", message_id="warning",
        )
        self.assertIn("sell_before_drop", result["offers"][0]["flags"])

    def test_quick_sell_is_a_seller(self):
        result = parse_cash_post("QUICK SELL\n1x Candy 2$/1", message_id="q")
        self.assertEqual(result["offers"][0]["side"], "sell")

    def test_quantity_without_per_unit_marker_is_rejected(self):
        result = parse_cash_post("Selling\n2x Candy 4$", message_id="q")
        self.assertEqual(result["offers"], [])
        self.assertEqual(result["rejected"][0]["reason"], "ambiguous_quantity_price")

    def test_single_item_price_without_currency_is_flagged(self):
        result = parse_cash_post("Selling\nBarrel Axe 1/0.5", message_id="q")
        self.assertEqual(result["offers"][0]["flags"], ["no_currency_symbol"])

    def test_non_finite_price_and_invalid_minimum_quantity_are_rejected(self):
        huge_price = "9" * 400
        result = parse_cash_post(
            f"Selling\\n1x Candy {huge_price}$", author="seller", message_id="huge-price"
        )
        self.assertEqual(result["offers"], [])
        self.assertEqual(result["rejected"][0]["reason"], "invalid_price_or_quantity")
        minimum = parse_cash_post(
            "Buying\\n2x Candy 2$/1 min 3", author="buyer", message_id="bad-min"
        )
        self.assertEqual(minimum["offers"], [])
        self.assertEqual(minimum["rejected"][0]["reason"], "invalid_price_or_quantity")

    def test_unknown_and_ambiguous_names_fail_closed(self):
        result = parse_cash_post("Buying\nbuying c scyth 39$ each\nEvergun 5$", message_id="q")
        self.assertEqual(result["offers"], [])
        self.assertEqual(len(result["rejected"]), 2)
        self.assertTrue(all(row["reason"] == "unresolved_name" for row in result["rejected"]))

    def test_pipe_separated_lines_create_distinct_offers(self):
        result = parse_cash_post("Selling\n1x Chroma alienbeam- 290$ | 2x Traveler axe - 102$/1", message_id="q")
        self.assertEqual(len(result["offers"]), 2)
        self.assertEqual(result["offers"][1]["item"], "Traveler Axe")

    def test_payment_hint_applies_to_following_offers(self):
        result = parse_cash_post("Selling for usdt bep20\n1x Candy 2$/1\n1x Sweet 3$/1", message_id="q")
        self.assertEqual([row["payment"] for row in result["offers"]], ["usdt_bep20", "usdt_bep20"])


if __name__ == "__main__":
    unittest.main()
