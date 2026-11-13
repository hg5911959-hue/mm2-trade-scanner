import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from discord_collector import merge_messages, normalize_message, parse_guild_ids


class DiscordCollectorTests(unittest.TestCase):
    def test_parse_guild_ids_deduplicates(self):
        self.assertEqual(
            parse_guild_ids("1125462206151143464;482699890741477376,1125462206151143464"),
            [1125462206151143464, 482699890741477376],
        )

    def test_parse_guild_ids_rejects_invalid_values(self):
        for raw in ("abc", "123", "-123456789012345", "123456789012345, nope"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_guild_ids(raw)

    def test_normalize_message_keeps_jump_url_and_content(self):
        created = datetime(2026, 11, 13, tzinfo=timezone.utc)
        message = SimpleNamespace(
            id=123456789012345,
            guild=SimpleNamespace(id=223456789012345, name="MM2"),
            channel=SimpleNamespace(id=323456789012345, name="trading"),
            author=SimpleNamespace(display_name="Trader", name="Trader", bot=False),
            content="Trading two items",
            attachments=[],
            embeds=[],
            created_at=created,
            jump_url="https://discord.com/channels/1/2/3",
        )
        row = normalize_message(message)
        self.assertEqual(row["source"], "discord")
        self.assertEqual(row["content"], "Trading two items")
        self.assertEqual(row["url"], "https://discord.com/channels/1/2/3")

    def test_merge_deduplicates_and_retains_recent(self):
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        rows = merge_messages(
            [{"message_id": "1", "created_at_utc": recent, "content": "old"}],
            [
                {"message_id": "1", "created_at_utc": recent, "content": "updated"},
                {"message_id": "2", "created_at_utc": recent, "content": "new"},
                {"message_id": "3", "created_at_utc": old, "content": "stale"},
            ],
        )
        self.assertEqual({row["message_id"] for row in rows}, {"1", "2"})
        self.assertEqual(next(row for row in rows if row["message_id"] == "1")["content"], "updated")


if __ (unknown) `unittest.main()`
