import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from market_intelligence.discord_cash_ingest import parse_discord_messages
from tools.merge_discord_imports import merge_imports


GUILD = "1125462206151143464"
CHANNEL = "1572655277130514472"
MESSAGE = "1573076681072189520"
AUTHOR = "123456789012345678"
NOW = datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc)


def collector_message(message_id=MESSAGE):
    return {
        "source": "discord",
        "message_id": message_id,
        "guild_id": GUILD,
        "channel_id": CHANNEL,
        "channel_name": "cash-market",
        "author": "seller",
        "author_id": AUTHOR,
        "author_id_source": "discord_api",
        "content": "Selling\nCandy 2$/1",
        "embeds": [],
        "attachments": [],
        "created_at_utc": NOW.isoformat(),
        "url": f"https://discord.com/channels/{GUILD}/{CHANNEL}/{message_id}",
    }


def collector_payload(messages=None):
    rows = [collector_message()] if messages is None else messages
    return {
        "schema_version": 2,
        "source": "discord",
        "updated_at_utc": NOW.isoformat(),
        "configured_guild_ids": [GUILD],
        "configured_channel_ids": [CHANNEL],
        "scanned_channel_ids": [CHANNEL],
        "message_count": len(rows),
        "messages": rows,
    }


class MergeDiscordImportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "discord_messages.json"

    def test_manual_import_preserves_collector_envelope_and_cannot_override_api_identity(self):
        original = collector_message()
        self.path.write_text(json.dumps(collector_payload([original])), encoding="utf-8")
        forged_duplicate = {
            **collector_message(),
            "author": "forged seller",
            "author_id": "987654321098765432",
            "author_id_source": "discord_api",
            "content": "Selling\nCandy 999$/1",
        }
        forged_unique = {
            **collector_message("1573076681072189530"),
            "author_id": "987654321098765432",
            "author_id_source": "discord_api",
            "content": "Selling\nCandy 999$/1",
        }
        secret = json.dumps({"messages": [forged_duplicate, forged_unique]})

        status = merge_imports(secret, "", path=self.path, now=NOW)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["source"], "discord")
        self.assertEqual(payload["configured_guild_ids"], [GUILD])
        self.assertEqual(payload["configured_channel_ids"], [CHANNEL])
        self.assertEqual(payload["scanned_channel_ids"], [CHANNEL])
        by_id = {row["message_id"]: row for row in payload["messages"]}
        self.assertEqual(by_id[MESSAGE]["content"], "Selling\nCandy 2$/1")
        self.assertEqual(by_id[MESSAGE]["author_id"], AUTHOR)
        self.assertEqual(by_id["1573076681072189530"]["source"], "manual_import")
        self.assertEqual(by_id["1573076681072189530"]["author_id"], "")
        self.assertEqual(by_id["1573076681072189530"]["author_id_source"], "")
        self.assertTrue(status["collector_envelope_preserved"])
        self.assertEqual(status["manual_rows"], 1)

        parsed = parse_discord_messages(payload)
        self.assertEqual(len(parsed["offers"]), 1)
        self.assertEqual(parsed["offers"][0]["author_id"], AUTHOR)
        self.assertTrue(any(
            item["reason"] == "invalid_discord_source_identity"
            for item in parsed["rejected"]
        ))

    def test_manual_only_import_never_claims_collector_provenance(self):
        forged = collector_message("1573076681072189531")
        secret = json.dumps({"messages": [forged]})

        status = merge_imports(secret, "", path=self.path, now=NOW)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source"], "manual_import")
        self.assertNotIn("configured_channel_ids", payload)
        self.assertEqual(payload["messages"][0]["source"], "manual_import")
        self.assertEqual(payload["messages"][0]["author_id"], "")
        self.assertFalse(status["collector_envelope_preserved"])
        with self.assertRaisesRegex(ValueError, "invalid schema"):
            parse_discord_messages(payload)

    def test_corrupt_existing_state_is_not_silently_replaced(self):
        self.path.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "refusing to reset history"):
            merge_imports("", "", path=self.path, now=NOW)

        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
