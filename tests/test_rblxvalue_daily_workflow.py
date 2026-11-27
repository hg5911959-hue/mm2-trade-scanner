"""Regression guards for the RBLXValue daily collector's shared request budget."""

from pathlib import Path

from test_support import IsolatedDataTestCase


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rblxvalue-daily-history.yml"
COLLECTOR = ROOT / "tools" / "rblxvalue_daily.py"


class RBLXValueDailyWorkflowBudgetTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.collector = COLLECTOR.read_text(encoding="utf-8")

    def test_budget_is_restored_and_validated_before_any_api_request(self):
        restore = self.workflow.index("- name: Restore and validate rolling RBLXValue request budget")
        fetch = self.workflow.index("- name: Fetch RBLXValue API dataset")
        self.assertLess(restore, fetch)
        self.assertIn("data/rblxvalue_request_budget.json", self.workflow[restore:fetch])
        self.assertIn("--api-safety-only", self.workflow[restore:fetch])

    def test_every_http_attempt_is_reserved_before_network_io(self):
        reserve = self.collector.index("rblxvalue_guard.reserve_request(")
        request = self.collector.index("with urlopen(Request(url, headers=headers)")
        self.assertLess(reserve, request)
        self.assertIn('workflow="RBLXValue Daily Weapon History"', self.collector)
        self.assertIn("RBLXVALUE_DAILY_REQUEST_LIMIT: \"60\"", self.workflow)

    def test_request_budget_artifact_is_uploaded_even_after_collection_failure(self):
        step = self.workflow.index("- name: Upload rolling request budget even if collection fails")
        end = self.workflow.index("- name: Upload validated snapshot", step)
        block = self.workflow[step:end]
        self.assertIn("if: always()", block)
        self.assertIn("name: rblxvalue-request-budget", block)
        self.assertIn("data/rblxvalue_request_budget.json", block)

    def test_separate_no_secret_job_persists_and_validates_the_budget(self):
        start = self.workflow.index("  persist-budget:")
        end = self.workflow.index("  archive:", start)
        block = self.workflow[start:end]
        self.assertIn("needs: collect", block)
        self.assertIn("if: always()", block)
        self.assertIn("contents: write", block)
        self.assertIn("actions/download-artifact", block)
        self.assertIn("--api-safety-only", block)
        self.assertNotIn("RBLXVALUE_API_KEY", block)
        self.assertIn("git push origin HEAD:robase-daily-history", block)

    def test_snapshot_archive_waits_for_successful_budget_persistence(self):
        start = self.workflow.index("  archive:")
        end = self.workflow.index("    name:", start + 5)
        block = self.workflow[start:end]
        self.assertIn("needs: [collect, persist-budget]", block)
        self.assertIn("needs.persist-budget.result == 'success'", block)
