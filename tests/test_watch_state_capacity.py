import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase
import scanner


class WatchStateCapacityTests(IsolatedDataTestCase):
    def test_watch_capacity_is_aligned_with_seen_listing_capacity(self):
        self.assertEqual(scanner.MAX_WATCHED_ADS, scanner.MAX_SEEN_ADS)

    def test_save_watch_state_fails_closed_instead_of_evicting_old_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch_ads.json"
            original = {"old-a": {"created_at": 100.0, "updated_at": 100.0}}
            path.write_text(json.dumps({"version": 1, "ads": original}), encoding="utf-8")
            with patch.object(scanner, "WATCH_FILE", path), patch.object(scanner, "MAX_WATCHED_ADS", 2):
                with self.assertRaisesRegex(RuntimeError, "refusing to evict unresolved listings"):
                    scanner.save_watch_state({
                        "old-a": {"created_at": 100.0, "updated_at": 100.0},
                        "old-b": {"created_at": 100.0, "updated_at": 101.0},
                        "new-c": {"created_at": 100.0, "updated_at": 102.0},
                    })
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["ads"], original)

    def test_load_watch_state_fails_closed_instead_of_trimming_existing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch_ads.json"
            entries = {
                f"ad-{index}": {"created_at": 1_000_000_000.0, "updated_at": 1_000_000_000.0 + index}
                for index in range(3)
            }
            path.write_text(json.dumps({"version": 1, "ads": entries}), encoding="utf-8")
            with patch.object(scanner, "WATCH_FILE", path), patch.object(scanner, "MAX_WATCHED_ADS", 2), patch.object(scanner.time, "time", return_value=1_000_000_100.0):
                with self.assertRaisesRegex(RuntimeError, "refusing to evict unresolved listings"):
                    scanner.load_watch_state()
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(stored["ads"]), set(entries))


if __name__ == "__main__":
    unittest.main()
