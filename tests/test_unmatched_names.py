from datetime import datetime, timedelta, timezone

from test_support import IsolatedDataTestCase
import scanner
from tools.unmatched_registry import prune_stale_unmatched_names


class UnmatchedNameRegistryTests(IsolatedDataTestCase):
    def test_rejection_reasons_are_not_recorded_as_item_names(self):
        missing = [
            "wildcard requests are not exact ROBASE-only offers",
            "wildcards or incomplete trade sides are not eligible",
            "Ajouts/Offres wildcards or incomplete trade sides are not eligible",
            "one side has no items",
            "quantity uncertain",
            "received-side value-weighted Demand is low",
            "Drool has no numeric Supreme value",
            "Brew has no numeric Supreme value",
            "Emberknife",
            "Candy",
        ]
        self.assertEqual(
            scanner.unmatched_names_from_missing(missing, cash_mode=True),
            ["Emberknife", "Candy"],
        )

    def test_wildcard_rejection_reasons_are_filtered_in_legacy_mode_too(self):
        self.assertEqual(
            scanner.unmatched_names_from_missing(
                [
                    "wildcard requests are not exact ROBASE-only offers",
                    "received-side value-weighted Demand is low",
                    "Drool has no numeric Supreme value",
                    "Embergun",
                ],
                cash_mode=False,
            ),
            ["Embergun"],
        )

    def test_stale_registry_entries_are_pruned_without_touching_active_or_invalid_rows(self):
        now = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
        state = {
            "old": {"name": "Old item", "last_seen_at": (now - timedelta(days=15)).isoformat()},
            "active": {"name": "Active item", "last_seen_at": (now - timedelta(days=2)).isoformat()},
            "boundary": {"name": "Boundary item", "last_seen_at": (now - timedelta(days=14)).isoformat()},
            "invalid": {"name": "Invalid timestamp", "last_seen_at": "not-a-date"},
            "missing": {"name": "Missing timestamp"},
            "naive": {"name": "Naive timestamp", "last_seen_at": "2026-11-01T00:00:00"},
        }

        retained, removed = prune_stale_unmatched_names(state, now=now, retention_days=14)

        self.assertEqual(removed, ["old"])
        self.assertNotIn("old", retained)
        self.assertEqual(retained["active"], state["active"])
        self.assertEqual(retained["boundary"], state["boundary"])
        self.assertEqual(retained["invalid"], state["invalid"])
        self.assertEqual(retained["missing"], state["missing"])
        self.assertEqual(retained["naive"], state["naive"])

    def test_unmatched_registry_retention_requires_safe_configuration(self):
        now = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            prune_stale_unmatched_names({}, now=now, retention_days=0)
        with self.assertRaises(ValueError):
            prune_stale_unmatched_names({}, now=datetime(2026, 11, 28, 12), retention_days=14)
