import unittest
from test_support import IsolatedDataTestCase
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowReliabilityTests(IsolatedDataTestCase):
    def test_ci_linter_version_is_pinned(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("ruff==0.17.5", workflow)

    def test_ci_runs_repository_wide_correctness_lint(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("ruff check --select F .", workflow)
    def test_ci_hashes_every_data_file_around_both_suite_runs(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("find data -type f -print0", workflow)
        self.assertIn('before_hash="$(data_hash)"', workflow)
        self.assertIn('after_hash="$(data_hash)"', workflow)
        self.assertIn('git status --porcelain data', workflow)

    def test_report_records_verified_baseline_and_unresolved_tag_operation(self):
        report = (ROOT / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Baseline test suite: **387 tests**", report)
        self.assertIn("before-v13", report)
        self.assertIn("tag creation operation", report)

    def test_scanner_is_reusable_and_scheduler_owns_the_cron(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertNotIn("repository_dispatch:", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('          BLOXSWAPS_URL: "https://bloxswaps.com/trade-mm2?trade-type=P2P"\n', workflow)
        self.assertIn('          BLOXSWAPS_API_ONLY: "true"\n', workflow)
        self.assertIn('          MAX_PENDING_RECHECK_PER_SCAN: "50"\n', workflow)
        self.assertIn('      CASH_FILTER: "false" # set to true only after fresh verified prices exist\n', workflow)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('      CASH_FILTER: "false" # set to true only after fresh verified prices exist\n', weekly)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", workflow)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", weekly)
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
        self.assertIn("tar --exclude='data/cash_prices.csv' -x", restore)
        self.assertNotIn("git checkout -- data/cash_prices.csv", restore)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn("tar --exclude='data/cash_prices.csv' -x", weekly)
        artifact = workflow.split("      - name: Prepare state artifact", 1)[1].split("      - name:", 1)[0]
        self.assertIn("rm -f state-bundle/data/cash_prices.csv", artifact)
        self.assertIn('cron: "*/10 * * * *"', scheduler)
        self.assertIn("workflow_dispatch:", scheduler)
        self.assertIn("uses: ./.github/workflows/scan.yml", scheduler)
        self.assertIn("secrets: inherit", scheduler)
        self.assertIn("contents: write", scheduler)
        self.assertIn("actions: read", scheduler)
        self.assertNotIn("workflow_run:", scheduler)
        self.assertNotIn("enable-schedules:", scheduler)


    def test_manual_scan_dispatch_defaults_to_dry_run(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("  workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("default: true", dispatch)
        self.assertIn("default: true", scheduler)
        self.assertIn("dry_run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || false }}", scheduler)
        self.assertIn("verify_rblxvalue_key: ${{ github.event_name == 'workflow_dispatch' && inputs.verify_rblxvalue_key || false }}", scheduler)

    def test_api_safety_ledger_is_persisted_when_scan_fails_without_replacing_cache(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("name: Upload API safety ledger", workflow)
        self.assertIn("--api-safety-only", workflow)
        self.assertIn("Persist API safety ledger after failed scan", workflow)
        self.assertIn("needs.scan.result != 'success'", workflow)
        self.assertIn("steps.persist-state.outcome == 'success'", workflow)
        self.assertIn("Persisted only validated API safety files", workflow)
        self.assertIn("hashFiles('api-safety-artifact/data/*.json') != ''", workflow)

    def test_watchdog_restores_inactivity_disabled_scan_schedule_but_respects_manual_disable(self):
        scheduler = (ROOT / ".github/workflows/watchdog-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("actions: write", scheduler)
        self.assertIn("disabled_inactivity)", scheduler)
        self.assertIn("disabled_manually)", scheduler)
        self.assertIn("respecting manual disable for scan-scheduler", scheduler)
        self.assertIn("needs: enable-schedules", scheduler)
        self.assertIn("  enable-schedules:", scheduler)

    def test_watchdog_uses_a_dedicated_scheduled_entrypoint(self):
        workflow = (ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/watchdog-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn('cron: "*/5 * * * *"', scheduler)
        self.assertIn("uses: ./.github/workflows/watchdog.yml", scheduler)
        self.assertIn("secrets: inherit", scheduler)
        self.assertIn("actions: write", scheduler)
        self.assertIn('workflows: ["MM2 Scanner Scheduler"]', scheduler)
        self.assertIn("types: [completed]", scheduler)

    def test_all_workflows_use_least_privilege_and_immutable_action_pins(self):
        import re
        workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("write-all", text)
                for line in text.splitlines():
                    match = re.match(r"\s*uses:\s*([^\s#]+)", line)
                    if match and not match.group(1).startswith("./"):
                        self.assertRegex(match.group(1), r"^[^@]+@[0-9a-f]{40}$")
                    if re.search(r"\b(?:echo|printf)\b.*(?:\$\{\{\s*secrets\.|\$[A-Z_]*(?:TOKEN|KEY|SECRET|CHAT))", line, re.I):
                        self.fail("workflow prints a secret-bearing value: " + path.name)
                root_permissions = re.search(r"(?m)^permissions:\n((?:[ \t]+[^\n]*\n)+)", text)
                if root_permissions:
                    self.assertNotRegex(root_permissions.group(1), r"(?m):\s*write\b")
                for match in re.finditer(r"(?m)^\s*actions:\s*write\s*$", text):
                    prefix = text[:match.start()]
                    job_match = list(re.finditer(r"(?m)^  ([a-zA-Z0-9_-]+):\s*$", prefix))
                    job_name = job_match[-1].group(1) if job_match else ""
                    self.assertEqual((path.name, job_name), ("watchdog-scheduler.yml", "enable-schedules"))

    def test_dashboard_skips_deploy_when_pages_is_not_enabled(self):
        workflow = (ROOT / ".github/workflows/dashboard-pages.yml").read_text(encoding="utf-8")
        self.assertIn("pages_enabled: ${{ steps.pages-preflight.outputs.enabled }}", workflow)
        self.assertIn("id: pages-preflight", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("request POST", workflow)
        self.assertIn('"build_type":"workflow"', workflow)
        self.assertIn("if: needs.build.outputs.pages_enabled == 'true'", workflow)
        self.assertIn("Repository administrator permission may be required", workflow)


    def test_tools_that_import_the_tools_package_are_invoked_as_modules(self):
        scanner = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn("run: python -m tools.preflight --warn", scanner)
        self.assertNotIn("run: python tools/preflight.py", scanner)
        self.assertIn("run: python -m tools.weekly_report", weekly)
        self.assertNotIn("run: python tools/weekly_report.py", weekly)

    def test_bloxswaps_scanner_step_has_no_duplicate_environment_keys(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Run BloxSwaps scanner", 1)[1].split("      - name:", 1)[0]
        self.assertEqual(step.count("CASH_MAX_AGE_DAYS:"), 1)


if __name__ == "__main__":
    unittest.main()
