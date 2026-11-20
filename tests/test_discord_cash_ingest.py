import unittest
from datetime import UTC, datetime

from test_support import IsolatedDataTestCase

from market_book import build_order_book, find_direct_arbitrage
from market_intelligence.discord_cash_ingest import parse_discord_messages


NOW = 1_800_000_000.0
GUILD_ID = "223456789012345"
CHANNEL_ID = "323456789012345"


def collector_payload(messages):
    return {
        "schema_version": 2,
        "source": "discord",
        "configured_guild_ids": [GUILD_ID],
        "configured_channel_ids": [CHANNEL_ID],
        "scanned_channel_ids": [CHANNEL_ID],
        "messages": messages,
    }


def discord_message(index, body, *, author_id=None, author_id_source="discord_api"):
    message_id = f"12345678901234{index}"
    row = {
        "source": "discord",
        "message_id": message_id,
        "guild_id": GUILD_ID,
        "channel_id": CHANNEL_ID,
        "channel_name": "trading",
        "author": f"Trader {index}",
        "content": body,
        "embeds": [],
        "created_at_utc": datetime.fromtimestamp(NOW - 300, UTC).isoformat(),
        "url": f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}",
    }
    if author_id is not None:
        row["author_id"] = author_id
        row["author_id_source"] = author_id_source
    return row


class DiscordCashIngestTests(IsolatedDataTestCase):
    def test_valid_collector_rows_preserve_identity_and_provenance(self):
        payload = collector_payload([
            discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        ])

        result = parse_discord_messages(payload)

        self.assertEqual(len(result["offers"]), 1)
        offer = result["offers"][0]
        self.assertEqual(offer["author_id"], "423456789012345")
        self.assertEqual(offer["author_id_source"], "discord_api")
        self.assertEqual(offer["source_type"], "discord_channel")
        self.assertEqual(offer["source_channel_id"], CHANNEL_ID)
        self.assertEqual(offer["message_id"], "discord:123456789012341")

    def test_owner_supplied_author_id_is_discarded_and_offer_is_non_actionable(self):
        payload = collector_payload([
            discord_message(
                1, "Selling\n1x Candy 4$/1",
                author_id="423456789012345", author_id_source="owner_supplied",
            )
        ])

        result = parse_discord_messages(payload)
        offer = result["offers"][0]
        book = build_order_book(result["offers"], now=NOW)

        self.assertEqual(offer["author_id"], "")
        self.assertIn("owner_supplied_author", offer["flags"])
        self.assertEqual(book["candy"]["author_count"], 0)
        self.assertIsNone(book["candy"]["best_ask"])

    def test_permalink_mismatch_and_missing_timestamp_fail_closed(self):
        wrong_url = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        wrong_url["url"] = "https://discord.com/channels/1/2/3"
        no_time = discord_message(2, "Selling\n1x Candy 4$/1", author_id="423456789012346")
        no_time["created_at_utc"] = ""

        result = parse_discord_messages(collector_payload([wrong_url, no_time]))

        self.assertEqual(result["offers"], [])
        self.assertEqual(
            {row["reason"] for row in result["rejected"]},
            {"discord_permalink_mismatch", "invalid_or_missing_timestamp"},
        )

    def test_invalid_first_duplicate_does_not_shadow_valid_record(self):
        invalid = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        invalid["url"] = "https://discord.com/channels/1/2/3"
        valid = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")

        result = parse_discord_messages(collector_payload([invalid, valid]))

        self.assertEqual(len(result["offers"]), 1)
        self.assertEqual(result["offers"][0]["unit_price_usd"], 4.0)
        self.assertEqual(result["stats"]["duplicate_messages"], 0)

    def test_collector_envelope_requires_expected_source_and_version(self):
        payload = collector_payload([])
        payload["schema_version"] = 1
        with self.assertRaises(ValueError):
            parse_discord_messages(payload)
        payload["schema_version"] = 2
        payload["source"] = "manual"
        with self.assertRaises(ValueError):
            parse_discord_messages(payload)

    def test_duplicate_message_ids_are_processed_once(self):
        row = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        duplicate = {**row, "content": "Selling\n1x Candy 9$/1"}

        result = parse_discord_messages(collector_payload([row, duplicate]))

        self.assertEqual(len(result["offers"]), 1)
        self.assertEqual(result["stats"]["duplicate_messages"], 1)
        self.assertEqual(result["offers"][0]["unit_price_usd"], 4.0)

    def test_demand_hints_never_become_buy_offers(self):
        result = parse_discord_messages(collector_payload([
            discord_message(1, "Buying\nEmbersickle", author_id="423456789012345")
        ]))

        self.assertEqual(result["offers"], [])
        self.assertEqual(len(result["demand_hints"]), 1)
        self.assertEqual(result["demand_hints"][0]["item"], "Embersickle")

    def test_end_to_end_verified_independent_offers_create_arbitrage(self):
        rows = [
            discord_message(1, "Selling\n5x Candy 4$/1", author_id="423456789012345"),
            discord_message(2, "Selling\n5x Candy 4.2$/1", author_id="423456789012346"),
            discord_message(3, "Buying\n5x Candy 5$/1", author_id="423456789012347"),
            discord_message(4, "Buying\n5x Candy 4.8$/1", author_id="423456789012348"),
        ]

        parsed = parse_discord_messages(collector_payload(rows))
        book = build_order_book(parsed["offers"], now=NOW)
        opportunities = find_direct_arbitrage(book)

        self.assertEqual(book["candy"]["confidence"], "high")
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0]["quantity"], 5)

    def test_unlisted_channel_is_rejected_even_with_valid_message_permalink(self):
        row = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        row["channel_id"] = "423456789012345"
        row["url"] = f"https://discord.com/channels/{GUILD_ID}/{row['channel_id']}/{row['message_id']}"

        result = parse_discord_messages(collector_payload([row]))

        self.assertEqual(result["offers"], [])
        self.assertEqual(result["rejected"][0]["reason"], "invalid_discord_source_identity")

    def test_missing_or_inconsistent_collector_allowlists_are_rejected(self):
        row = discord_message(1, "Selling\n1x Candy 4$/1", author_id="423456789012345")
        with self.assertRaises(ValueError):
            parse_discord_messages({"messages": [row]})
        payload = collector_payload([row])
        payload["scanned_channel_ids"] = ["423456789012345"]
        with self.assertRaises(ValueError):
            parse_discord_messages(payload)

    def test_invalid_payload_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_discord_messages({"messages": "not-a-list"})


if __name__ == "__main__":
    unittest.main()
