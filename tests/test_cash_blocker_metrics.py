import json
import tempfile
import unittest
from test_support import IsolatedDataTestCase
from datetime import datetime, timezone
from pathlib import Path

from tools.metrics import update_metrics


class CashBlockerMetricsTests(IsolatedDataTestCase):
    def test_blockers_increment_once_by_normalized_item_and_retain_14_days(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            now = datetime(2026, 11, 14, tzinfo=timezone.utc)
            update_metrics(
                path, cash_blockers={"spirit": 1, "bat": 1},
                cash_blocker_groups={"bat|spirit": 1}, now=now,
            )
            update_metrics(
                path, cash_blockers={"spirit": 2},
                cash_blocker_groups={"spirit": 1}, now=now,
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            row = data["2026-11-14"]
            self.assertEqual(row["cash_blockers"], {"bat": 1, "spirit": 3})
            self.assertEqual(row["cash_blocker_groups"], {"bat|spirit": 1, "spirit": 1})
            self.assertFalse(path.with_name("metrics.json.tmp").exists())

    def test_predictable_temp_symlink_cannot_overwrite_another_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "metrics.json"
            sentinel = root / "sentinel.txt"
            predictable_temp = root / "metrics.json.tmp"
            sentinel.write_text("must remain unchanged", encoding="utf-8")
            predictable_temp.symlink_to(sentinel)

            update_metrics(path, increments={"scans": 1})

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must remain unchanged")
            self.assertTrue(predictable_temp.is_symlink())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["2026-11-27"]["scans"], 1)

    def test_invalid_blocker_names_and_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            with self.assertRaises(ValueError):
                update_metrics(path, cash_blockers={"Bad Name": 1})
            with self.assertRaises(ValueError):
                update_metrics(path, cash_blockers={"spirit": True})


if __name__ == "__main__":
    unittest.main()
