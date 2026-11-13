import json
import tempfile
import unittest
from pathlib import Path

from discord_parser import load_discord_listings, parse_trade_text


class DiscordParserTests(unittest.TestCase):
    def test_labelled_trade_parses_items_and_quantities(self):
        listing = parse_trade_text(
            "Trading: 2x Icebreaker, Luger | Looking for: Candy x3, Heat",
            message_id="123456789012345",
            message_url="https://discord.com/channels/1/2/123456789012345",
        )
        self.assertIsNotNone(listing)
        self.assertEqual([(x["name"], x["quantity"]) for x in listing["give_items"]],
                         [("Icebreaker", 2), ("Luger", 1)])
        self.assertEqual([(x["name"], x["quantity"]) for x in listing["receive_items"]],
                         [("Candy", 3), ("Heat", 1)])
        self.assertEqual(listing["source_ad_id"], "discord:123456789012345")

    def test_for_style_trade_parses(self):
        listing = parse_trade_text("Trading Icebreaker for Candy", message_id="123456789012346")
        self.assertEqual(listing["receive_items"][0]["name"], "Icebreaker")
        self.assertEqual(listing["give_items"][0]["name"], "Candy")

    def test_ambiguous_or_one_sided_message_is_rejected(self):
        for message in ("Trading Icebreaker", "Anyone trading?", "Trading: Icebreaker | LF:",
                        "Trading: 0x Icebreaker | LF: Candy",
                        "Trading: 101x Icebreaker | LF: Candy"):
            with self.subTest(message=message):
                self.assertIsNone(parse_trade_text(message, message_id="123456789012347"))

    def test_stable_id_uses_discord_message_identity(self):
        a = parse_trade_text("Trading Icebreaker for Candy", message_id="123456789012348",
                             guild_id="1", channel_id="2")
        b = parse_trade_text("Trading Heat for Luger", message_id="123456789012348",
                             guild_id="1", channel_id="2")
        self.assertEqual(a["id"], b["id"])

    def test_load_json_rejects_bad_schema_and_counts_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            path.write_text(json.dumps({"messages": [
                {"message_id": "123456789012349", "content": "Trading Icebreaker for Candy"},
                {"message_id": "123456789012350", "content": "hello"},
            ]}), encoding="utf-8")
            listings, rejected = load_discord_listings(path)
            self.assertEqual(len(listings), 1)
            self.assertEqual(rejected, 1)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_discord_listings(path)


if __name__ == "__main__":
    unittest.main()
