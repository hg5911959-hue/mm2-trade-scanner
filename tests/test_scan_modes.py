from __future__ import annotations

from test_support import IsolatedDataTestCase

from tools.scan_modes import resolve_scan_modes


class ScanModeTests(IsolatedDataTestCase):
    def test_default_mode_requires_telegram_credentials_for_live_delivery(self):
        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN"):
            resolve_scan_modes({}, cash_filter=True, bot_token="", chat_id="")

    def test_explicit_dry_run_does_not_require_telegram_credentials(self):
        modes = resolve_scan_modes(
            {"DRY_RUN": "true", "MM2_TESTING": "true"},
            cash_filter=True,
            bot_token="",
            chat_id="",
        )
        self.assertTrue(modes.dry_run)
        self.assertTrue(modes.testing_mode)
        self.assertFalse(modes.force_dry_run)

    def test_shadow_mode_forces_dry_run_even_when_environment_says_false(self):
        modes = resolve_scan_modes(
            {"ROBASE_DAILY_CHART_SHADOW_ENABLED": "true", "DRY_RUN": "false"},
            cash_filter=True,
            bot_token="",
            chat_id="",
        )
        self.assertTrue(modes.daily_shadow_mode)
        self.assertTrue(modes.dry_run)
        self.assertTrue(modes.force_dry_run)

    def test_value_profit_shadow_rejects_cash_mode(self):
        with self.assertRaisesRegex(RuntimeError, "CASH_FILTER=false"):
            resolve_scan_modes(
                {"RBLXVALUE_VALUE_PROFIT_SHADOW_ENABLED": "true"},
                cash_filter=True,
                bot_token="",
                chat_id="",
            )

    def test_shadow_modes_are_mutually_exclusive(self):
        with self.assertRaisesRegex(RuntimeError, "must run separately"):
            resolve_scan_modes(
                {
                    "ROBASE_DAILY_CHART_SHADOW_ENABLED": "true",
                    "RBLXVALUE_VALUE_PROFIT_SHADOW_ENABLED": "true",
                },
                cash_filter=False,
                bot_token="",
                chat_id="",
            )


if __name__ == "__main__":
    unittest.main()
