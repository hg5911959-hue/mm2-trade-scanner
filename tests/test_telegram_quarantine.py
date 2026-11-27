import unittest

from test_support import IsolatedDataTestCase
from tools.telegram_quarantine import reconcile_uncertain_quarantine


class TelegramQuarantineReconciliationTests(IsolatedDataTestCase):
    def test_only_confirmed_alerts_are_removed_and_orphan_context_is_pruned(self):
        state = {
            "confirmed-a": 100.0,
            "unresolved-b": 200.0,
            "confirmed-c": 300.0,
        }
        context = {
            "confirmed-a": {"source_ad_id": "post-a"},
            "unresolved-b": {"source_ad_id": "post-b"},
            "orphan-old": {"source_ad_id": "post-old"},
        }

        next_state, next_context, report = reconcile_uncertain_quarantine(
            state, {"confirmed-a", "confirmed-c"}, context
        )

        self.assertEqual(next_state, {"unresolved-b": 200.0})
        self.assertEqual(next_context, {"unresolved-b": {"source_ad_id": "post-b"}})
        self.assertEqual(
            report["confirmed_quarantines_cleared"],
            ["confirmed-a", "confirmed-c"],
        )
        self.assertEqual(report["orphan_contexts_removed"], ["orphan-old"])

    def test_missing_context_does_not_clear_an_unresolved_delivery(self):
        state = {"no-context": 123.0}
        next_state, next_context, report = reconcile_uncertain_quarantine(
            state, set(), {}
        )
        self.assertEqual(next_state, state)
        self.assertEqual(next_context, {})
        self.assertEqual(report["confirmed_quarantines_cleared"], [])
        self.assertEqual(report["orphan_contexts_removed"], [])

    def test_context_for_live_unresolved_delivery_is_preserved(self):
        context = {"live": {"source_ad_id": "post-live"}}
        state, details, report = reconcile_uncertain_quarantine(
            {"live": 42.0}, set(), context
        )
        self.assertEqual(state, {"live": 42.0})
        self.assertEqual(details, context)
        self.assertEqual(report["orphan_contexts_removed"], [])


if __name__ == "__main__":
    unittest.main()
