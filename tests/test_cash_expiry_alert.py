import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner


class CashExpiryAlertTests(unittest.TestCase):
    def test_runway_notice_triggers_at_three_day_boundary_and_lists_counts_only(self):
        with patch.object(scanner, "send_maintenance_alert") as alert:
            scanner.alert_cash_price_runway({"expiring_within_3_days": 1}, 8)
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], "cash-price-runway")
        self.assertIn("verified rows=8", alert.call_args.args[1])
        self.assertIn("expiring within 3 days=1", alert.call_args.args[1])
        self.assertNotIn("Test Item", alert.call_args.args[1])
        self.assertEqual(alert.call_args.kwargs["cooldown_seconds"], 86400)

    def test_runway_warning_when_below_eight_rows_but_not_at_eight(self):
        with patch.object(scanner, "send_maintenance_alert") as alert:
            scanner.alert_cash_price_runway({"expiring_within_3_days": 0}, 7)
            scanner.alert_cash_price_runway({"expiring_within_3_days": 0}, 8)
        alert.assert_called_once()
        self.assertIn("verified rows=7", alert.call_args.args[1])

    def test_expiry_notice_is_sent_once_per_24_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "maintenance.json"
            rows = [
                {"name": f"Test Item {i}", "verified_at_utc": f"2026-10-{i:02d}T00:00:00Z"}
                for i in range(1, 8)
            ]
            with (
                patch.object(scanner, "MAINTENANCE_FILE", state),
                patch.object(scanner, "send_telegram") as send,
                patch("scanner.time.time", return_value=1_800_000_000),
            ):
                scanner.alert_expired_cash_prices(rows, 14)
                scanner.alert_expired_cash_prices(rows, 14)
            send.assert_called_once()
            message = send.call_args.args[0]
            self.assertIn("7 verified cash prices have expired", message)
            self.assertIn("Test Item 1", message)
            self.assertNotIn("Test Item 6", message)
            self.assertEqual(json.loads(state.read_text())["cash-price-expired"], 1_800_000_000)


if __name__ == "__main__":
    unittest.main()
