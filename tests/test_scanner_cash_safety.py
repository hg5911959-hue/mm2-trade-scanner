"""Regression tests for scanner cash-alert safety gates."""

import unittest

import scanner


class ScannerCashSafetyTests(unittest.TestCase):
    def test_legacy_estimate_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": True,
            "manual_review": False,
            "cash_profit_worst": 10_000.0,
            "profit_worst": 10_000.0,
            "margin": 500.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_manual_review_is_never_alert_eligible(self):
        result = {
            "cash_estimate_only": False,
            "manual_review": True,
            "cash_profit_worst": 10_000.0,
            "margin": 500.0,
        }
        self.assertFalse(scanner.qualifies_cash(result))

    def test_verified_cash_profit_can_pass_alert_gate(self):
        result = {
            "cash_estimate_only": False,
            "manual_review": False,
            "cash_profit_worst": 100.0,
            "margin": 20.0,
        }
        self.assertTrue(scanner.qualifies_cash(result))


if __name__ == "__main__":
    unittest.main()
