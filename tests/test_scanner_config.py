from __future__ import annotations

import unittest

from test_support import IsolatedDataTestCase
from tools.scanner_config import ScannerConfig


class ScannerConfigTests(IsolatedDataTestCase):
    def test_defaults_preserve_existing_scanner_defaults(self):
        config = ScannerConfig.from_env({})
        self.assertEqual(config.bloxswaps_url, "https://bloxswaps.com/trade-mm2?trade-type=P2P")
        self.assertEqual(config.max_feed_pages, 1000)
        self.assertEqual(config.max_alerts_per_run, 0)
        self.assertEqual(config.rblxvalue_daily_request_limit, 60)
        self.assertEqual(config.max_alerts_per_scan, 0)
        self.assertEqual(config.max_alerts_per_session, 0)
        self.assertEqual(config.scan_duration_seconds, 0)
        self.assertEqual(config.scan_interval_seconds, 45)
        self.assertEqual(config.cash_min_profit_usd, 2.0)
        self.assertEqual(config.cash_min_profit_usd_small, 2.0)
        self.assertEqual(config.cash_min_verified_rows, 8)
        self.assertEqual(config.cash_min_margin_percent, 5.0)
        self.assertFalse(config.cash_filter)
        self.assertEqual(config.default_values_ttl_seconds, 12 * 60 * 60)
        self.assertEqual(config.values_ttl_seconds, 12 * 60 * 60)

    def test_cash_mode_uses_its_existing_default_values_ttl(self):
        config = ScannerConfig.from_env({"CASH_FILTER": "true"})
        self.assertTrue(config.cash_filter)
        self.assertEqual(config.default_values_ttl_seconds, 24 * 60 * 60)
        self.assertEqual(config.values_ttl_seconds, 24 * 60 * 60)

    def test_values_are_normalized_and_bounded(self):
        config = ScannerConfig.from_env({
            "BLOXSWAPS_URL": "  https://example.test/trades  ",
            "TELEGRAM_BOT_TOKEN": " token ",
            "TELEGRAM_CHAT_TYPE": " PRIVATE ",
            "MAX_FEED_PAGES": "5000",
            "MAX_ALERTS_PER_RUN": "-3",
            "CASH_MAX_AGE_DAYS": "0",
            "CASH_MAX_LISTING_AGE_SECONDS": "5",
            "CASH_MIN_PROFIT_USD": "0.25",
            "CASH_MIN_VERIFIED_ROWS": "-1",
        })
        self.assertEqual(config.bloxswaps_url, "https://example.test/trades")
        self.assertEqual(config.telegram_bot_token, "token")
        self.assertEqual(config.telegram_chat_type, "private")
        self.assertEqual(config.max_feed_pages, 1000)
        self.assertEqual(config.max_alerts_per_run, 0)
        self.assertEqual(config.cash_max_age_days, 1)
        self.assertEqual(config.cash_max_listing_age_seconds, 60)
        self.assertEqual(config.cash_min_profit_usd, 1.0)
        self.assertEqual(config.cash_min_verified_rows, 0)

    def test_runtime_scan_settings_are_parsed_and_session_limit_falls_back_to_run_limit(self):
        config = ScannerConfig.from_env({
            "MAX_ALERTS_PER_RUN": "7",
            "MAX_ALERTS_PER_SCAN": "3",
            "MAX_ALERTS_PER_SESSION": "4",
            "MM2_SCAN_DURATION_SECONDS": "540",
            "MM2_SCAN_INTERVAL_SECONDS": "30",
        })
        self.assertEqual(config.max_alerts_per_scan, 3)
        self.assertEqual(config.max_alerts_per_session, 4)
        self.assertEqual(config.scan_duration_seconds, 540)
        self.assertEqual(config.scan_interval_seconds, 30)

    def test_blank_runtime_integer_uses_existing_default(self):
        config = ScannerConfig.from_env({
            "MM2_SCAN_DURATION_SECONDS": "",
            "MM2_SCAN_INTERVAL_SECONDS": "",
            "MAX_ALERTS_PER_SESSION": "",
        })
        self.assertEqual(config.scan_duration_seconds, 0)
        self.assertEqual(config.scan_interval_seconds, 45)
        self.assertEqual(config.max_alerts_per_session, 0)

    def test_rblxvalue_daily_request_limit_is_bounded_and_validated(self):
        self.assertEqual(
            ScannerConfig.from_env({"RBLXVALUE_DAILY_REQUEST_LIMIT": "100"}).rblxvalue_daily_request_limit,
            60,
        )
        self.assertEqual(
            ScannerConfig.from_env({"RBLXVALUE_DAILY_REQUEST_LIMIT": "0"}).rblxvalue_daily_request_limit,
            1,
        )
        with self.assertRaisesRegex(ValueError, "RBLXVALUE_DAILY_REQUEST_LIMIT"):
            ScannerConfig.from_env({"RBLXVALUE_DAILY_REQUEST_LIMIT": "many"})

    def test_invalid_integer_reports_the_environment_variable(self):
        with self.assertRaisesRegex(ValueError, "MAX_FEED_PAGES"):
            ScannerConfig.from_env({"MAX_FEED_PAGES": "many"})

    def test_non_finite_float_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CASH_MIN_MARGIN_PERCENT"):
            ScannerConfig.from_env({"CASH_MIN_MARGIN_PERCENT": "nan"})

    def test_invalid_boolean_is_rejected_instead_of_silently_enabling_or_disabling(self):
        with self.assertRaisesRegex(ValueError, "CASH_FILTER"):
            ScannerConfig.from_env({"CASH_FILTER": "enabled"})


if __name__ == "__main__":
    unittest.main()
