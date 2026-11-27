from test_support import IsolatedDataTestCase
import scanner


class UnmatchedNameRegistryTests(IsolatedDataTestCase):
    def test_rejection_reasons_are_not_recorded_as_item_names(self):
        missing = [
            "wildcard requests are not exact ROBASE-only offers",
            "wildcards or incomplete trade sides are not eligible",
            "Ajouts/Offres wildcards or incomplete trade sides are not eligible",
            "one side has no items",
            "quantity uncertain",
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
                ["wildcard requests are not exact ROBASE-only offers", "Embergun"],
                cash_mode=False,
            ),
            ["Embergun"],
        )
