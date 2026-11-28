from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

import scanner
from test_support import IsolatedDataTestCase


class UncertainDeliveryStateTests(IsolatedDataTestCase):
    def test_reconciliation_persists_both_state_files_and_returns_quarantine_ids(self):
        uncertain = {"pending-1": 50_000.0}
        context = {"pending-1": {"source_ad_id": "ad-1"}}
        reconciliation = {
            "confirmed_quarantines_cleared": ["confirmed-1"],
            "orphan_contexts_removed": [],
        }
        output = io.StringIO()
        with (
            patch.object(scanner, "load_alerted", return_value=["sent-1"]),
            patch.object(scanner, "load_uncertain_state", return_value=(uncertain, {})),
            patch.object(scanner, "load_uncertain_context", return_value=context),
            patch.object(
                scanner,
                "reconcile_uncertain_quarantine",
                return_value=(uncertain, context, reconciliation),
            ),
            patch.object(scanner, "save_uncertain_state") as save_state,
            patch.object(scanner, "save_uncertain_context") as save_context,
            patch.object(scanner, "send_maintenance_alert") as alert,
            patch.object(scanner.time, "time", return_value=50_001.0),
            contextlib.redirect_stdout(output),
        ):
            alerted, alerted_set, uncertain_ids = scanner._load_uncertain_delivery_state()

        self.assertEqual(alerted, ["sent-1"])
        self.assertEqual(alerted_set, {"sent-1"})
        self.assertEqual(uncertain_ids, {"pending-1"})
        save_state.assert_called_once_with(uncertain)
        save_context.assert_called_once_with(context)
        alert.assert_not_called()
        self.assertIn("confirmed_quarantines_cleared=1", output.getvalue())

    def test_aged_uncertain_delivery_sends_manual_review_alert_without_resending(self):
        now = scanner.UNCERTAIN_REVIEW_AFTER_SECONDS + 100.0
        uncertain = {"pending-2": 100.0}
        context = {
            "pending-2": {
                "source_ad_id": "ad-2",
                "listing_url": "https://example.invalid/listing/2",
            }
        }
        with (
            patch.object(scanner, "load_alerted", return_value=[]),
            patch.object(scanner, "load_uncertain_state", return_value=(uncertain, {})),
            patch.object(scanner, "load_uncertain_context", return_value=context),
            patch.object(
                scanner,
                "reconcile_uncertain_quarantine",
                return_value=(
                    uncertain,
                    context,
                    {"confirmed_quarantines_cleared": [], "orphan_contexts_removed": []},
                ),
            ),
            patch.object(scanner, "send_maintenance_alert") as alert,
            patch.object(scanner.time, "time", return_value=now),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = scanner._load_uncertain_delivery_state()

        self.assertEqual(result, ([], set(), {"pending-2"}))
        alert.assert_called_once()
        args, kwargs = alert.call_args
        self.assertEqual(args[0], "telegram-quarantine-review")
        self.assertIn("No automatic resend will occur", args[1])
        self.assertIn("ad-2: https://example.invalid/listing/2", args[1])
        self.assertEqual(kwargs, {"cooldown_seconds": 86400})

    def test_empty_uncertain_state_does_not_send_review_alert(self):
        with (
            patch.object(scanner, "load_alerted", return_value=["sent-1"]),
            patch.object(scanner, "load_uncertain_state", return_value=({}, {})),
            patch.object(scanner, "load_uncertain_context", return_value={}),
            patch.object(
                scanner,
                "reconcile_uncertain_quarantine",
                return_value=(
                    {},
                    {},
                    {"confirmed_quarantines_cleared": [], "orphan_contexts_removed": []},
                ),
            ),
            patch.object(scanner, "send_maintenance_alert") as alert,
            patch.object(scanner.time, "time", return_value=10_000.0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = scanner._load_uncertain_delivery_state()

        self.assertEqual(result, (["sent-1"], {"sent-1"}, set()))
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
