import json
import tempfile
import unittest
from pathlib import Path

from tools.rblxvalue_value_profit_shadow import load_values_from_snapshot


def sample_row(index, **overrides):
    row = {
        "snapshot_date": "2026-11-25",
        "robase_slug": f"weapon-{index}",
        "robase_name": f"Weapon {index}",
        "bloxswaps_slug": f"weapon-{index}",
        "item_type": "knife",
        "item_rarity": "godly",
        "is_chroma": False,
        "rblxvalue_slug": f"rblx-weapon-{index}",
        "rblxvalue_name": f"Weapon {index}",
        "category": "godly",
        "api_type": "knife",
        "value": 999,
        "value_source_a": 888,
        "value_source_b": 123,
        "stability": "Stable",
        "demand": 5,
        "rarity": 4,
        "range": None,
        "status": "matched",
        "item_last_updated": "2026-11-25 12:02:35",
    }
    row.update(overrides)
    return row


def snapshot(rows):
    return {
        "schema_version": 1,
        "source": "RBLXValue official API v2",
        "source_fields": {
            "value": "RBLXValue blended estimate",
            "value_source_a": "MM2Values",
            "value_source_b": "Supreme Values",
        },
        "snapshot_date": "2026-11-25",
        "dataset_last_updated": "2026-11-25 12:02:35",
        "expected_weapons": 214,
        "items": rows,
    }


class RblxValueValueProfitShadowTests(unittest.TestCase):
    def write_snapshot(self, data):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "snapshot.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_uses_supreme_source_b_only_and_exact_bloxswaps_slug(self):
        data = snapshot([sample_row(i) for i in range(214)])
        values, summary = load_values_from_snapshot(self.write_snapshot(data), today="2026-11-25")
        variant = values["weapon 0"]["variants"][0]
        self.assertEqual(variant["value"], 123)
        self.assertEqual(variant["raw_source_value"], 123)
        self.assertEqual(variant["secondary_value"], 888)
        self.assertEqual(variant["rblxvalue_value"], 999)
        self.assertEqual(variant["slug"], "weapon-0")
        self.assertEqual(summary["supreme_numeric"], 214)
        self.assertEqual(summary["api_requests"], 0)

    def test_missing_supreme_value_never_falls_back_to_other_sources(self):
        rows = [sample_row(i) for i in range(214)]
        rows[0].update({"value_source_b": None, "value": 500, "value_source_a": 400})
        values, summary = load_values_from_snapshot(self.write_snapshot(snapshot(rows)), today="2026-11-25")
        self.assertIsNone(values["weapon 0"]["variants"][0]["value"])
        self.assertEqual(summary["supreme_numeric"], 213)

    def test_missing_stability_is_preserved_as_missing(self):
        rows = [sample_row(i) for i in range(214)]
        rows[0]["stability"] = None
        values, summary = load_values_from_snapshot(self.write_snapshot(snapshot(rows)), today="2026-11-25")
        self.assertIsNone(values["weapon 0"]["variants"][0]["stability"])
        self.assertEqual(summary["stability_available"], 213)

    def test_duplicate_names_keep_separate_exact_variants(self):
        rows = [sample_row(i) for i in range(214)]
        rows[1].update({"robase_name": "Weapon 0", "rblxvalue_name": "Weapon 0", "bloxswaps_slug": "other-weapon"})
        values, _ = load_values_from_snapshot(self.write_snapshot(snapshot(rows)), today="2026-11-25")
        self.assertEqual(len(values["weapon 0"]["variants"]), 2)
        self.assertIsNone(values["weapon 0"]["value"])

    def test_wrong_snapshot_date_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "not today's"):
            load_values_from_snapshot(self.write_snapshot(snapshot([sample_row(i) for i in range(214)])), today="2026-11-26")

    def test_duplicate_identity_fails_closed(self):
        rows = [sample_row(i) for i in range(214)]
        rows[1]["robase_slug"] = rows[0]["robase_slug"]
        with self.assertRaisesRegex(ValueError, "duplicate ROBASE"):
            load_values_from_snapshot(self.write_snapshot(snapshot(rows)), today="2026-11-25")

    def test_type_mismatch_fails_closed(self):
        rows = [sample_row(i) for i in range(214)]
        rows[0]["api_type"] = "gun"
        with self.assertRaisesRegex(ValueError, "type/category mismatch"):
            load_values_from_snapshot(self.write_snapshot(snapshot(rows)), today="2026-11-25")

    def test_incomplete_snapshot_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly 214"):
            load_values_from_snapshot(self.write_snapshot(snapshot([sample_row(i) for i in range(213)])), today="2026-11-25")

if __name__ == "__main__":
    unittest.main()
