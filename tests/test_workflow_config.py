import unittest
from test_support import IsolatedDataTestCase
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowReliabilityTests(IsolatedDataTestCase):

    def test_telegram_connection_test_uses_real_newlines_in_html_message(self):
        workflow = (ROOT / ".github/workflows/telegram-test.yml").read_text(encoding="utf-8")
        self.assertIn('ALERT — TEST ONLY</b>\\n"', workflow)
        self.assertIn('DELIVERY CHECK</b>\\n\\n"', workflow)
        self.assertNotIn(r"\\n", workflow)

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
        self.assertIn('          MAX_BACKLOG_RECHECK_PAGES: "1000"\n', workflow)
        self.assertIn('          CASH_MIN_PROFIT_USD: "1.00"\n', workflow)
        self.assertIn('      CASH_FILTER: "true" # only fully priced, fresh ROBASE-verified offers are eligible\n', workflow)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('      CASH_FILTER: "false" # weekly report is separate from the cash-first production scanner\n', weekly)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", workflow)
        self.assertNotIn("env.CASH_FILTER", weekly)
        self.assertIn('          RBLXVALUE_META_POLL_ENABLED: "true"\n', workflow)
        self.assertIn('          VALUES_TTL_SECONDS: "43200"\n', workflow)
        self.assertIn('          RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY: "6"\n', workflow)
        self.assertIn('          MM2_SCAN_DURATION_SECONDS: "540"\n', workflow)
        self.assertIn('          MM2_SCAN_INTERVAL_SECONDS: "30"\n', workflow)
        self.assertIn('          MAX_BACKLOG_RECHECK_PAGES: "1000"\n', workflow)
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
        self.assertIn('      - "scanner.py"\n', scheduler)
        self.assertIn('      - "tools/**"\n', scheduler)
        self.assertIn("workflow_dispatch:", scheduler)
        self.assertIn("uses: ./.github/workflows/scan.yml", scheduler)
        self.assertNotIn("secrets: inherit", scheduler)
        for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RBLXVALUE_API_KEY", "HEALTHCHECKS_PING_URL"):
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", scheduler)
        self.assertIn("contents: write", scheduler)
        self.assertIn("actions: read", scheduler)
        self.assertNotIn("workflow_run:", scheduler)
        self.assertNotIn("enable-schedules:", scheduler)



    def test_reusable_workflows_declare_explicit_optional_secrets(self):
        scan = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        watchdog = (ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8")
        scan_call = scan.split("  workflow_call:", 1)[1].split("  workflow_dispatch:", 1)[0]
        watchdog_call = watchdog.split("  workflow_call:", 1)[1].split("  workflow_dispatch:", 1)[0]
        scan_secrets = (
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "RBLXVALUE_API_KEY", "HEALTHCHECKS_PING_URL", "MM2_STATE_REPO_TOKEN",
        )
        for secret in scan_secrets:
            self.assertIn(f"{secret}:\n        required: false", scan_call)
        for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            self.assertIn(f"{secret}:\n        required: false", watchdog_call)


    def test_rblxvalue_connection_test_uses_shared_budget_and_never_runs_on_push(self):
        workflow = (ROOT / ".github/workflows/rblxvalue-connection-test.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("rblxvalue_guard.reserve_request(key=key)", workflow)
        self.assertIn("rblxvalue_guard.mark_key_success(key=key)", workflow)
        self.assertIn("if [ ! -f data/rblxvalue_request_budget.json ]; then", workflow)
        self.assertIn("python tools/validate_state.py --directory api-safety-check --api-safety-only", workflow)
        self.assertIn("Persist shared API safety ledger", workflow)
        self.assertIn("git push origin HEAD:state", workflow)
        self.assertNotIn("urllib.request.urlopen(request", workflow)


    def test_existing_state_branch_is_validated_before_scanning(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        restore = workflow.split("      - name: Restore durable state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("python tools/validate_state.py --directory data --require-cache", restore)
        self.assertIn("python tools/validate_state.py --directory data", restore)

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
        self.assertNotIn("secrets: inherit", scheduler)
        self.assertIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", scheduler)
        self.assertIn("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}", scheduler)
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
                self.assertNotIn("secrets: inherit", text)
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
                    allowed_jobs = {"enable-schedules", "recover-stale-scan"}
                    self.assertEqual(path.name, "watchdog-scheduler.yml")
                    self.assertIn(job_name, allowed_jobs)

    def test_dashboard_skips_deploy_when_pages_is_not_enabled(self):
        workflow = (ROOT / ".github/workflows/dashboard-pages.yml").read_text(encoding="utf-8")
        self.assertIn("pages_enabled: ${{ steps.pages-preflight.outputs.enabled }}", workflow)
        self.assertIn("id: pages-preflight", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("request POST", workflow)
        self.assertIn('"build_type":"workflow"', workflow)
        self.assertIn("if: needs.build.outputs.pages_enabled == 'true'", workflow)
        self.assertIn("Repository administrator permission may be required", workflow)


    def test_failed_scan_preserves_only_validated_dedupe_checkpoints(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        validate = workflow.split(
            "      - name: Validate dedupe state after failed scan", 1
        )[1].split("      - name:", 1)[0]
        persist = workflow.split(
            "      - name: Persist validated dedupe state after failed scan", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn("needs.scan.result != 'success'", validate)
        self.assertIn("--dedupe-only", validate)
        self.assertIn("steps.validate-dedupe.outcome == 'success'", persist)
        self.assertIn("git checkout --detach origin/state", persist)
        self.assertIn("git push origin HEAD:state", persist)
        self.assertNotIn("--force", persist)
        for filename in (
            "alerted.json",
            "telegram_uncertain.json",
            "telegram_uncertain_context.json",
            "seen_ads.json",
            "pending_recheck.json",
            "pending_recheck_schedule.json",
            "first_seen_ads.json",
            "watch_ads.json",
            "maintenance_alerts.json",
        ):
            self.assertIn(filename, validate)
            self.assertIn(filename, persist)
        self.assertIn("market cache/database were not replaced", persist)
        tests_workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("Validate current durable dedupe checkpoints", tests_workflow)
        self.assertIn("--dedupe-only", tests_workflow)

    def test_tools_that_import_the_tools_package_are_invoked_as_modules(self):
        scanner = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn("run: python -m tools.preflight --warn", scanner)
        self.assertNotIn("run: python tools/preflight.py", scanner)
        self.assertIn("run: python -m tools.weekly_report", weekly)
        self.assertNotIn("run: python tools/weekly_report.py", weekly)

    def test_private_state_remote_is_configured_for_every_state_consumer(self):
        script = (ROOT / "tools/configure_state_remote.sh").read_text(encoding="utf-8")
        self.assertIn("git remote rename origin source", script)
        self.assertIn("git remote set-url origin", script)
        self.assertIn('http.https://github.com/$state_repo.git/.extraheader', script)
        self.assertNotIn('x-access-token:$state_token@', script)
        self.assertIn('get("private") is True', script)
        self.assertIn("refusing to store runtime data there", script)
        self.assertIn("git push origin refs/remotes/source/state:refs/heads/state", script)
        self.assertNotIn("git push source --delete state", script)
        workflows = {
            ".github/workflows/scan.yml": "Configure private durable state repository",
            ".github/workflows/weekly-report.yml": "Configure private durable state repository",
            ".github/workflows/dashboard-pages.yml": "Configure private durable state repository",
            ".github/workflows/rblxvalue-connection-test.yml": "Configure private durable state repository",
            ".github/workflows/tests.yml": "Configure private durable state repository",
        }
        for path, step in workflows.items():
            content = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(step, content, path)
            self.assertIn("MM2_STATE_REPO_TOKEN", content, path)
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("MM2_STATE_REPO_TOKEN: ${{ secrets.MM2_STATE_REPO_TOKEN }}", scheduler)
        scan = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("prepare-private-state:", scan)
        self.assertIn("needs: prepare-private-state", scan)
        self.assertIn("needs: [prepare-private-state, scan]", scan)
        for path in (".github/workflows/weekly-report.yml", ".github/workflows/dashboard-pages.yml", ".github/workflows/tests.yml"):
            content = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn('MM2_STATE_ALLOW_MIGRATION: "false"', content, path)

    def test_preflight_receives_the_same_rblxvalue_budget_limit(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        preflight = workflow.split(
            "      - name: Run scanner preflight checks", 1
        )[1].split("      - name:", 1)[0]
        scanner = workflow.split(
            "      - name: Run BloxSwaps scanner", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn('RBLXVALUE_DAILY_REQUEST_LIMIT: "60"', preflight)
        self.assertIn('RBLXVALUE_DAILY_REQUEST_LIMIT: "60"', scanner)

    def test_weekly_report_keeps_cash_summary_separate_from_scanner_production_mode(self):
        workflow = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('CASH_FILTER: "false"', workflow)
        self.assertNotIn("CASH_PRICES_CSV", workflow)
        self.assertNotIn("CASH_MAX_AGE_DAYS", workflow)

    def test_bloxswaps_scanner_step_has_no_duplicate_environment_keys(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Run BloxSwaps scanner", 1)[1].split("      - name:", 1)[0]
        self.assertEqual(step.count("CASH_MAX_AGE_DAYS:"), 1)


if __name__ == "__main__":
    unittest.main()
