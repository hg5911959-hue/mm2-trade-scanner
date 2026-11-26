import unittest
import json
import tempfile
from pathlib import Path

from test_support import IsolatedDataTestCase
from tools.export_dashboard import _robase_dashboard_payload


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
        self.assertEqual(summary["totalSold7d"], 12)
        self.assertEqual(rows[0]["priceUsd"], 2.8)
        self.assertEqual(rows[0]["sold7d"], 12)
        self.assertIsNone(rows[1]["priceUsd"])
        self.assertEqual(rows[1]["sold7d"], 0)
        self.assertNotIn("sold30d", rows[0])
        self.assertNotIn("totalSold30d", summary)
        self.assertEqual(rows[0]["priceBasis"], "chromas.gg Price (7d)")

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


if __name__ == "__main__":
    unittest.main()
