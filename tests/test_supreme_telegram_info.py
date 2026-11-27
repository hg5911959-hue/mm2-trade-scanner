from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import scanner
from test_support import IsolatedDataTestCase
from tools.supreme_telegram_info import load_telegram_market_info


def make_snapshot():
    rows = []
    for index in range(214):
        rows.append({
            "bloxswaps_slug": f"weapon-{index}",
            "robase_name": f"Weapon {index}",
            "value_source_b": 160 + index,
            "stability": "Stable" if index != 1 else None,
            "status": "matched" if index < 158 else "matched_missing_blended_value",
        })
    return {
        "schema_version": 1,
        "source": "RBLXValue official API v2",
        "source_fields": {
            "value_source_b": "Supreme Values (exclusive trade-price source)",
            "stability": "RBLXValue",
        },
        "snapshot_date": "2026-11-27",
        "fetched_at_utc": "2026-11-27T05:12:48+00:00",
        "expected_weapons": 214,
        "items": rows,
    }


class SupremeTelegramInfoTests(IsolatedDataTestCase):
    def write_snapshot(self, snapshot):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "rblxvalue_latest.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def test_loads_supreme_value_and_rblxvalue_stability_by_exact_slug(self):
        snapshot = make_snapshot()
        mapping, summary = load_telegram_market_info(
            self.write_snapshot(snapshot),
            now=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(mapping["weapon1"]["supreme_value"], 161)
        self.assertIsNone(mapping["weapon1"]["stability"])
        self.assertEqual(mapping["weapon0"]["stability"], "Stable")
        self.assertEqual(summary["with_supreme_value"], 214)
        self.assertEqual(summary["with_stability"], 213)

    def test_stale_or_future_snapshot_is_rejected(self):
        snapshot = make_snapshot()
        for fetched in ("2026-11-25T00:00:00+00:00", "2026-11-28T00:00:00+00:00"):
            snapshot["fetched_at_utc"] = fetched
            with self.subTest(fetched=fetched), self.assertRaisesRegex(ValueError, "stale or future-dated"):
                load_telegram_market_info(
                    self.write_snapshot(snapshot),
                    now=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
                )

    def test_wrong_source_attribution_is_rejected(self):
        snapshot = make_snapshot()
        snapshot["source_fields"]["stability"] = "Supreme Values"
        with self.assertRaisesRegex(ValueError, "Stability field source"):
            load_telegram_market_info(
                self.write_snapshot(snapshot),
                now=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
            )

    def test_duplicate_slug_is_rejected(self):
        snapshot = make_snapshot()
        snapshot["items"][1]["bloxswaps_slug"] = snapshot["items"][0]["bloxswaps_slug"]
        with self.assertRaisesRegex(ValueError, "duplicate BloxSwaps slug"):
            load_telegram_market_info(
                self.write_snapshot(snapshot),
                now=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
            )

    def test_telegram_market_info_is_display_only_and_exact_slug_matched(self):
        listing = {
            "source": "bloxswaps",
            "valuation_mode": "cash",
            "give_items": [{"name": "Weapon 0", "catalog_slug": "weapon-0", "quantity": 1}],
            "receive_items": [{"name": "Weapon 1", "catalog_slug": "weapon-1", "quantity": 1}],
        }
        market_info = {
            "weapon0": {"name": "Weapon 0", "supreme_value": 160, "stability": "Stable"},
            "weapon1": {"name": "Weapon 1", "supreme_value": 161, "stability": None},
        }
        message = scanner.format_trade_message(
            listing, 2.5, ["Weapon 0 x1 — $2.50 each = $2.50"],
            5.0, ["Weapon 1 x1 — $5.00 each = $5.00"], 2.5,
            market_info=market_info,
        )
        self.assertIn("\n\n┌─ 📊 <b>REFERENCE INFO ONLY</b>", message)
        self.assertNotIn("\n\n\n┌─ 📊", message)
        self.assertIn("└ <i>RBLXValue · not used in cash filters or alert decisions</i>", message)
        self.assertIn("Supreme <code>160</code>", message)
        self.assertIn("Stability <code>Stable</code>", message)
        self.assertIn("Stability <code>N/A</code>", message)
        self.assertIn("EST. CASH PROFIT (before fees/slippage): +$2.5", message)
        self.assertNotIn("weapon-2", message)
        self.assertNotIn("RBLXValue/Supreme", message)

    def test_missing_optional_market_info_does_not_change_cash_message(self):
        listing = {"source": "bloxswaps", "valuation_mode": "cash"}
        message = scanner.format_trade_message(
            listing, 2.5, ["Weapon 0 x1 — $2.50 each = $2.50"],
            5.0, ["Weapon 1 x1 — $5.00 each = $5.00"], 2.5,
        )
        self.assertNotIn("REFERENCE INFO ONLY", message)
        self.assertIn("EST. CASH PROFIT (before fees/slippage): +$2.5", message)


if __name__ == "__main__":
    unittest.main()
