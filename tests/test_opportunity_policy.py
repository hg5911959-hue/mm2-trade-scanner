from test_support import IsolatedDataTestCase
from tools.opportunity_policy import qualifies


class OpportunityQualificationTests(IsolatedDataTestCase):
    def test_verified_conservative_profit_qualifies(self):
        self.assertTrue(
            qualifies(
                {"profit_worst": 12.0, "margin": 8.0},
                min_diff=10.0,
                min_percent=5.0,
            )
        )

    def test_difference_is_legacy_metric_fallback(self):
        self.assertTrue(
            qualifies({"difference": 12.0, "margin": 8.0}, 10.0, 5.0)
        )

    def test_best_case_only_or_estimate_only_results_never_qualify(self):
        self.assertFalse(
            qualifies(
                {"profit_worst": 12.0, "margin": 8.0, "manual_review": True},
                10.0,
                5.0,
            )
        )
        self.assertFalse(
            qualifies(
                {"profit_worst": 12.0, "margin": 8.0, "cash_estimate_only": True},
                10.0,
                5.0,
            )
        )

    def test_non_positive_or_below_threshold_profit_is_rejected(self):
        self.assertFalse(qualifies({"profit_worst": 0, "margin": 8}, 0, 0))
        self.assertFalse(qualifies({"profit_worst": 9, "margin": 8}, 10, 5))
        self.assertFalse(qualifies({"profit_worst": 12, "margin": 4}, 10, 5))

    def test_invalid_non_finite_and_boolean_metrics_are_rejected(self):
        for value in (True, "12", float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertFalse(
                    qualifies({"profit_worst": value, "margin": 8}, 10, 5)
                )

    def test_negative_thresholds_are_rejected(self):
        self.assertFalse(qualifies({"profit_worst": 12, "margin": 8}, -1, 5))
        self.assertFalse(qualifies({"profit_worst": 12, "margin": 8}, 10, -1))

    def test_missing_or_non_mapping_results_are_rejected(self):
        for result in (None, [], "trade", {}):
            with self.subTest(result=result):
                self.assertFalse(qualifies(result, 0, 0))
