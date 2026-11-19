import unittest
from test_support import IsolatedDataTestCase
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from discord_collector import merge_messages, normalize_message, parse_guild_ids


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

    def test_normalize_message_keeps_jump_url_and_content(self):
        message = {
            "id": "123456789012345",
            "channel_id": "323456789012345",
            "author": {"global_name": "Trader", "username": "trader", "bot": False},
            "content": "Trading two items",
            "attachments": [],
            "embeds": [],
            "timestamp": "2026-11-13T00:00:00+00:00",
        }
        channel = {"id": "323456789012345", "name": "trading"}
        row = normalize_message(message, 223456789012345, channel)
        self.assertEqual(row["source"], "discord")
        self.assertEqual(row["content"], "Trading two items")
        self.assertEqual(row["author"], "Trader")
        self.assertEqual(
            row["url"],
            "https://discord.com/channels/223456789012345/323456789012345/123456789012345",
        )

    def test_collect_uses_only_read_only_configured_guild_text_channels(self):
        import json
        import tempfile
        from unittest.mock import patch
        from pathlib import Path
        from discord_collector import collect

        calls = []
        def fake_get(path, token, params=None, timeout=20):
            calls.append((path, token, params))
            if path == "/guilds/1125462206151143464/channels":
                return [
                    {"id": "200000000000000001", "type": 0, "name": "trading"},
                    {"id": "200000000000000002", "type": 2, "name": "voice"},
                ]
            if path == "/guilds/1125462206151143464/threads/active":
                return {"threads": [{"id": "200000000000000003", "type": 11, "name": "thread"}]}
            if path == "/channels/200000000000000001/messages":
                return [{
                    "id": "300000000000000001", "channel_id": "200000000000000001",
                    "author": {"username": "seller", "global_name": "Seller", "bot": False},
                    "content": "Selling Candy 2$", "timestamp": "2026-11-19T20:00:00+00:00",
                    "attachments": [], "embeds": [],
                }]
            if path == "/channels/200000000000000003/messages":
                return []
            raise AssertionError(f"Unexpected Discord API path: {path}")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discord_messages.json"
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "bot-token"}):
                with patch("discord_collector._discord_get_json", side_effect=fake_get):
                    result = collect([1125462206151143464], output, 100, 7)
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(saved["message_count"], 1)
        self.assertEqual(saved["messages"][0]["author"], "Seller")
        self.assertEqual(saved["messages"][0]["guild_id"], "1125462206151143464")
        self.assertEqual(
            [call[0] for call in calls],
            [
                "/guilds/1125462206151143464/channels",
                "/guilds/1125462206151143464/threads/active",
                "/channels/200000000000000001/messages",
                "/channels/200000000000000003/messages",
            ],
        )
        self.assertTrue(all(call[1] == "bot-token" for call in calls))
        self.assertTrue(all(call[2] is None or call[2] == {"limit": 100} for call in calls))

    def test_discord_redirect_handler_rejects_credential_forwarding(self):
        import urllib.error
        import urllib.request
        from discord_collector import _RejectDiscordRedirects

        request = urllib.request.Request(
            "https://discord.com/api/v10/guilds/123",
            headers={"Authorization": "Bot secret"},
        )
        handler = _RejectDiscordRedirects()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://attacker.example/collect"
            )

    def test_collector_has_no_third_party_discord_dependency(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "discord_collector.py").read_text(encoding="utf-8")
        self.assertNotIn("import discord", source)
        self.assertNotIn("discord.py", source)

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
