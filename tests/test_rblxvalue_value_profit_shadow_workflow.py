"""Guards the Supreme value-profit experiment as a manual, isolated shadow only."""

from pathlib import Path

from test_support import IsolatedDataTestCase


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rblxvalue-value-profit-shadow.yml"
CORE = ROOT / ".github" / "workflows" / "scan-core.yml"


class RBLXValueShadowWorkflowTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.core = CORE.read_text(encoding="utf-8")

    def test_shadow_analysis_is_manual_only(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("workflow_run:", self.workflow)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", self.workflow)
        self.assertIn("Automatic archive-triggered scans add BloxSwaps API load", self.workflow)

    def test_shadow_remains_dry_run_and_outside_cash_filter(self):
        self.assertIn("dry_run: true", self.workflow)
        self.assertIn("rblxvalue_value_profit_shadow: true", self.workflow)
        self.assertIn("CASH_FILTER: ${{ inputs.rblxvalue_value_profit_shadow && 'false' || 'true' }}", self.core)
        self.assertIn("DRY_RUN: ${{ (inputs.dry_run || inputs.robase_daily_chart_shadow || inputs.rblxvalue_value_profit_shadow) && 'true' || 'false' }}", self.core)

    def test_shadow_workflow_has_no_production_write_or_telegram_secrets(self):
        self.assertIn("contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", self.workflow)
        self.assertNotIn("TELEGRAM_CHAT_ID", self.workflow)
        self.assertNotIn("RBLXVALUE_API_KEY", self.workflow)
