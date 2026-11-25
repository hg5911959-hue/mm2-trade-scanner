"""Regression guards for automatic, isolated daily-chart shadow workflow."""

from pathlib import Path

from test_support import IsolatedDataTestCase


ROOT = Path(__file__).resolve().parents[1]
SHADOW_WORKFLOW = ROOT / ".github" / "workflows" / "robase-daily-chart-shadow.yml"
SCAN_WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"


class DailyChartShadowWorkflowTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        cls.shadow = SHADOW_WORKFLOW.read_text(encoding="utf-8")
        cls.scan = SCAN_WORKFLOW.read_text(encoding="utf-8")

    def test_shadow_runs_only_after_successful_main_archive(self):
        self.assertIn('workflows: ["ROBASE daily All marketplaces history"]', self.shadow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.shadow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.shadow)

    def test_caller_grants_reusable_workflow_permissions(self):
        self.assertIn("actions: read", self.shadow)
        self.assertIn("contents: write", self.shadow)

    def test_shadow_is_dry_run_and_uses_daily_prices(self):
        self.assertIn("dry_run: true", self.shadow)
        self.assertIn("robase_daily_chart_shadow: true", self.shadow)

    def test_shadow_never_persists_live_scanner_state(self):
        self.assertIn("inputs.robase_daily_chart_shadow != true", self.scan)


