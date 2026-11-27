import unittest

from test_support import IsolatedDataTestCase
from tools.feed_accounting import validate_candidate_accounting


class FeedCandidateAccountingTests(IsolatedDataTestCase):
    def test_accepts_complete_classification(self):
        result = validate_candidate_accounting(
            candidate_count=12,
            additions=1,
            single_sided=2,
            offers_excluded=1,
            malformed=1,
            parsed=7,
        )
        self.assertTrue(result["classification_complete"])
        self.assertEqual(result["classified_count"], 12)

    def test_rejects_unexplained_candidate_gap(self):
        with self.assertRaisesRegex(RuntimeError, "unexplained=1"):
            validate_candidate_accounting(
                candidate_count=12,
                additions=1,
                single_sided=2,
                offers_excluded=1,
                malformed=1,
                parsed=6,
            )

    def test_rejects_overclassification(self):
        with self.assertRaisesRegex(RuntimeError, "unexplained=-1"):
            validate_candidate_accounting(
                candidate_count=12,
                additions=1,
                single_sided=2,
                offers_excluded=1,
                malformed=1,
                parsed=8,
            )

    def test_rejects_boolean_and_negative_counters(self):
        for value in (True, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "Invalid"):
                    validate_candidate_accounting(
                        candidate_count=1,
                        additions=value,
                        single_sided=0,
                        offers_excluded=0,
                        malformed=0,
                        parsed=0,
                    )


if __name__ == "__main__":
    unittest.main()
