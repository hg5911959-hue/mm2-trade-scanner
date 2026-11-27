import json
import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase

from tools.public_dashboard_history import PublicDashboardHistory


class PublicDashboardHistoryTests(IsolatedDataTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "public.sqlite3"
        self.history = PublicDashboardHistory(self.database)

    def listing(self, **overrides):
        row = {
            "id": "stable-listing-id",
            "source": "bloxswaps",
            "source_ad_id": "public-post-123",
            "url": "https://bloxswaps.com/trade-mm2?trade-type=P2P&ad=public-post-123",
            "owner_url": "https://bloxswaps.com/fr/users/123",
            "source_author": "PublicTrader",
            "give_items": [{"name": "Candy", "quantity": 2, "value": 999, "stability": "Rising"}],
            "receive_items": [{"name": "Bat", "quantity": 1}],
        }
        row.update(overrides)
        return row

    def opportunity(self, listing=None, **overrides):
        result = {
            "listing": listing or self.listing(valuation_mode="cash"),
            "cash_profit_worst": 2.75,
            "give_total": 10.0,
            "receive_total": 12.75,
            "margin": 27.5,
            "manual_review": False,
        }
        result.update(overrides)
        return result

    def test_persists_only_public_bloxswaps_listings_and_sanitized_items(self):
        rows = [
            self.listing(),
            self.listing(id="private", source="discord", source_ad_id="private-message"),
            self.listing(id="unknown", source="other"),
            self.listing(id="unsafe-url", url="https://attacker.example/listing"),
        ]
        self.assertEqual(self.history.record_listings(rows), 1)

        with sqlite3.connect(self.database) as db:
            row = db.execute(
                "SELECT source, source_id, owner_name, listing_url, give_items_json FROM listings"
            ).fetchone()
            tables = {
                item[0] for item in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }

        self.assertEqual(row[0], "bloxswaps")
        self.assertEqual(row[1], "public-post-123")
        self.assertEqual(row[2], "PublicTrader")
        self.assertIn("bloxswaps.com", row[3])
        items = json.loads(row[4])
        self.assertEqual(items, [{"name": "Candy", "quantity": 2}])
        self.assertEqual(tables, {"listings", "opportunities"})

    def test_source_less_api_listing_is_inferred_from_trusted_bloxswaps_url(self):
        listing = self.listing(valuation_mode="cash")
        listing.pop("source")
        self.assertEqual(self.history.record_listings([listing]), 1)
        self.assertTrue(self.history.record_opportunity(self.opportunity(listing)))
        with sqlite3.connect(self.database) as db:
            sources = db.execute(
                "SELECT source FROM listings UNION SELECT source FROM opportunities"
            ).fetchall()
        self.assertEqual(sources, [("bloxswaps",)])

    def test_records_only_cash_bloxswaps_opportunities(self):
        cash_listing = self.listing(valuation_mode="cash")
        self.assertTrue(self.history.record_opportunity(self.opportunity(cash_listing)))
        with self.assertRaisesRegex(ValueError, "cash-mode"):
            self.history.record_opportunity(
                self.opportunity(self.listing(valuation_mode="value"))
            )
        with self.assertRaisesRegex(ValueError, "only BloxSwaps"):
            self.history.record_opportunity(
                self.opportunity(self.listing(source="discord"))
            )
        with self.assertRaisesRegex(ValueError, "safe listing identity"):
            self.history.record_opportunity(
                self.opportunity(self.listing(url="https://attacker.example/listing", valuation_mode="cash"))
            )
        with sqlite3.connect(self.database) as db:
            rows = db.execute(
                "SELECT source, profit, details_json FROM opportunities"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "bloxswaps")
        self.assertEqual(rows[0][1], 2.75)
        details = json.loads(rows[0][2])
        self.assertEqual(details["valuation_mode"], "cash")
        self.assertEqual(details["give_items"], [{"name": "Candy", "quantity": 2}])
        self.assertNotIn("stability", rows[0][2].casefold())
        self.assertNotIn("supreme_value", rows[0][2].casefold())

    def test_rejects_non_finite_or_missing_conservative_profit(self):
        listing = self.listing(valuation_mode="cash")
        with self.assertRaisesRegex(ValueError, "safe listing identity"):
            self.history.record_opportunity(
                self.opportunity(listing, cash_profit_worst=float("nan"))
            )
        with self.assertRaisesRegex(ValueError, "safe listing identity"):
            self.history.record_opportunity(
                self.opportunity(listing, cash_profit_worst=None)
            )

    def test_database_size_limit_fails_closed(self):
        with patch("tools.public_dashboard_history.MAX_DATABASE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                self.history.record_listings([self.listing()])

    def test_symlink_database_path_is_rejected(self):
        target = Path(self.directory.name) / "target.sqlite3"
        target.touch()
        link = Path(self.directory.name) / "link.sqlite3"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            PublicDashboardHistory(link)


if __name__ == "__main__":
    unittest.main()
