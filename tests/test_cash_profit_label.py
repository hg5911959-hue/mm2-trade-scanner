from test_support import IsolatedDataTestCase
import scanner


class CashProfitLabelTests(IsolatedDataTestCase):
    def test_cash_telegram_alert_discloses_gross_estimate_and_excluded_fees(self):
        listing = {
            "source": "bloxswaps",
            "valuation_mode": "cash",
            "url": "https://bloxswaps.com/trade-mm2?trade-type=P2P&ad=test-post",
        }
        message = str(
            scanner.format_trade_message(
                listing,
                10.0,
                ["• Bat x1 — $10.00"],
                12.75,
                ["• Candy x1 — $12.75"],
                2.75,
            )
        )
        self.assertIn("EST. CASH PROFIT (after marketplace fee; before extra costs)", message)
        self.assertIn("Chroma.gg Price (7d)", message)
        self.assertIn("additional costs/slippage are not included", message)

    def test_legacy_value_alert_does_not_receive_cash_fee_disclaimer(self):
        message = str(
            scanner.format_trade_message(
                {"source": "bloxswaps", "valuation_mode": "value"},
                10.0,
                ["• Bat x1"],
                12.0,
                ["• Candy x1"],
                2.0,
            )
        )
        self.assertNotIn("additional costs/slippage are not included", message)
