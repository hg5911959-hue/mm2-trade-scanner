from __future__ import annotations

import unittest
from unittest.mock import Mock

from test_support import IsolatedDataTestCase

from tools.scan_state_actions import (
    mark_alerted_state,
    mark_pending_state,
    mark_seen_ad_state,
)


class ScanStateActionTests(IsolatedDataTestCase):
    def test_mark_alerted_persists_only_the_first_transition(self):
        alerted = []
        alerted_set = set()
        save = Mock()

        mark_alerted_state("trade-1", alerted=alerted, alerted_set=alerted_set, save_alerted=save)
        mark_alerted_state("trade-1", alerted=alerted, alerted_set=alerted_set, save_alerted=save)

        self.assertEqual(alerted, ["trade-1"])
        self.assertEqual(alerted_set, {"trade-1"})
        save.assert_called_once_with(["trade-1"])

    def test_mark_pending_ignores_discord_source(self):
        pending_ids = {}
        pending_schedule = {}
        seen_ads = set()
        save_seen = Mock()
        save_schedule = Mock()
        save_pending = Mock()

        mark_pending_state(
            {"source": "discord", "source_ad_id": "discord-1"},
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            seen_ads=seen_ads,
            save_seen_ads=save_seen,
            save_pending_recheck_schedule=save_schedule,
            save_pending_ids=save_pending,
            priority_pending_reasons=("telegram_failure",),
            pending_recheck_backoff_seconds=(30, 60),
            now=100,
        )

        self.assertEqual(pending_ids, {})
        self.assertEqual(pending_schedule, {})
        save_seen.assert_not_called()
        save_schedule.assert_not_called()
        save_pending.assert_not_called()

    def test_priority_pending_reason_requeues_seen_ad_immediately(self):
        pending_ids = {}
        pending_schedule = {"ad-1": {"attempts": 2, "next_check_at": 500, "reason": "old"}}
        seen_ads = {"ad-1"}
        save_seen = Mock()
        save_schedule = Mock()
        save_pending = Mock()

        mark_pending_state(
            {"source": "bloxswaps", "source_ad_id": "ad-1"},
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            seen_ads=seen_ads,
            save_seen_ads=save_seen,
            save_pending_recheck_schedule=save_schedule,
            save_pending_ids=save_pending,
            priority_pending_reasons=("telegram_failure",),
            pending_recheck_backoff_seconds=(30, 60),
            reason="telegram_failure",
            now=100,
        )

        self.assertEqual(pending_ids, {"ad-1": 100})
        self.assertEqual(
            pending_schedule["ad-1"],
            {"attempts": 2, "next_check_at": 100, "reason": "telegram_failure"},
        )
        self.assertEqual(seen_ads, set())
        save_seen.assert_called_once_with(set())
        save_schedule.assert_called_once_with(pending_schedule)
        save_pending.assert_called_once_with(pending_ids)

    def test_normal_pending_reason_preserves_existing_backoff_for_same_reason(self):
        pending_ids = {}
        pending_schedule = {"ad-1": {"attempts": 3, "next_check_at": 250, "reason": "not_found"}}
        seen_ads = set()
        save_seen = Mock()
        save_schedule = Mock()
        save_pending = Mock()

        mark_pending_state(
            {"source": "bloxswaps", "source_ad_id": "ad-1"},
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            seen_ads=seen_ads,
            save_seen_ads=save_seen,
            save_pending_recheck_schedule=save_schedule,
            save_pending_ids=save_pending,
            priority_pending_reasons=("telegram_failure",),
            pending_recheck_backoff_seconds=(30, 60),
            reason="not_found",
            now=100,
        )

        self.assertEqual(pending_ids, {"ad-1": 100})
        self.assertEqual(
            pending_schedule["ad-1"],
            {"attempts": 3, "next_check_at": 250.0, "reason": "not_found"},
        )
        save_seen.assert_not_called()
        save_schedule.assert_called_once_with(pending_schedule)
        save_pending.assert_called_once_with(pending_ids)

    def test_mark_seen_ad_adds_id_and_clears_pending_state(self):
        seen_ads = set()
        pending_ids = {"ad-1": 10}
        pending_schedule = {"ad-1": {"attempts": 1, "next_check_at": 20, "reason": "not_found"}}
        save_seen = Mock()
        save_pending = Mock()
        save_schedule = Mock()

        mark_seen_ad_state(
            {"source": "bloxswaps", "source_ad_id": "ad-1"},
            seen_ads=seen_ads,
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            save_seen_ads=save_seen,
            save_pending_ids=save_pending,
            save_pending_recheck_schedule=save_schedule,
        )

        self.assertEqual(seen_ads, {"ad-1"})
        self.assertEqual(pending_ids, {})
        self.assertEqual(pending_schedule, {})
        save_seen.assert_called_once_with({"ad-1"})
        save_pending.assert_called_once_with({})
        save_schedule.assert_called_once_with({})

    def test_mark_seen_ad_can_keep_pending_state(self):
        seen_ads = set()
        pending_ids = {"ad-1": 10}
        pending_schedule = {"ad-1": {"attempts": 1, "next_check_at": 20, "reason": "missing_price"}}
        save_seen = Mock()
        save_pending = Mock()
        save_schedule = Mock()

        mark_seen_ad_state(
            {"source": "bloxswaps", "source_ad_id": "ad-1"},
            seen_ads=seen_ads,
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            save_seen_ads=save_seen,
            save_pending_ids=save_pending,
            save_pending_recheck_schedule=save_schedule,
            clear_pending=False,
        )

        self.assertEqual(seen_ads, {"ad-1"})
        self.assertEqual(set(pending_ids), {"ad-1"})
        self.assertEqual(set(pending_schedule), {"ad-1"})
        save_seen.assert_called_once_with({"ad-1"})
        save_pending.assert_not_called()
        save_schedule.assert_not_called()

    def test_mark_seen_ad_ignores_discord_source(self):
        seen_ads = set()
        pending_ids = {}
        pending_schedule = {}
        save_seen = Mock()
        save_pending = Mock()
        save_schedule = Mock()

        mark_seen_ad_state(
            {"source": "discord", "source_ad_id": "discord-1"},
            seen_ads=seen_ads,
            pending_ids=pending_ids,
            pending_schedule=pending_schedule,
            save_seen_ads=save_seen,
            save_pending_ids=save_pending,
            save_pending_recheck_schedule=save_schedule,
        )

        self.assertEqual(seen_ads, set())
        save_seen.assert_not_called()
        save_pending.assert_not_called()
        save_schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
