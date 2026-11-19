"""Regression tests for private Discord offer isolation."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import scanner
from private_state import PRIVATE_FILENAMES
from test_support import IsolatedDataTestCase


class DiscordPrivacyTests(IsolatedDataTestCase):
    def test_discord_watch_fingerprints_never_enter_public_watch_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "watch_ads.json"
            with patch.object(scanner, "WATCH_FILE", target):
                changed = scanner.update_watch_entry(
                    {},
                    {
                        "source": "discord",
                        "source_ad_id": "discord:private-message-id",
                        "give_items": [{"name": "PrivateItem", "quantity": 2}],
                        "receive_items": [{"name": "OtherItem", "quantity": 1}],
                    },
                    {},
                )
            self.assertFalse(changed)
            self.assertFalse(target.exists())

    def test_discord_alert_deduplication_is_stored_in_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "discord_alert_state.json"
            with patch.object(scanner, "DISCORD_ALERT_STATE_FILE", target):
                state = {
                    "alerted": ["private-message-id"],
                    "uncertain": {"another-private-message": 1800000000.0},
                }
                scanner.save_discord_alert_state(state)
                loaded = scanner.load_discord_alert_state()
            self.assertEqual(loaded["alerted"], ["private-message-id"])
            self.assertEqual(loaded["uncertain"], {"another-private-message": 1800000000.0})
            self.assertIn("discord_alert_state.json", PRIVATE_FILENAMES)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)

    def test_corrupt_discord_private_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "discord_alert_state.json"
            target.write_text('{"alerted":"not-a-list","uncertain":[]}', encoding="utf-8")
            with patch.object(scanner, "DISCORD_ALERT_STATE_FILE", target):
                with self.assertRaises(RuntimeError):
                    scanner.load_discord_alert_state()


if __name__ == "__main__":
    import unittest
    unittest.main()
