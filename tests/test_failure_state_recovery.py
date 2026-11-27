import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase
import scanner


class FailureStateRecoveryTests(IsolatedDataTestCase):
    def test_corrupt_or_wrong_shaped_failure_checkpoint_does_not_break_failure_handler(self):
        invalid_payloads = ["[]", "null", '"bad"', '{"consecutive_failures": "oops"}',
                            '{"consecutive_failures": true}', '{"consecutive_failures": -1}']
        for payload in invalid_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "failure_state.json"
                path.write_text(payload, encoding="utf-8")
                with (
                    patch.object(scanner, "FAILURE_FILE", path),
                    patch.object(scanner, "FAILURE_THRESHOLD", 999),
                    patch.object(scanner, "update_metrics") as metrics,
                    patch.dict("os.environ", {"DRY_RUN": "true"}),
                ):
                    scanner.record_failure(RuntimeError("fixture failure"))
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["consecutive_failures"], 1)
                metrics.assert_called_once_with(increments={"scan_failures": 1})

    def test_valid_failure_checkpoint_increments_existing_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failure_state.json"
            path.write_text(json.dumps({
                "consecutive_failures": 3,
                "last_success_at_utc": "2026-11-27T00:00:00+00:00",
            }), encoding="utf-8")
            with (
                patch.object(scanner, "FAILURE_FILE", path),
                patch.object(scanner, "FAILURE_THRESHOLD", 999),
                patch.object(scanner, "update_metrics"),
                patch.dict("os.environ", {"DRY_RUN": "true"}),
            ):
                scanner.record_failure(RuntimeError("fixture failure"))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["consecutive_failures"], 4)
            self.assertEqual(saved["last_success_at_utc"], "2026-11-27T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
