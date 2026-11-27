import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import IsolatedDataTestCase
import scanner


class MaintenanceAlertCooldownTests(IsolatedDataTestCase):
    def _run_alert(self, error):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance_alerts.json"
            with (
                patch.object(scanner, "MAINTENANCE_FILE", path),
                patch.object(scanner.time, "time", return_value=1_000.0),
                patch.object(scanner, "send_telegram", side_effect=error),
            ):
                result = scanner.send_maintenance_alert("test-alert", "test message")
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            return result, state

    def test_definite_telegram_rejection_releases_cooldown_for_next_cycle(self):
        result, state = self._run_alert(RuntimeError("Telegram explicitly rejected request"))
        self.assertFalse(result)
        self.assertNotIn("test-alert", state)

    def test_uncertain_telegram_delivery_retains_cooldown_to_prevent_duplicate(self):
        result, state = self._run_alert(
            scanner.TelegramDeliveryUncertain("response lost after request")
        )
        self.assertFalse(result)
        self.assertEqual(state["test-alert"], 1_000.0)

    def test_successful_telegram_delivery_keeps_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance_alerts.json"
            with (
                patch.object(scanner, "MAINTENANCE_FILE", path),
                patch.object(scanner.time, "time", return_value=1_000.0),
                patch.object(scanner, "send_telegram"),
            ):
                self.assertTrue(scanner.send_maintenance_alert("test-alert", "test message"))
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(state["test-alert"], 1_000.0)


if __name__ == "__main__":
    unittest.main()
