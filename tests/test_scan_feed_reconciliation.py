from __future__ import annotations

import unittest
from unittest.mock import patch

from test_support import IsolatedDataTestCase

import scanner


class DiscordFeedMergeTests(IsolatedDataTestCase):
    def test_cash_mode_never_loads_or_merges_discord_listings(self):
        listings = [{"id": "blox-1"}]
        with patch("discord_parser.load_discord_listings") as load:
            result = scanner._merge_optional_discord_listings(listings, cash_filter=True)
        self.assertIs(result, listings)
        self.assertEqual(result, [{"id": "blox-1"}])
        load.assert_not_called()

    def test_non_cash_mode_merges_only_ids_not_already_in_bloxswaps_batch(self):
        listings = [{"id": "same-id"}, {"id": "blox-2"}]
        discord_rows = [
            {"id": "same-id", "source": "discord"},
            {"id": "discord-1", "source": "discord"},
        ]
        with patch("discord_parser.load_discord_listings", return_value=(discord_rows, 2)):
            result = scanner._merge_optional_discord_listings(listings, cash_filter=False)
        self.assertEqual([row["id"] for row in result], ["same-id", "blox-2", "discord-1"])

    def test_discord_loader_failure_does_not_stop_bloxswaps_scan(self):
        listings = [{"id": "blox-1"}]
        with patch("discord_parser.load_discord_listings", side_effect=OSError("unavailable")):
            result = scanner._merge_optional_discord_listings(listings, cash_filter=False)
        self.assertIs(result, listings)
        self.assertEqual(result, [{"id": "blox-1"}])


class FirstSeenTimestampTests(IsolatedDataTestCase):
    def test_assigns_first_seen_once_and_preserves_discord_posted_time(self):
        first_seen = {"ad-existing": "2026-11-27T10:00:00+00:00"}
        listings = [
            {"id": "blox-1", "source": "bloxswaps", "source_ad_id": "ad-new"},
            {"id": "discord-1", "source": "discord", "posted_at": "2026-11-27T11:00:00Z"},
            {"id": "blox-2", "source": "bloxswaps", "source_ad_id": "ad-existing"},
            {"id": "blox-3", "source": "bloxswaps"},
        ]
        updated, changed = scanner._assign_first_seen_timestamps(
            listings, ["ad-new", "ad-existing"], first_seen, "2026-11-28T12:00:00+00:00"
        )
        self.assertTrue(changed)
        self.assertEqual(updated["ad-new"], "2026-11-28T12:00:00+00:00")
        self.assertEqual(listings[0]["first_seen_at_utc"], "2026-11-28T12:00:00+00:00")
        self.assertEqual(listings[1]["first_seen_at_utc"], "2026-11-27T11:00:00Z")
        self.assertEqual(listings[2]["first_seen_at_utc"], "2026-11-27T10:00:00+00:00")
        self.assertEqual(listings[3]["first_seen_at_utc"], "2026-11-28T12:00:00+00:00")

    def test_existing_first_seen_state_is_not_marked_changed(self):
        listings = [{"id": "blox-1", "source": "bloxswaps", "source_ad_id": "ad-1"}]
        updated, changed = scanner._assign_first_seen_timestamps(
            listings, ["ad-1"], {"ad-1": "2026-11-27T10:00:00+00:00"},
            "2026-11-28T12:00:00+00:00",
        )
        self.assertFalse(changed)
        self.assertEqual(updated["ad-1"], "2026-11-27T10:00:00+00:00")



class ColdStartBaselineTests(IsolatedDataTestCase):
    def test_cold_start_with_listings_seeds_seen_state_and_continues(self):
        stats = {
            "cold_start": True,
            "cold_start_seed_ids": ["old-1", "old-2"],
            "cold_start_recent_ids": ["recent-1"],
        }
        with (
            patch("scanner.save_seen_ads") as save_seen,
            patch("scanner.seed_pending_ids_as_seen") as seed_pending,
            patch("scanner.record_success_if_complete") as record_success,
            patch("scanner.write_step_summary") as summary,
        ):
            seen, stop = scanner._apply_cold_start_baseline(
                stats, ["recent-1"], [{"id": "recent-1"}], None, {}, {}, False, {}
            )
        self.assertEqual(seen, {"old-1", "old-2"})
        self.assertFalse(stop)
        self.assertEqual(save_seen.call_count, 2)
        seed_pending.assert_called_once()
        record_success.assert_not_called()
        summary.assert_not_called()

    def test_empty_cold_start_records_success_and_stops(self):
        stats = {"cold_start": True, "cold_start_seed_ids": ["old-1"]}
        with (
            patch("scanner.save_seen_ads") as save_seen,
            patch("scanner.seed_pending_ids_as_seen"),
            patch("scanner.record_success_if_complete") as record_success,
            patch("scanner.write_step_summary") as summary,
        ):
            seen, stop = scanner._apply_cold_start_baseline(
                stats, ["old-1"], [], None, {}, {}, False, {}
            )
        self.assertEqual(seen, {"old-1"})
        self.assertTrue(stop)
        record_success.assert_called_once_with(stats)
        summary.assert_called_once()
        self.assertEqual(save_seen.call_count, 2)

    def test_non_cold_start_normalizes_missing_seen_state(self):
        with patch("scanner.save_seen_ads") as save_seen:
            seen, stop = scanner._apply_cold_start_baseline(
                {}, [], [{"id": "new"}], None, {}, {}, False, {}
            )
        self.assertEqual(seen, set())
        self.assertFalse(stop)
        save_seen.assert_not_called()


class PendingFeedReconciliationTests(IsolatedDataTestCase):
    def test_retries_unparsed_ids_and_retires_confirmed_non_actionable_ids(self):
        seen = {"unparsed", "ajout", "single-sided", "not-found"}
        pending = {}
        schedule = {}
        stats = {
            "unparsed_ad_ids": ["unparsed"],
            "addition_ids": ["ajout"],
            "single_sided_ids": ["single-sided"],
            "pending_not_found_ids": ["not-found"],
        }
        with (
            patch("scanner.save_seen_ads") as save_seen,
            patch("scanner.save_pending_ids") as save_pending,
            patch("scanner.save_pending_recheck_schedule") as save_schedule,
        ):
            result = scanner._reconcile_feed_pending_state(stats, seen, pending, schedule)
        updated_seen, updated_pending, updated_schedule, additions, singles, missing = result
        self.assertNotIn("unparsed", updated_seen)
        self.assertTrue({"ajout", "single-sided", "not-found"}.issubset(updated_seen))
        self.assertEqual(set(updated_pending), {"unparsed"})
        self.assertEqual(updated_schedule["unparsed"]["reason"], "unparsed_feed")
        self.assertEqual(additions, {"ajout"})
        self.assertEqual(singles, {"single-sided"})
        self.assertEqual(missing, {"not-found"})
        self.assertGreaterEqual(save_seen.call_count, 2)
        save_pending.assert_called()
        save_schedule.assert_called()

    def test_empty_feed_current_boundary_returns_success(self):
        stats = {
            "seen_boundary_reached": True,
            "malformed_count": 0,
            "html_malformed_count": 0,
            "html_parse_warning_count": 0,
            "candidate_count": 0,
            "addition_count": 0,
        }
        with (
            patch("scanner.update_metrics") as metrics,
            patch("scanner.record_success_if_complete") as record_success,
            patch("scanner.write_step_summary") as summary,
        ):
            handled = scanner._handle_empty_scan_feed(stats, [], {}, False)
        self.assertTrue(handled)
        metrics.assert_called_once_with(increments={"parse_empty": 1})
        record_success.assert_called_once_with(stats)
        summary.assert_called_once()

    def test_empty_feed_all_ajouts_returns_success(self):
        stats = {
            "seen_boundary_reached": False,
            "malformed_count": 0,
            "html_malformed_count": 0,
            "html_parse_warning_count": 0,
            "candidate_count": 3,
            "addition_count": 3,
        }
        with (
            patch("scanner.update_metrics"),
            patch("scanner.record_success_if_complete") as record_success,
            patch("scanner.write_step_summary") as summary,
        ):
            handled = scanner._handle_empty_scan_feed(stats, [], {}, False)
        self.assertTrue(handled)
        record_success.assert_called_once_with(stats)
        summary.assert_called_once()

    def test_empty_unverified_feed_fails_closed(self):
        stats = {
            "seen_boundary_reached": False,
            "malformed_count": 0,
            "html_malformed_count": 0,
            "html_parse_warning_count": 0,
            "candidate_count": 0,
            "addition_count": 0,
            "pagination_complete": False,
        }
        with patch("scanner.update_metrics"):
            with self.assertRaisesRegex(RuntimeError, "pagination could not be verified"):
                scanner._handle_empty_scan_feed(stats, [], {}, False)

    def test_nonempty_feed_is_not_treated_as_empty(self):
        with patch("scanner.update_metrics") as metrics:
            handled = scanner._handle_empty_scan_feed({}, [{"id": "offer"}], {}, False)
        self.assertFalse(handled)
        metrics.assert_not_called()

if __name__ == "__main__":
    unittest.main()
