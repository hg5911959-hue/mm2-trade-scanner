"""Regression tests for safe scanner state-file replacement."""
import tempfile
import unittest
from pathlib import Path

from test_support import IsolatedDataTestCase
from scanner import atomic_write_text


class ScannerAtomicWriteTests(IsolatedDataTestCase):
    def test_atomic_write_replaces_target_and_cleans_its_unique_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state.json"
            atomic_write_text(target, '{"safe": true}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"safe": true}')
            self.assertEqual(list(root.glob("state.json.*.tmp")), [])

    def test_atomic_write_does_not_follow_a_predictable_temp_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state.json"
            sentinel = root / "outside.txt"
            predictable_temp = root / "state.json.tmp"
            sentinel.write_text("must remain unchanged", encoding="utf-8")
            predictable_temp.symlink_to(sentinel)

            atomic_write_text(target, '{"safe": true}')

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must remain unchanged")
            self.assertEqual(target.read_text(encoding="utf-8"), '{"safe": true}')
            self.assertTrue(predictable_temp.is_symlink())
            self.assertEqual(list(root.glob("state.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
