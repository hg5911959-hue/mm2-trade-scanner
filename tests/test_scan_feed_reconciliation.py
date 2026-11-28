from __future__ import annotations

import unittest
from unittest.mock import patch

from test_support import IsolatedDataTestCase

import scanner


class DiscordFeedMergeTests(IsolatedDataTestCase):
    def test_cash_mode_never_loads_or_merges_discord_listings(self):
        listings = [{"id": "blox-1"}]
        with patch("discord_parser.load_discord_listings") as load:
            result = scanner._merge_optional_discord_listings(listings, cash_filter=True)
        self.assertIs(result, listings)
        self.assertEqual(result, [{"id": "blox-1"}])
        load.assert_not_called()

    def test_non_cash_mode_merges_only_ids_not_already_in_bloxswaps_batch(self):
        listings = [{"id": "same-id"}, {"id": "blox-2"}]
        discord_rows = [
            {"id": "same-id", "source": "discord"},
            {"id": "discord-1", "source": "discord"},
        ]
        with patch("discord_parser.load_discord_listings", return_value=(discord_rows, 2)):
            result = scanner._merge_optional_discord_listings(listings, cash_filter=False)
        self.assertEqual([row["id"] for row in result], ["same-id", "blox-2", "discord-1"])

    def test_discord_loader_failure_does_not_stop_bloxswaps_scan(self):
        listings = [{"id": "blox-1"}]
        with patch("discord_parser.load_discord_listings", side_effect=OSError("unavailable")):
            result = scanner._merge_optional_discord_listings(listings, cash_filter=False)
        self.assertIs(result, listings)
        self.assertEqual(result, [{"id": "blox-1"}])


class FirstSeenTimestampTests(IsolatedDataTestCase):
    def test_assigns_first_seen_once_and_preserves_discord_posted_time(self):
        first_seen = {"ad-existing": "2026-11-27T10:00:00+00:00"}
        listings = [
            {"id": "blox-1", "source": "bloxswaps", "source_ad_id": "ad-new"},
            {"id": "discord-1", "source": "discord", "posted_at": "2026-11-27T11:00:00Z"},
            {"id": "blox-2", "source": "bloxswaps", "source_ad_id": "ad-existing"},
            {"id": "blox-3", "source": "bloxswaps"},
        ]
        updated, changed = scanner._assign_first_seen_timestamps(
            listings, ["ad-new", "ad-existing"], first_seen, "2026-11-28T12:00:00+00:00"
        )
        self.assertTrue(changed)
        self.assertEqual(updated["ad-new"], "2026-11-28T12:00:00+00:00")
        self.assertEqual(listings[0]["first_seen_at_utc"], "2026-11-28T12:00:00+00:00")
        self.assertEqual(listings[1]["first_seen_at_utc"], "2026-11-27T11:00:00Z")
        self.assertEqual(listings[2]["first_seen_at_utc"], "2026-11-27T10:00:00+00:00")
        self.assertEqual(listings[3]["first_seen_at_utc"], "2026-11-28T12:00:00+00:00")

    def test_existing_first_seen_state_is_not_marked_changed(self):
        listings = [{"id": "blox-1", "source": "bloxswaps", "source_ad_id": "ad-1"}]
        updated, changed = scanner._assign_first_seen_timestamps(
            listings, ["ad-1"], {"ad-1": "2026-11-27T10:00:00+00:00"},
            "2026-11-28T12:00:00+00:00",
        )
        self.assertFalse(changed)
        self.assertEqual(updated["ad-1"], "2026-11-27T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
