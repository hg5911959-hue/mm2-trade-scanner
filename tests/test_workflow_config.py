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
        self.assertIn('          CASH_MIN_PROFIT_USD: "2.00"\n', workflow)
        self.assertIn('      CASH_FILTER: "true" # only fully priced, fresh ROBASE-verified offers are eligible\n', workflow)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('      CASH_FILTER: "false" # weekly report is separate from the cash-first production scanner\n', weekly)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", workflow)
        self.assertNotIn("env.CASH_FILTER", weekly)
        self.assertNotIn("RBLXVALUE_META_POLL_ENABLED", workflow)
        self.assertIn('          MM2_SCAN_DURATION_SECONDS: "540"\n', workflow)
        self.assertIn('          MM2_SCAN_INTERVAL_SECONDS: "30"\n', workflow)
        self.assertIn('          MAX_BACKLOG_RECHECK_PAGES: "1000"\n', workflow)
        self.assertIn('          BACKLOG_RECHECK_EVERY_CYCLES: "4"\n', workflow)
        self.assertNotIn('\n      MAX_PENDING_RECHECK_PER_SCAN:', workflow)
        self.assertIn("python tools/validate_state.py --directory state-artifact/data", workflow)
        restore = workflow.split("      - name: Restore durable state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("set -euo pipefail", restore)
        self.assertIn("tar --exclude='data/cash_prices.csv' -x", restore)
        self.assertNotIn("git checkout -- data/cash_prices.csv", restore)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn("tar --exclude='data/cash_prices.csv' -x", weekly)
        artifact = workflow.split("      - name: Prepare state artifact", 1)[1].split("      - name:", 1)[0]
        self.assertIn("rm -f state-bundle/data/cash_prices.csv", artifact)
        self.assertIn('cron: "3-59/10 * * * *"', scheduler)
        self.assertIn('      - "scanner.py"\n', scheduler)
        self.assertIn('      - "tools/**"\n', scheduler)
        self.assertIn("workflow_dispatch:", scheduler)
        self.assertIn("uses: ./.github/workflows/scan.yml", scheduler)
        self.assertNotIn("secrets: inherit", scheduler)
        for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "HEALTHCHECKS_PING_URL"):
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
            "HEALTHCHECKS_PING_URL",
        )
        for secret in scan_secrets:
            self.assertIn(f"{secret}:\n        required: false", scan_call)
        for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            self.assertIn(f"{secret}:\n        required: false", watchdog_call)


    def test_rblxvalue_connection_test_uses_shared_budget_and_never_runs_on_push(self):
        workflow = (ROOT / ".github/workflows/rblxvalue-connection-test.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertIn("if: false", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("rblxvalue_guard.reserve_request(key=key)", workflow)
        self.assertIn("rblxvalue_guard.mark_key_success(key=key)", workflow)
        self.assertIn("if [ ! -f data/rblxvalue_request_budget.json ]; then", workflow)
        self.assertIn("python tools/validate_state.py --directory api-safety-check --api-safety-only", workflow)
        self.assertIn("Persist shared API safety ledger", workflow)
        self.assertIn("git push origin HEAD:state", workflow)
        self.assertNotIn("urllib.request.urlopen(request", workflow)


    def test_read_only_shadow_core_stays_identical_to_live_scan_job(self):
        live = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        core = (ROOT / ".github/workflows/scan-core.yml").read_text(encoding="utf-8")
        live_job = live.split("  scan:\n", 1)[1].split("  persist-state:\n", 1)[0]
        core_job = core.split("  scan:\n", 1)[1]
        # The reusable core may add only explicit, read-only shadow inputs and
        # their restore step. Normalize those additions before checking that
        # the shared live scan implementation remains byte-for-byte equivalent.
        core_job = core_job.replace(
            "      CASH_FILTER: ${{ inputs.rblxvalue_value_profit_shadow && 'false' || 'true' }} # value-profit shadow is isolated from the production cash filter\n",
            '      CASH_FILTER: "true" # only fully priced, fresh ROBASE-verified offers are eligible\n',
        )
        core_job = core_job.replace(
            "      RBLXVALUE_VALUE_PROFIT_SHADOW_ENABLED: ${{ inputs.rblxvalue_value_profit_shadow && 'true' || 'false' }}\n"
            "      RBLXVALUE_DAILY_ARCHIVE_FILE: rblxvalue-archive/data/rblxvalue_latest.json\n",
            "",
        )
        if "      - name: Restore RBLXValue daily snapshot for offline Value-profit shadow\n" in core_job:
            before, after = core_job.split(
                "      - name: Restore RBLXValue daily snapshot for offline Value-profit shadow\n", 1
            )
            after = after.split("      - name: Run scanner preflight checks", 1)[1]
            core_job = before + "      - name: Run scanner preflight checks" + after
        core_job = core_job.replace(
            "PREFLIGHT_SEND_NOTICES: ${{ (inputs.dry_run || inputs.robase_daily_chart_shadow || inputs.rblxvalue_value_profit_shadow) && 'false' || 'true' }}",
            "PREFLIGHT_SEND_NOTICES: ${{ (inputs.dry_run || inputs.robase_daily_chart_shadow) && 'false' || 'true' }}",
        )
        core_job = core_job.replace(
            "DRY_RUN: ${{ (inputs.dry_run || inputs.robase_daily_chart_shadow || inputs.rblxvalue_value_profit_shadow) && 'true' || 'false' }}",
            "DRY_RUN: ${{ (inputs.dry_run || inputs.robase_daily_chart_shadow) && 'true' || 'false' }}",
        )
        self.assertEqual(live_job.strip(), core_job.strip())
        self.assertNotIn("persist-state:", core)

    def test_supreme_shadow_uses_conservative_value_and_market_quality_thresholds(self):
        core = (ROOT / ".github/workflows/scan-core.yml").read_text(encoding="utf-8")
        self.assertIn("MIN_VALUE_DIFFERENCE: ${{ inputs.rblxvalue_value_profit_shadow && '2' || '0' }}", core)
        self.assertIn("MIN_PROFIT_PERCENT: ${{ inputs.rblxvalue_value_profit_shadow && '8' || '5' }}", core)
        self.assertIn("MIN_RECEIVE_DEMAND_SCORE: ${{ inputs.rblxvalue_profit_shadow && '5.0' || '4.0' }}".replace("rblxvalue_profit_shadow","rblxvalue_value_profit_shadow"), core)
        self.assertIn("MIN_RECEIVE_STABILITY_SCORE: ${{ inputs.rblxvalue_value_profit_shadow && '5.5' || '3.0' }}", core)
        live = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn('CASH_MIN_PROFIT_USD: "2.00"', live)

    def test_existing_state_branch_is_validated_before_scanning(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        restore = workflow.split("      - name: Restore durable state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("python tools/validate_state.py --directory data", restore)
        self.assertIn("python tools/validate_state.py --directory data", restore)

    def test_manual_scan_dispatch_defaults_to_dry_run(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("  workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("default: true", dispatch)
        self.assertIn("default: true", scheduler)
        self.assertIn("dry_run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || false }}", scheduler)
        self.assertNotIn("verify_rblxvalue_key", scheduler)

    def test_successful_scan_requires_durable_state_before_continuation(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("Require scanner state artifact after successful scan", workflow)
        self.assertIn("needs.scan.result == 'success' && steps.download.outcome != 'success'", workflow)
        self.assertIn("Verify successful scan timestamp is durable", workflow)
        self.assertIn("state-artifact/data/last_successful_scan.json", workflow)
        self.assertIn("refs/remotes/origin/state:data/last_successful_scan.json", workflow)
        self.assertIn("Durable last_successful_scan timestamp does not match", workflow)

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
        self.assertIn('cron: "2-59/5 * * * *"', scheduler)
        self.assertIn("uses: ./.github/workflows/watchdog.yml", scheduler)
        self.assertNotIn("secrets: inherit", scheduler)
        self.assertIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", scheduler)
        self.assertIn("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}", scheduler)
        self.assertIn("actions: write", scheduler)
        self.assertIn('workflows: ["MM2 Scanner Scheduler"]', scheduler)
        self.assertIn("types: [completed]", scheduler)
        self.assertIn("recover-stale-scan:", scheduler)
        self.assertIn("20-minute cooldown", scheduler)
        self.assertEqual(scheduler.count('select(.head_branch == "main" and .status != "completed")'), 1)
        self.assertNotIn("continue-scan-loop:", scheduler)
        self.assertNotIn("status=$status&per_page=20", scheduler)

    def test_scheduler_manual_runs_do_not_chain_unless_explicitly_internal(self):
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("run-name: MM2 Scanner Scheduler", scheduler)
        self.assertIn("auto_chain:", scheduler)
        self.assertIn("default: false", scheduler.split("auto_chain:", 1)[1].split("permissions:", 1)[0])
        self.assertIn("inputs.dry_run && 'dry-run'", scheduler)
        self.assertIn("inputs.auto_chain && 'chain'", scheduler)
        self.assertLess(scheduler.index("inputs.dry_run && 'dry-run'"), scheduler.index("inputs.auto_chain && 'chain'"))
        self.assertIn("  continue-scan-loop:", scheduler)
        self.assertIn("needs: scan", scheduler)
        self.assertIn("needs.scan.result == 'success'", scheduler)
        self.assertIn("(inputs.auto_chain && !inputs.dry_run)", scheduler)
        self.assertIn("gh api \"/repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID\" --jq '.run_started_at'", scheduler)
        self.assertIn("timedelta(minutes=10)", scheduler)
        self.assertNotIn("github.run_started_at", scheduler)
        self.assertIn("CURRENT_RUN_ID: ${{ github.run_id }}", scheduler)
        self.assertIn("status != \"completed\"", scheduler)
        self.assertIn("((.id | tostring) != $current)", scheduler)
        self.assertIn("workflow_state", scheduler)
        self.assertIn("actions: write", scheduler)
        self.assertIn("inputs[auto_chain]=true", scheduler)
        self.assertIn("inputs[dry_run]=false", scheduler)
        watchdog = (ROOT / ".github/workflows/watchdog-scheduler.yml").read_text(encoding="utf-8")
        self.assertNotIn("continue-scan-loop:", watchdog)

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
                    if path.name == "watchdog-scheduler.yml":
                        allowed_jobs = {"enable-schedules", "recover-stale-scan"}
                    elif path.name == "scan-scheduler.yml":
                        allowed_jobs = {"continue-scan-loop"}
                    else:
                        allowed_jobs = set()
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


    def test_tools_that_import_the_tools_package_are_invoked_as_modules(self):
        scanner = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn("run: python -m tools.preflight --warn", scanner)
        self.assertNotIn("run: python tools/preflight.py", scanner)
        self.assertIn("run: python -m tools.weekly_report", weekly)
        self.assertNotIn("run: python tools/weekly_report.py", weekly)

    def test_weekly_report_keeps_cash_summary_separate_from_scanner_production_mode(self):
        workflow = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('CASH_FILTER: "false"', workflow)
        self.assertNotIn("CASH_PRICES_CSV", workflow)
        self.assertNotIn("CASH_MAX_AGE_DAYS", workflow)

    def test_bloxswaps_scanner_step_has_no_duplicate_environment_keys(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        preflight_step = workflow.split("      - name: Run scanner preflight checks", 1)[1].split("      - name:", 1)[0]
        step = workflow.split("      - name: Run BloxSwaps scanner", 1)[1].split("      - name:", 1)[0]
        self.assertIn("id: preflight", preflight_step)
        self.assertIn("TELEGRAM_CHAT_TYPE: ${{ steps.preflight.outputs.telegram_chat_type || 'unknown' }}", step)
        self.assertEqual(step.count("CASH_MAX_AGE_DAYS:"), 1)


if __name__ == "__main__":
    unittest.main()
