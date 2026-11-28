from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import scanner
from test_support import IsolatedDataTestCase
from tools.scan_outcomes import ScanOutcomeLedger


class DigestDeliveryStageTests(IsolatedDataTestCase):
    def _op(self, listing_id):
        return {"listing": {"id": listing_id, "source_ad_id": f"ad-{listing_id}"}}

    def _deliver(self, items, ledger, *, alerted=None, uncertain=None, deliver=None):
        return scanner._deliver_digest(
            items,
            watch_state={},
            values={},
            aliases={},
            cash_catalog={},
            alerted_set=set(alerted or ()),
            uncertain_ids=uncertain if uncertain is not None else set(),
            outcome_ledger=ledger,
            market_info={},
            mark_seen_ad=Mock(),
            mark_pending=Mock(),
            deliver_opportunity=deliver or Mock(),
        )

    def test_already_alerted_and_quarantined_items_are_filtered_before_sending(self):
        items = [self._op("alerted"), self._op("uncertain")]
        ledger = ScanOutcomeLedger(["alerted", "uncertain"])
        with (
            patch.object(scanner, "listing_watch_fingerprints", return_value=("content", "values")),
            patch.object(scanner, "send_telegram") as send,
            patch.dict(os.environ, {"DRY_RUN": "false"}),
        ):
            delivered = self._deliver(
                items,
                ledger,
                alerted={"alerted"},
                uncertain={"uncertain"},
            )

        self.assertEqual(delivered, 0)
        self.assertEqual(
            ledger.finalize(),
            {"delivery_guard_skipped": 1, "delivery_quarantined": 1},
        )
        send.assert_not_called()

    def test_dry_run_records_preview_without_preparing_or_sending_items(self):
        item = self._op("preview")
        ledger = ScanOutcomeLedger(["preview"])
        with (
            patch.object(scanner, "listing_watch_fingerprints", return_value=("content", "values")),
            patch.object(scanner, "deliver_digest_items_individually") as deliver_items,
            patch.dict(os.environ, {"DRY_RUN": "true"}),
        ):
            delivered = self._deliver([item], ledger)

        self.assertEqual(delivered, 0)
        self.assertEqual(ledger.finalize(), {"dry_run_preview": 1})
        deliver_items.assert_not_called()

    def test_uncertain_digest_delivery_remains_quarantined_and_is_not_resent(self):
        item = self._op("uncertain")
        ledger = ScanOutcomeLedger(["uncertain"])
        uncertain_ids = set()
        seen = Mock()
        deliver_opportunity = Mock()

        def deliver_items(eligible, prepare, sender):
            for op in eligible:
                prepare(op)
            return [("uncertain", item, TimeoutError("network timeout"))]

        with (
            patch.object(scanner, "listing_watch_fingerprints", return_value=("content", "values")),
            patch.object(scanner, "deliver_digest_items_individually", side_effect=deliver_items),
            patch.object(scanner, "record_uncertain_context") as record_context,
            patch.object(scanner, "save_uncertain_ids") as save_ids,
            patch.object(scanner, "clear_uncertain_context") as clear_context,
            patch.object(scanner, "update_metrics") as metrics,
            patch.dict(os.environ, {"DRY_RUN": "false"}),
        ):
            delivered = scanner._deliver_digest(
                [item],
                watch_state={},
                values={},
                aliases={},
                cash_catalog={},
                alerted_set=set(),
                uncertain_ids=uncertain_ids,
                outcome_ledger=ledger,
                market_info={},
                mark_seen_ad=seen,
                mark_pending=Mock(),
                deliver_opportunity=deliver_opportunity,
            )

        self.assertEqual(delivered, 0)
        self.assertEqual(uncertain_ids, {"uncertain"})
        self.assertEqual(ledger.finalize(), {"delivery_quarantined": 1})
        record_context.assert_called_once()
        self.assertEqual(save_ids.call_count, 2)
        seen.assert_called_once_with(item["listing"])
        deliver_opportunity.assert_not_called()
        clear_context.assert_not_called()
        metrics.assert_not_called()

    def test_definite_failure_is_queued_and_quarantine_is_cleared(self):
        item = self._op("failed")
        ledger = ScanOutcomeLedger(["failed"])
        uncertain_ids = set()
        mark_pending = Mock()

        def deliver_items(eligible, prepare, sender):
            for op in eligible:
                prepare(op)
            return [("failed", item, RuntimeError("HTTP 500"))]

        with (
            patch.object(scanner, "listing_watch_fingerprints", return_value=("content", "values")),
            patch.object(scanner, "deliver_digest_items_individually", side_effect=deliver_items),
            patch.object(scanner, "record_uncertain_context"),
            patch.object(scanner, "save_uncertain_ids") as save_ids,
            patch.object(scanner, "clear_uncertain_context") as clear_context,
            patch.dict(os.environ, {"DRY_RUN": "false"}),
        ):
            delivered = scanner._deliver_digest(
                [item],
                watch_state={},
                values={},
                aliases={},
                cash_catalog={},
                alerted_set=set(),
                uncertain_ids=uncertain_ids,
                outcome_ledger=ledger,
                market_info={},
                mark_seen_ad=Mock(),
                mark_pending=mark_pending,
                deliver_opportunity=Mock(),
            )

        self.assertEqual(delivered, 0)
        self.assertEqual(uncertain_ids, set())
        self.assertEqual(ledger.finalize(), {"delivery_failed_queued": 1})
        self.assertEqual(save_ids.call_count, 2)
        clear_context.assert_called_once_with("failed")
        mark_pending.assert_called_once_with(item["listing"], reason="telegram_failure")

    def test_confirmed_digest_delivery_finalizes_and_updates_digest_metrics(self):
        item = self._op("sent")
        ledger = ScanOutcomeLedger(["sent"])
        uncertain_ids = set()
        delivery = Mock(
            side_effect=lambda op, **kwargs: ledger.record(op["listing"]["id"], "alert_sent")
        )

        def deliver_items(eligible, prepare, sender):
            for op in eligible:
                prepare(op)
            return [("sent", item, None)]

        with (
            patch.object(scanner, "listing_watch_fingerprints", return_value=("content", "values")),
            patch.object(scanner, "deliver_digest_items_individually", side_effect=deliver_items),
            patch.object(scanner, "record_uncertain_context"),
            patch.object(scanner, "save_uncertain_ids"),
            patch.object(scanner, "update_metrics") as metrics,
            patch.object(scanner, "_SESSION_ALERTS_SENT", 0),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
        ):
            delivered = scanner._deliver_digest(
                [item],
                watch_state={},
                values={},
                aliases={},
                cash_catalog={},
                alerted_set=set(),
                uncertain_ids=uncertain_ids,
                outcome_ledger=ledger,
                market_info={},
                mark_seen_ad=Mock(),
                mark_pending=Mock(),
                deliver_opportunity=delivery,
            )

        self.assertEqual(delivered, 1)
        self.assertEqual(uncertain_ids, set())
        self.assertEqual(ledger.finalize(), {"alert_sent": 1})
        delivery.assert_called_once_with(item, send=False, delivery_confirmed=True)
        metrics.assert_called_once_with(
            increments={"alerts_digest_messages": 1, "alerts_digest_items": 1}
        )


if __name__ == "__main__":
    unittest.main()
