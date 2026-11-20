import unittest
from test_support import IsolatedDataTestCase
from datetime import datetime, timedelta, timezone

from discord_collector import merge_messages, normalize_message, parse_guild_ids


class DiscordCollectorTests(IsolatedDataTestCase):
    def test_parse_guild_ids_deduplicates(self):
        self.assertEqual(
            parse_guild_ids("1125462206151143464;482699890741477376,1125462206151143464"),
            [1125462206151143464, 482699890741477376],
        )

    def test_parse_channel_ids_deduplicates_and_bounds_input(self):
        from discord_collector import parse_channel_ids

        self.assertEqual(
            parse_channel_ids("1572655277130514472;1567557584402120785,1572655277130514472"),
            ["1572655277130514472", "1567557584402120785"],
        )
        with self.assertRaises(ValueError):
            parse_channel_ids(",".join(str(100000000000000000 + i) for i in range(11)))

    def test_parse_channel_ids_rejects_invalid_values(self):
        from discord_collector import parse_channel_ids

        for raw in ("abc", "123", "-123456789012345", "123456789012345, nope"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_channel_ids(raw)

    def test_collect_can_be_restricted_to_exact_configured_channels(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from discord_collector import collect

        calls = []

        def fake_get(path, token, params=None, timeout=20):
            calls.append(path)
            if path == "/guilds/1125462206151143464/channels":
                return [
                    {"id": "200000000000000001", "type": 0, "name": "wanted"},
                    {"id": "200000000000000002", "type": 0, "name": "not-wanted"},
                ]
            if path == "/guilds/1125462206151143464/threads/active":
                return {"threads": []}
            if path == "/guilds/482699890741477376/channels":
                return []
            if path == "/guilds/482699890741477376/threads/active":
                return {"threads": []}
            if path == "/channels/200000000000000001/messages":
                return [{
                    "id": "300000000000000001", "channel_id": "200000000000000001",
                    "author": {"username": "seller", "bot": False},
                    "content": "Selling Candy 2$", "timestamp": "2026-11-19T20:00:00+00:00",
                }]
            raise AssertionError(f"Unexpected Discord API path: {path}")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discord_messages.json"
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "bot-token"}):
                with patch("discord_collector._discord_get_json", side_effect=fake_get):
                    result = collect(
                        [1125462206151143464, 482699890741477376], output, 100, 7,
                        channel_ids=["200000000000000001"],
                    )
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(saved["scanned_channel_ids"], ["200000000000000001"])
        self.assertEqual(
            calls,
            [
                "/guilds/1125462206151143464/channels",
                "/guilds/1125462206151143464/threads/active",
                "/guilds/482699890741477376/channels",
                "/guilds/482699890741477376/threads/active",
                "/channels/200000000000000001/messages",
            ],
        )

    def test_missing_configured_channel_marks_collection_degraded(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from discord_collector import collect

        def fake_get(path, token, params=None, timeout=20):
            if path == "/guilds/1125462206151143464/channels":
                return [{"id": "200000000000000001", "type": 0, "name": "other"}]
            if path == "/guilds/1125462206151143464/threads/active":
                return {"threads": []}
            raise AssertionError(f"Unexpected Discord API path: {path}")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discord_messages.json"
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "bot-token"}):
                with patch("discord_collector._discord_get_json", side_effect=fake_get):
                    result = collect(
                        [1125462206151143464], output, 100, 7,
                        channel_ids=["200000000000000099"],
                    )

        self.assertEqual(result, 1)


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


    def test_partial_collection_reports_degraded_exit_status(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from discord_collector import collect

        def fake_get(path, token, params=None, timeout=20):
            if path == "/guilds/1125462206151143464/channels":
                return [
                    {"id": "200000000000000001", "type": 0, "name": "readable"},
                    {"id": "200000000000000002", "type": 0, "name": "restricted"},
                ]
            if path == "/guilds/1125462206151143464/threads/active":
                return {"threads": []}
            if path == "/channels/200000000000000001/messages":
                return [{
                    "id": "300000000000000001", "channel_id": "200000000000000001",
                    "author": {"username": "seller", "bot": False},
                    "content": "Selling Candy 2$", "timestamp": "2026-11-19T20:00:00+00:00",
                }]
            if path == "/channels/200000000000000002/messages":
                raise PermissionError("Discord bot lacks permission to read this resource")
            raise AssertionError(f"Unexpected Discord API path: {path}")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discord_messages.json"
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "bot-token"}):
                with patch("discord_collector._discord_get_json", side_effect=fake_get):
                    result = collect([1125462206151143464], output, 100, 7)
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(saved["message_count"], 1)
        self.assertEqual(saved["messages"][0]["content"], "Selling Candy 2$")


    def test_channel_history_paginates_until_a_short_page(self):
        from unittest.mock import patch
        from discord_collector import _fetch_channel_history

        first = [{"id": "300000000000000002"}, {"id": "300000000000000001"}]
        second = [{"id": "299999999999999999"}]
        with patch("discord_collector._discord_get_json", side_effect=[first, second]) as get_page:
            messages, truncated = _fetch_channel_history("200000000000000001", "bot-token", 2)

        self.assertFalse(truncated)
        self.assertEqual(len(messages), 3)
        self.assertEqual(get_page.call_args_list[0].kwargs["params"], {"limit": 2})
        self.assertEqual(
            get_page.call_args_list[1].kwargs["params"],
            {"limit": 2, "before": "300000000000000001"},
        )

    def test_channel_history_fails_closed_at_bounded_page_cap(self):
        from unittest.mock import patch
        from discord_collector import MAX_HISTORY_PAGES_PER_CHANNEL, _fetch_channel_history

        pages = [[{"id": str(300000000000000000 - index)}] for index in range(MAX_HISTORY_PAGES_PER_CHANNEL)]
        with patch("discord_collector._discord_get_json", side_effect=pages) as get_page:
            messages, truncated = _fetch_channel_history("200000000000000001", "bot-token", 1)

        self.assertTrue(truncated)
        self.assertEqual(len(messages), MAX_HISTORY_PAGES_PER_CHANNEL)
        self.assertEqual(get_page.call_count, MAX_HISTORY_PAGES_PER_CHANNEL)


if __name__ == "__main__":
    unittest.main()
