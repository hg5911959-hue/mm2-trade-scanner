import scanner
from test_support import IsolatedDataTestCase


class RequestedWildcardKindTests(IsolatedDataTestCase):
    def test_addition_wildcards_are_classified(self):
        for value in ("add", "ajout", "ajouts"):
            with self.subTest(value=value):
                self.assertEqual(scanner.requested_wildcard_kind([value]), "ajout")

    def test_offres_wildcards_are_classified(self):
        for value in ("offer", "offers", "offre", "offres", "Offres"):
            with self.subTest(value=value):
                self.assertEqual(scanner.requested_wildcard_kind([value]), "offres")

    def test_accented_french_offer_is_classified(self):
        self.assertEqual(scanner.requested_wildcard_kind(["offrès"]), "offres")

    def test_specific_trades_are_not_excluded(self):
        self.assertEqual(scanner.requested_wildcard_kind([]), "")
        self.assertEqual(scanner.requested_wildcard_kind(["Harvester"]), "")
        self.assertEqual(scanner.requested_wildcard_kind(["upgrade"]), "")

    def test_unstructured_string_is_not_treated_as_wildcard_list(self):
        self.assertEqual(scanner.requested_wildcard_kind("offres"), "")
