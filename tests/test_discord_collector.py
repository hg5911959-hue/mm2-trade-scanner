import json
import os
import tempfile
import unittest
from unittest.mock import patch
from test_support import IsolatedDataTestCase
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

from discord_collector import (
    atomic_save, filter_messages_to_allowlist, load_existing, merge_messages,
    normalize_message, parse_channel_ids, parse_guild_ids,
)


class DiscordCollectorTests(IsolatedDataTestCase):
    def test_parse_guild_ids_deduplicates(self):
        self.assertEqual(
            parse_guild_ids("1125462206151143464;482699890741477376,1125462206151143464"),
            [1125462206151143464, 482699890741477376],
        )

    def test_parse_guild_ids_rejects_invalid_values(self):
        for raw in ("abc", "123", "-123456789012345", "123456789012345, nope"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_guild_ids(raw)

    def test_parse_channel_ids_deduplicates(self):
        self.assertEqual(
            parse_channel_ids("1572655277130514472;1567557584402120785,1572655277130514472"),
            [1572655277130514472, 1567557584402120785],
        )

    def test_parse_channel_ids_rejects_invalid_values(self):
        for raw in ("abc", "123", "-123456789012345", "123456789012345, nope"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_channel_ids(raw)

    def test_normalize_message_keeps_jump_url_and_content(self):
        created = datetime(2026, 11, 13, tzinfo=timezone.utc)
        message = SimpleNamespace(
            id=123456789012345,
            guild=SimpleNamespace(id=223456789012345, name="MM2"),
            channel=SimpleNamespace(id=323456789012345, name="trading"),
            author=SimpleNamespace(id=423456789012345, display_name="Trader", name="Trader", bot=False),
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
        self.assertEqual(row["author_id"], "423456789012345")
        self.assertEqual(row["author_id_source"], "discord_api")

    def test_normalize_message_does_not_trust_invalid_author_ids(self):
        message = SimpleNamespace(
            id=123456789012345,
            guild=SimpleNamespace(id=223456789012345, name="MM2"),
            channel=SimpleNamespace(id=323456789012345, name="trading"),
            author=SimpleNamespace(id="not-a-snowflake", display_name="Trader", bot=False),
            content="Trading",
            attachments=[],
            embeds=[],
            created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
            jump_url="",
        )

        row = normalize_message(message)

        self.assertEqual(row["author_id"], "")
        self.assertEqual(row["author_id_source"], "")

    def test_existing_history_is_filtered_to_current_guild_and_channel_allowlists(self):
        allowed = {"message_id": "1", "guild_id": "100000000000001", "channel_id": "200000000000001"}
        other_channel = {"message_id": "2", "guild_id": "100000000000001", "channel_id": "200000000000002"}
        other_guild = {"message_id": "3", "guild_id": "100000000000002", "channel_id": "200000000000001"}

        filtered = filter_messages_to_allowlist(
            [allowed, other_channel, other_guild, "malformed"],
            [100000000000001], [200000000000001],
        )

        self.assertEqual(filtered, [allowed])
        self.assertEqual(
            filter_messages_to_allowlist([allowed], [], [200000000000001]),
            [],
        )

    def test_atomic_save_does_not_follow_predictable_tmp_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "discord_messages.json"
            outside = root / "outside.json"
            outside.write_text("preserve-me", encoding="utf-8")
            output.with_name(output.name + ".tmp").symlink_to(outside)

            atomic_save(
                output, [], [1125462206151143464],
                [1572655277130514472], ["1572655277130514472"],
            )

            self.assertEqual(outside.read_text(encoding="utf-8"), "preserve-me")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["configured_channel_ids"], ["1572655277130514472"])

    def test_load_existing_rejects_symlink_without_reading_target(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.json"
            target.write_text('{"messages":[]}', encoding="utf-8")
            link = root / "discord_messages.json"
            link.symlink_to(target)

            with self.assertRaisesRegex(RuntimeError, "cannot be opened safely"):
                load_existing(link)

    def test_load_existing_rejects_fifo_without_blocking(self):
        if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
            self.skipTest("FIFO/nonblocking open is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discord_messages.pipe"
            os.mkfifo(path)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                load_existing(path)

    def test_load_existing_enforces_read_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discord_messages.json"
            path.write_text('{"messages":[]}', encoding="utf-8")
            with patch("discord_collector.MAX_EXISTING_STATE_BYTES", 4):
                with self.assertRaisesRegex(RuntimeError, "exceeds the size limit"):
                    load_existing(path)

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


if __name__ == "__main__":
    unittest.main()
