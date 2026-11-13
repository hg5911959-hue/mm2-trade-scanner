import sqlite3
import tempfile
import unittest
from pathlib import Path

import market_history
from tools.export_dashboard import build_dashboard_data


class MarketHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "history.sqlite3"
        self.store = market_history.MarketHistory(self.db_path)

    @staticmethod
    def values(supreme=100):
        return {
            "icebreaker": {
                "name": "Icebreaker",
                "variants": [{
                    "id": "item-1", "slug": "icebreaker", "name": "Icebreaker",
                    "value": supreme, "secondary_value": 100,
                    "category": "godly", "type": "knife", "stability": "Stable",
                }],
            },
            "candy": {
                "name": "Candy",
                "variants": [{
                    "id": "item-2", "slug": "candy", "name": "Candy",
                    "value": 80, "secondary_value": 78,
                    "category": "godly", "type": "knife", "stability": "Stable",
                }],
            },
        }

    @staticmethod
    def listing(source="bloxswaps", listing_id="listing-public"):
        return {
            "id": listing_id, "source": source, "source_ad_id": "ad-123",
            "owner_name": "PublicTrader", "owner_url": "https://bloxswaps.com/fr/users/123",
            "url": "https://bloxswaps.com/fr/trade-mm2?ad=123",
            "first_seen_at_utc": "2026-11-13T10:00:00+00:00",
            "give_items": [{"name": "Icebreaker", "quantity": 1}],
            "receive_items": [{"name": "Candy", "quantity": 2}],
        }

    def test_value_snapshots_are_deduplicated_and_source_agreement_classified(self):
        self.assertTrue(self.store.record_values_snapshot(self.values()))
        self.assertFalse(self.store.record_values_snapshot(self.values()))
        self.assertTrue(self.store.record_values_snapshot(self.values(supreme=160)))
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0], 2)
            rows = db.execute(
                "SELECT confidence FROM market_values WHERE item_key='item-1' ORDER BY snapshot_id"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["cross_checked", "conflict"])

    def test_listing_upsert_preserves_first_seen_and_updates_last_seen(self):
        self.assertEqual(self.store.record_listings([self.listing()]), 1)
        self.assertEqual(self.store.record_listings([self.listing()]), 1)
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT first_seen_at_utc,last_seen_at_utc FROM listings WHERE listing_id='listing-public'"
            ).fetchone()
        self.assertEqual(row[0], "2026-11-13T10:00:00+00:00")
        self.assertIsNotNone(row[1])

    def test_opportunities_are_idempotent(self):
        listing = self.listing()
        opportunity = {
            "listing": listing, "give_total": 80, "receive_total": 100,
            "difference": 20, "margin": 25,
            "give_lines": ["Candy x2"], "receive_lines": ["Icebreaker"],
        }
        self.assertTrue(self.store.record_opportunity(opportunity))
        self.assertFalse(self.store.record_opportunity(opportunity))

    def test_public_export_excludes_private_discord_records(self):
        self.store.record_values_snapshot(self.values())
        self.store.record_listings([self.listing()])
        private = self.listing(source="discord", listing_id="private-discord-1")
        private["url"] = "https://discord.com/channels/private/123"
        self.store.record_listings([private])
        self.store.record_opportunity({
            "listing": self.listing(), "give_total": 80, "receive_total": 100,
            "difference": 20, "margin": 25, "give_lines": [], "receive_lines": [],
        })
        self.store.record_opportunity({
            "listing": private, "give_total": 80, "receive_total": 100,
            "difference": 20, "margin": 25, "give_lines": [], "receive_lines": [],
        })

        payload = build_dashboard_data(self.db_path)
        self.assertEqual(payload["summary"]["trackedListings"], 1)
        self.assertEqual(payload["summary"]["opportunities7d"], 1)
        self.assertEqual(len(payload["recentListings"]), 1)
        self.assertEqual(len(payload["opportunities"]), 1)
        serialized = str(payload)
        self.assertNotIn("private-discord-1", serialized)
        self.assertNotIn("discord.com/channels/private", serialized)

    def test_missing_database_exports_empty_dashboard(self):
        payload = build_dashboard_data(Path(self.tmp.name) / "missing.sqlite3")
        self.assertEqual(payload["summary"]["trackedListings"], 0)
        self.assertEqual(payload["opportunities"], [])


if __name__ == "__main__":
    unittest.main()
