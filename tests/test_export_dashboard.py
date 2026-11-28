import unittest
import json
import tempfile
import sqlite3
from pathlib import Path

from test_support import IsolatedDataTestCase
from tools.export_dashboard import _robase_dashboard_payload, build_dashboard_data
from market_history import MarketHistory
from tools.public_dashboard_history import PublicDashboardHistory


def catalogue():
    return {
        "schema_version": 6,
        "price_basis": "chromas_gg_7d_price",
        "catalog_complete": True,
        "catalog_expected_count": 2,
        "items": [
            {
                "name": "Candy",
                "status": "verified",
                "cash_price_usd": 2.8,
                "price_display": "$2.80",
                "selected_marketplace": "chromas.gg",
                "selected_marketplace_sold_7d": 12,
                "marketplace_prices": {
                    "chromas.gg": {
                        "marketplace": "chromas.gg",
                        "price_usd": 2.8,
                        "sold_7d": 12,
                    }
                },
                "sold_7d": 999,
                "sold_30d": 888,
                "price_window_days": 7,
                "last_checked_at_utc": "2026-11-26T10:00:00Z",
                "price_updated_at_utc": "2026-11-26T10:00:00Z",
                "robase_category": "Godly",
                "source": "https://robase.gg/item/candy",
                "bloxswaps_copies": 100,
                "bloxswaps_holders": 50,
            },
            {
                "name": "Old Glory",
                "status": "verified",
                "cash_price_usd": None,
                "price_display": "",
                "selected_marketplace": None,
                "selected_marketplace_sold_7d": 0,
                "marketplace_prices": {
                    "chromas.gg": {
                        "marketplace": "chromas.gg",
                        "price_usd": None,
                        "price_display": "",
                        "sold_7d": 0,
                    }
                },
                "sold_7d": 45,
                "sold_30d": 300,
                "price_window_days": None,
                "last_checked_at_utc": "2026-11-26T10:00:00Z",
                "price_updated_at_utc": "2026-11-26T10:00:00Z",
                "robase_category": "Vintage",
                "source": "https://robase.gg/item/old-glory",
            },
        ],
    }


class RobaseDashboardPayloadTests(IsolatedDataTestCase):
    def test_uses_only_chroma_market_price_and_sales_7d(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(catalogue()), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["itemCount"], 2)
        self.assertEqual(summary["pricedItemCount"], 1)
        self.assertEqual(summary["cashEligibleItemCount"], 1)
        self.assertEqual(summary["blockedCashItemCount"], 1)
        self.assertEqual(summary["lowSampleItemCount"], 0)
        self.assertEqual(summary["noSalesItemCount"], 1)
        self.assertEqual(summary["totalSold7d"], 12)
        self.assertFalse(rows[1]["cashEligible"])
        self.assertIn("No Chroma.gg completed sales", rows[1]["cashBlockReason"])
        self.assertEqual(rows[0]["priceUsd"], 2.8)
        self.assertEqual(rows[0]["sold7d"], 12)
        self.assertIsNone(rows[1]["priceUsd"])
        self.assertEqual(rows[1]["sold7d"], 0)
        self.assertNotIn("sold30d", rows[0])
        self.assertNotIn("totalSold30d", summary)
        self.assertEqual(rows[0]["priceBasis"], "chromas.gg Price (7d)")


    def test_exposes_low_sample_items_as_blocked_with_a_clear_reason(self):
        payload = catalogue()
        low_sample = json.loads(json.dumps(payload["items"][0]))
        low_sample.update({
            "name": "Low Sample",
            "cash_price_usd": 1.5,
            "price_display": "$1.50",
            "selected_marketplace_sold_7d": 2,
            "source": "https://robase.gg/item/low-sample",
        })
        low_sample["marketplace_prices"]["chromas.gg"].update({
            "price_usd": 1.5,
            "price_display": "$1.50",
            "sold_7d": 2,
        })
        payload["catalog_expected_count"] = 3
        payload["items"].append(low_sample)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)

        low = next(row for row in rows if row["name"] == "Low Sample")
        self.assertEqual(summary["cashEligibleItemCount"], 1)
        self.assertEqual(summary["blockedCashItemCount"], 2)
        self.assertEqual(summary["lowSampleItemCount"], 1)
        self.assertFalse(low["cashEligible"])
        self.assertEqual(low["cashBlockReason"], "Only 2 Chroma.gg completed sale(s) in 7 days; 3 required")

    def test_rejects_zero_sales_chroma_row_with_a_price(self):
        payload = catalogue()
        payload["items"][1]["marketplace_prices"]["chromas.gg"]["price_usd"] = 1.25
        payload["items"][1]["marketplace_prices"]["chromas.gg"]["price_display"] = "$1.25"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)
        self.assertFalse(summary["available"])
        self.assertEqual(rows, [])

    def test_distinguishes_missing_chroma_data_from_zero_sales(self):
        payload = catalogue()
        payload["items"][1]["marketplace_prices"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["missingChromaDataCount"], 1)
        self.assertIsNone(rows[1]["sold7d"])
        self.assertFalse(rows[1]["salesAvailable"])
        self.assertEqual(rows[1]["priceBasis"], "Chroma 7d data unavailable")

    def test_rejects_legacy_price_schema(self):
        payload = catalogue()
        payload["schema_version"] = 5
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)
        self.assertFalse(summary["available"])
        self.assertEqual(rows, [])

    def test_rejects_non_chroma_marketplace(self):
        payload = catalogue()
        payload["items"][0]["marketplace_prices"]["starpets"] = {
            "marketplace": "starpets", "price_usd": 9.99, "sold_7d": 100
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            summary, rows = _robase_dashboard_payload(path)
        self.assertFalse(summary["available"])
        self.assertEqual(rows, [])


    def test_empty_but_valid_sqlite_history_is_reported_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices = root / "prices.json"
            prices.write_text(json.dumps(catalogue()), encoding="utf-8")
            database = root / "history.sqlite3"
            MarketHistory(database)
            payload = build_dashboard_data(database, prices)

        summary = payload["summary"]
        self.assertTrue(summary["historyAvailable"])
        self.assertEqual(summary["trackedListings"], 0)
        self.assertEqual(summary["opportunities7d"], 0)

    def test_missing_sqlite_history_is_not_reported_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices = root / "prices.json"
            prices.write_text(json.dumps(catalogue()), encoding="utf-8")
            payload = build_dashboard_data(root / "missing.sqlite3", prices)

        summary = payload["summary"]
        self.assertFalse(summary["historyAvailable"])
        self.assertIsNone(summary["trackedListings"])
        self.assertIsNone(summary["opportunities7d"])
        self.assertEqual(payload["robaseSummary"]["pricedItemCount"], 1)


    def test_export_whitelists_item_fields_from_legacy_or_corrupt_sqlite_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices = root / "prices.json"
            prices.write_text(json.dumps(catalogue()), encoding="utf-8")
            database = root / "public-history.sqlite3"
            history = PublicDashboardHistory(database)
            listing = {
                "id": "legacy-sensitive-listing",
                "source": "bloxswaps",
                "source_ad_id": "post-sensitive",
                "url": "https://bloxswaps.com/trade-mm2?ad=post-sensitive",
                "source_author": "PublicTrader",
                "give_items": [{"name": "Bat", "quantity": 1}],
                "receive_items": [{"name": "Candy", "quantity": 2}],
                "valuation_mode": "cash",
            }
            history.record_listings([listing])
            history.record_opportunity({
                "listing": listing,
                "cash_profit_worst": 2.5,
                "give_total": 10.0,
                "receive_total": 12.5,
                "margin": 25.0,
                "manual_review": False,
            })

            sensitive_items = [
                {
                    "name": "Bat", "quantity": 1, "quantity_min": 1,
                    "quantity_max": 1, "quantity_source": "structured",
                    "supreme_value": 999999, "stability": "Rising",
                    "confidence": 100, "rblxvalue_value": 123456,
                    "raw_source_metadata": {"secret": "must-not-export"},
                }
            ]
            sensitive_details = {
                "valuation_mode": "cash",
                "manual_review": False,
                "give_items": sensitive_items,
                "receive_items": sensitive_items,
                "supreme_values": {"Bat": 999999},
                "stability": {"Bat": "Rising"},
                "confidence": 100,
            }
            with sqlite3.connect(database) as db:
                db.execute(
                    "UPDATE listings SET give_items_json = ?, receive_items_json = ?",
                    (json.dumps(sensitive_items), json.dumps(sensitive_items)),
                )
                db.execute(
                    "UPDATE opportunities SET details_json = ?",
                    (json.dumps(sensitive_details),),
                )
                db.commit()

            payload = build_dashboard_data(database, prices)

        expected = [{
            "name": "Bat", "quantity": 1, "quantity_min": 1,
            "quantity_max": 1, "quantity_source": "structured",
        }]
        self.assertEqual(payload["opportunities"][0]["giveItems"], expected)
        self.assertEqual(payload["opportunities"][0]["receiveItems"], expected)
        self.assertEqual(payload["recentListings"][0]["giveItems"], expected)
        self.assertEqual(payload["recentListings"][0]["receiveItems"], expected)
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        for forbidden in ("supreme_value", "stability", "confidence", "rblxvalue_value", "raw_source_metadata"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("must-not-export", serialized)

    def test_public_cash_history_exports_listings_and_opportunities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices = root / "prices.json"
            prices.write_text(json.dumps(catalogue()), encoding="utf-8")
            database = root / "public-history.sqlite3"
            history = PublicDashboardHistory(database)
            listing = {
                "id": "stable-public-id",
                "source": "bloxswaps",
                "source_ad_id": "post-123",
                "url": "https://bloxswaps.com/trade-mm2?ad=post-123",
                "source_author": "PublicTrader",
                "give_items": [{"name": "Bat", "quantity": 1}],
                "receive_items": [{"name": "Candy", "quantity": 2}],
                "valuation_mode": "cash",
            }
            history.record_listings([listing])
            history.record_opportunity({
                "listing": listing,
                "cash_profit_worst": 2.5,
                "give_total": 10.0,
                "receive_total": 12.5,
                "margin": 25.0,
                "manual_review": False,
            })
            payload = build_dashboard_data(database, prices)

        self.assertTrue(payload["summary"]["historyAvailable"])
        self.assertEqual(payload["summary"]["trackedListings"], 1)
        self.assertEqual(payload["summary"]["opportunities7d"], 1)
        self.assertEqual(payload["opportunities"][0]["profit"], 2.5)
        self.assertEqual(payload["recentListings"][0]["owner"], "PublicTrader")
        self.assertEqual(payload["recentListings"][0]["giveItems"], [{"name": "Bat", "quantity": 1}])

if __name__ == "__main__":
    unittest.main()
