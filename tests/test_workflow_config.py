import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowReliabilityTests(unittest.TestCase):
    def test_ci_linter_version_is_pinned(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("ruff==0.17.5", workflow)

    def test_ci_runs_repository_wide_correctness_lint(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("ruff check --select F .", workflow)
    def test_scanner_is_reusable_and_scheduler_owns_the_cron(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertNotIn("repository_dispatch:", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('          MAX_PENDING_RECHECK_PER_SCAN: "50"\n', workflow)
        self.assertIn('          RBLXVALUE_META_POLL_ENABLED: "true"\n', workflow)
        self.assertIn('          RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY: "6"\n', workflow)
        self.assertIn('          MM2_SCAN_DURATION_SECONDS: "540"\n', workflow)
        self.assertIn('          MM2_SCAN_INTERVAL_SECONDS: "30"\n', workflow)
        self.assertIn('          MAX_BACKLOG_RECHECK_PAGES: "25"\n', workflow)
        self.assertIn('          BACKLOG_RECHECK_EVERY_CYCLES: "4"\n', workflow)
        self.assertNotIn('\n      MAX_PENDING_RECHECK_PER_SCAN:', workflow)
        self.assertIn("python tools/validate_state.py --directory state-artifact/data --require-cache", workflow)
        restore = workflow.split("      - name: Restore durable state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("set -euo pipefail", restore)
        self.assertIn("git checkout -- data/cash_prices.csv", restore)
        artifact = workflow.split("      - name: Prepare state artifact", 1)[1].split("      - name:", 1)[0]
        self.assertIn("rm -f state-bundle/data/cash_prices.csv", artifact)
        self.assertIn('cron: "*/10 * * * *"', scheduler)
        self.assertIn("uses: ./.github/workflows/scan.yml", scheduler)
        self.assertIn("secrets: inherit", scheduler)
        self.assertIn("contents: write", scheduler)
        self.assertIn("actions: write", scheduler)
        self.assertIn("Re-enable scheduled workflows if GitHub disabled them", scheduler)
        self.assertIn("/actions/workflows/$workflow_id/enable", scheduler)
        self.assertIn('workflows: ["MM2 Scanner Tests"]', scheduler)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", scheduler)


    def test_watchdog_restores_inactivity_disabled_scan_schedule_but_respects_manual_disable(self):
        scheduler = (ROOT / ".github/workflows/watchdog-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("actions: write", scheduler)
        self.assertIn("disabled_inactivity)", scheduler)
        self.assertIn("disabled_manually)", scheduler)
        self.assertIn("respecting manual disable for scan-scheduler", scheduler)
        self.assertIn("needs: restore-scanner-schedule", scheduler)

    def test_scan_scheduler_does_not_override_manual_disable(self):
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("disabled_inactivity)", scheduler)
        self.assertIn("disabled_manually)", scheduler)
        self.assertIn("respecting manual disable", scheduler)
        self.assertNotIn("disabled_manually|disabled_inactivity|disabled)", scheduler)

    def test_watchdog_uses_a_dedicated_scheduled_entrypoint(self):
        workflow = (ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/watchdog-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn('cron: "*/5 * * * *"', scheduler)
        self.assertIn("uses: ./.github/workflows/watchdog.yml", scheduler)
        self.assertIn("secrets: inherit", scheduler)
        self.assertIn("actions: read", scheduler)
        self.assertIn('workflows: ["MM2 Scanner Scheduler"]', scheduler)
        self.assertIn("types: [completed]", scheduler)

    def test_dashboard_skips_deploy_when_pages_is_not_enabled(self):
        workflow = (ROOT / ".github/workflows/dashboard-pages.yml").read_text(encoding="utf-8")
        self.assertIn("pages_enabled: ${{ steps.pages-preflight.outputs.enabled }}", workflow)
        self.assertIn("id: pages-preflight", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("request POST", workflow)
        self.assertIn('"build_type":"workflow"', workflow)
        self.assertIn("if: needs.build.outputs.pages_enabled == 'true'", workflow)
        self.assertIn("Repository administrator permission may be required", workflow)


if __name__ == "__main__":
    unittest.main()
