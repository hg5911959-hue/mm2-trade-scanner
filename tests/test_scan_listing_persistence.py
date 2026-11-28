from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import scanner
from test_support import IsolatedDataTestCase


class ScanListingPersistenceTests(IsolatedDataTestCase):
    def test_deduplicated_listings_are_persisted_to_both_history_stores(self):
        listings = [{"id": "one"}, {"id": "duplicate"}]
        deduplicated = [{"id": "one"}]
        history = Mock()
        history.record_listings.return_value = 1
        public_history = Mock()
        public_history.record_listings.return_value = 1

        with patch.object(scanner, "deduplicate_listings", return_value=deduplicated):
            result = scanner._deduplicate_and_record_scan_listings(
                listings, history, public_history
            )

        self.assertEqual(result[0], deduplicated)
        self.assertEqual([row for row in result[1].outcomes], [])
        history.record_listings.assert_called_once_with(deduplicated)
        public_history.record_listings.assert_called_once_with(deduplicated)

    def test_private_history_failure_does_not_block_scan(self):
        listings = [{"id": "one"}]
        history = Mock()
        history.record_listings.side_effect = OSError("disk unavailable")

        with patch.object(scanner, "deduplicate_listings", return_value=listings):
            result = scanner._deduplicate_and_record_scan_listings(
                listings, history, None
            )

        self.assertEqual(result[0], listings)
        history.record_listings.assert_called_once_with(listings)

    def test_public_history_failure_fails_closed(self):
        listings = [{"id": "one"}]
        public_history = Mock()
        public_history.record_listings.side_effect = OSError("database unavailable")

        with patch.object(scanner, "deduplicate_listings", return_value=listings):
            with self.assertRaisesRegex(
                RuntimeError, "Public dashboard listing history could not be persisted"
            ):
                scanner._deduplicate_and_record_scan_listings(
                    listings, None, public_history
                )

        public_history.record_listings.assert_called_once_with(listings)

    def test_feed_report_includes_pagination_and_explicit_quantities(self):
        listing = {
            "id": "listing-1",
            "receive_items": [{"name": "Candy", "quantity": 2}],
            "give_items": [],
        }
        stats = {
            "candidate_count": 4,
            "addition_count": 1,
            "offers_excluded_count": 1,
            "owner_link_count": 2,
            "pagination_pages": 3,
            "pagination_api_posts": 60,
            "pagination_complete": True,
            "seen_boundary_reached": False,
        }
        with patch.object(scanner, "_report_scan_feed_quality") as quality, patch(
            "builtins.print"
        ) as output:
            scanner._log_scan_feed_candidates(stats, [listing])

        quality.assert_called_once_with(stats)
        printed = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("pages=3", printed)
        self.assertIn("Candy x2", printed)


if __name__ == "__main__":
    unittest.main()
