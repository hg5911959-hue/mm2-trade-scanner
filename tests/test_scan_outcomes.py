import unittest

from test_support import IsolatedDataTestCase
from tools.scan_outcomes import ScanOutcomeLedger


class ScanOutcomeLedgerTests(IsolatedDataTestCase):
    def test_every_listing_must_have_one_terminal_outcome(self):
        ledger = ScanOutcomeLedger(["a", "b", "c"])
        ledger.record("a", "already_alerted")
        ledger.record("b", "qualified_pending_delivery")
        ledger.record("b", "alert_sent")
        ledger.record("c", "watch_unmapped")
        self.assertEqual(
            ledger.finalize(),
            {"alert_sent": 1, "already_alerted": 1, "watch_unmapped": 1},
        )

    def test_finalize_rejects_missing_or_pending_delivery_outcomes(self):
        ledger = ScanOutcomeLedger(["a", "b"])
        ledger.record("a", "qualified_pending_delivery")
        with self.assertRaisesRegex(RuntimeError, "missing=1 pending_delivery=1"):
            ledger.finalize()

    def test_rejects_duplicate_terminal_outcome(self):
        ledger = ScanOutcomeLedger(["a"])
        ledger.record("a", "already_alerted")
        with self.assertRaisesRegex(RuntimeError, "already has outcome"):
            ledger.record("a", "watch_unchanged")

    def test_only_qualified_delivery_may_transition_from_pending(self):
        ledger = ScanOutcomeLedger(["a"])
        ledger.record("a", "qualified_pending_delivery")
        with self.assertRaisesRegex(RuntimeError, "Invalid or duplicate outcome"):
            ledger.record("a", "qualified_pending_delivery")
        with self.assertRaisesRegex(RuntimeError, "already has outcome"):
            ledger.record("a", "already_alerted")

    def test_rejects_unknown_listing_ids_and_empty_ids(self):
        with self.assertRaisesRegex(RuntimeError, "no stable ID"):
            ScanOutcomeLedger([""])
        ledger = ScanOutcomeLedger(["a"])
        with self.assertRaisesRegex(RuntimeError, "unknown listing ID"):
            ledger.record("other", "alert_sent")


if __name__ == "__main__":
    unittest.main()
