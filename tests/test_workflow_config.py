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

    def test_ruff_has_shared_project_and_precommit_configuration(self):
        config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        precommit = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        self.assertIn('target-version = "py312"', config)
        self.assertIn('select = ["E4", "E7", "E9", "F"]', config)
        self.assertIn("ruff-pre-commit", precommit)
        self.assertIn("rev: v0.17.5", precommit)

    def test_ci_scans_full_git_history_for_secrets_with_pinned_gitleaks(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("secret-scan:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("GITLEAKS_LOG_OPTS: --all", workflow)
        self.assertIn("gitleaks/gitleaks-action@ff98106e4c7b2bc287b24eaf42907196329070c7", workflow)
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
        self.assertIn('          MAX_PENDING_RECHECK_PER_SCAN: "50"\n', workflow)
        self.assertIn('      CASH_FILTER: "false" # set to true only after fresh verified prices exist\n', workflow)
        weekly = (ROOT / ".github/workflows/weekly-report.yml").read_text(encoding="utf-8")
        self.assertIn('      CASH_FILTER: "false" # set to true only after fresh verified prices exist\n', weekly)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", workflow)
        self.assertIn("env.CASH_FILTER == '1' || env.CASH_FILTER == 'true'", weekly)
        self.assertIn('          RBLXVALUE_META_POLL_ENABLED: "true"\n', workflow)
        self.assertIn('          RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY: "6"\n', workflow)
        self.assertIn("MM2_SCAN_DURATION_SECONDS: ${{ inputs.discord_only && '0' || '540' }}", workflow)
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
        self.assertIn("enable_discord:", scheduler)
        self.assertIn("contains(github.event.head_commit.message, '[discord-dry-run]')", scheduler)
        self.assertIn("github.event_name != 'push'", scheduler)
        self.assertIn("contains(github.event.head_commit.message, '[rblxvalue-key-check]')", scheduler)
        self.assertIn("contains(github.event.head_commit.message, '[post-cache-recovery]')", scheduler)
        self.assertIn("contents: write", scheduler)
        self.assertIn("actions: read", scheduler)
        self.assertNotIn("workflow_run:", scheduler)
        self.assertNotIn("enable-schedules:", scheduler)


    def test_cash_market_and_telegram_features_are_disabled_and_private_state_is_encrypted(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        for flag in ("TELEGRAM_COMMANDS_ENABLED", "INVENTORY_ENABLED"):
            self.assertIn(f'{flag}: "false"', workflow)
        self.assertIn("MARKET_BOOK_ENABLED: ${{ inputs.enable_discord && 'true' || 'false' }}", workflow)
        self.assertIn("PRIVATE_STATE_ENABLED: ${{ inputs.enable_discord && 'true' || 'false' }}", workflow)
        for trigger in ("  workflow_call:", "  workflow_dispatch:"):
            inputs = workflow.split(trigger, 1)[1].split("\npermissions:", 1)[0]
            self.assertIn("enable_discord:", inputs)
            self.assertIn("default: false", inputs)
        self.assertIn("Decrypt private market state", workflow)
        self.assertIn("private_state.py decrypt", workflow)
        self.assertIn("private_state.py encrypt", workflow)
        self.assertIn("MM2_PRIVATE_STATE_KEY: ${{ secrets.TELEGRAM_BOT_TOKEN }}", workflow)
        decrypt_pos = workflow.index("Decrypt private market state")
        scan_pos = workflow.index("Run BloxSwaps scanner")
        encrypt_pos = workflow.index("private_state.py encrypt")
        artifact_pos = workflow.index("mkdir -p state-bundle/data")
        self.assertLess(decrypt_pos, scan_pos)
        self.assertLess(encrypt_pos, artifact_pos)
        self.assertIn("private_state.enc", workflow)
        self.assertIn("discord_alert_state.json", workflow)
        self.assertIn(
            "rm -f data/cash_market_offers.json data/inventory.json data/telegram_update_state.json "
            "data/telegram_last_run.json data/market_book_history.json data/market_alert_state.json "
            "data/discord_alert_state.json",
            workflow,
        )
        validator = (ROOT / "tools/validate_state.py").read_text(encoding="utf-8")
        self.assertIn("private Telegram/market state", validator)
        self.assertIn("discord_alert_state.json", validator)
        private_state = (ROOT / "private_state.py").read_text(encoding="utf-8")
        self.assertIn('"discord_alert_state.json"', private_state)

    def test_encrypted_private_state_survives_failed_scans_without_plaintext(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("Persist encrypted private state after failed scan", workflow)
        self.assertIn("needs.scan.result != 'success'", workflow)
        self.assertIn("state-artifact/data/private_state.enc", workflow)
        self.assertIn("Persisted encrypted private state only", workflow)
        self.assertIn("data/private_state.enc", workflow)

    def test_encrypted_private_state_is_authenticated_before_persistence(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("id: verify-private-state", workflow)
        self.assertIn("private_state.py decrypt --input state-artifact/data/private_state.enc", workflow)
        failed_scan = workflow.split("Persist encrypted private state after failed scan", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.verify-private-state.outcome == 'success'", failed_scan)
        persist = workflow.split("Persist state branch", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.verify-private-state.outcome == 'success'", persist)
        self.assertIn("steps.verify-private-state.outcome == 'skipped'", persist)

    def test_every_telegram_send_path_rejects_redirects(self):
        scanner = (ROOT / "scanner.py").read_text(encoding="utf-8")
        weekly = (ROOT / "tools/weekly_report.py").read_text(encoding="utf-8")
        telegram_test = (ROOT / ".github/workflows/telegram-test.yml").read_text(encoding="utf-8")
        self.assertIn("class _RejectTelegramRedirects", scanner)
        self.assertIn("_TELEGRAM_OPENER.open(req, timeout=20)", scanner)
        self.assertIn("class _RejectTelegramRedirects", weekly)
        self.assertIn("_TELEGRAM_OPENER.open(request, timeout=20)", weekly)
        self.assertIn("class RejectTelegramRedirects", telegram_test)
        self.assertIn("opener.open(request, timeout=20)", telegram_test)
        self.assertNotIn("urllib.request.urlopen(request", telegram_test)

    def test_rblxvalue_metadata_polling_is_bounded_to_six_hours(self):
        scanner = (ROOT / "scanner.py").read_text(encoding="utf-8")
        self.assertIn("RBLXVALUE_META_POLL_INTERVAL_SECONDS = 6 * 60 * 60", scanner)
        self.assertIn("VALUES_REVALIDATION_INTERVAL_SECONDS = 12 * 60 * 60", scanner)

    def test_rblxvalue_connection_check_is_manual_and_uses_shared_budget(self):
        workflow = (ROOT / ".github/workflows/rblxvalue-connection-test.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertIn("group: mm2-trade-scanner", workflow)
        self.assertIn("reserve_request(key=key", workflow)
        self.assertIn("Restore shared API safety ledger", workflow)
        self.assertIn("Persist shared API safety ledger", workflow)
        self.assertIn("git push origin HEAD:state", workflow)
        self.assertIn("class RejectRblxValueRedirects", workflow)
        self.assertIn("urllib.request.build_opener(RejectRblxValueRedirects())", workflow)
        self.assertIn("with opener.open(request, timeout=20)", workflow)
        self.assertIn("git add data/rblxvalue_request_budget.json", workflow)
        self.assertIn("git rm -f --ignore-unmatch data/api_key_blocked.json", workflow)

    def test_discord_collector_uses_stdlib_without_installing_discord_py(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        collector = (ROOT / "discord_collector.py").read_text(encoding="utf-8")
        self.assertIn("python -u discord_collector.py", workflow)
        self.assertIn("if: env.PRIVATE_STATE_ENABLED == 'true'", workflow)
        self.assertIn("Discord opt-in requires DISCORD_BOT_TOKEN or at least one authorized manual-import secret.", workflow)
        self.assertIn("No Discord bot token configured; processing only owner-supplied manual imports.", workflow)
        self.assertIn("Discord channel collection was incomplete; refusing to process partial live messages.", workflow)
        self.assertIn("rm -f data/discord_messages.json data/discord_messages.json.tmp", workflow)
        self.assertNotIn("pip install", workflow)
        self.assertNotIn("discord.py", workflow)
        self.assertNotIn("import discord", collector)
        self.assertIn('method="GET"', collector)
        self.assertIn("Authorization", collector)
        self.assertIn("class _RejectDiscordRedirects", collector)

    def test_manual_scan_dispatch_defaults_to_dry_run(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        dispatch = workflow.split("  workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("default: true", dispatch)
        self.assertIn("default: true", scheduler)
        self.assertIn("dry_run: ${{ github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && inputs.dry_run) || false }}", scheduler)
        self.assertIn("contains(github.event.head_commit.message, '[rblxvalue-key-check]')", scheduler)
        self.assertIn("verify_rblxvalue_key: ${{ (github.event_name == 'push' && contains(github.event.head_commit.message, '[rblxvalue-key-check]')) || (github.event_name == 'workflow_dispatch' && inputs.verify_rblxvalue_key) || false }}", scheduler)

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
                    if path.name in {"scan.yml", "scan-scheduler.yml"}:
                        self.assertRegex(root_permissions.group(1), r"(?m)^  contents:\s*write\s*$")
                        self.assertNotRegex(root_permissions.group(1), r"(?m)^  (?!contents:)[a-z_]+:\s*write\b")
                    else:
                        self.assertNotRegex(root_permissions.group(1), r"(?m):\s*write\b")
                for match in re.finditer(r"(?m)^\s*actions:\s*write\s*$", text):
                    prefix = text[:match.start()]
                    job_match = list(re.finditer(r"(?m)^  ([a-zA-Z0-9_-]+):\s*$", prefix))
                    job_name = job_match[-1].group(1) if job_match else ""
                    self.assertIn(
                        (path.name, job_name),
                        {
                            ("watchdog-scheduler.yml", "enable-schedules"),
                            ("rblxvalue-connection-test.yml", "connection-test"),
                        },
                    )

    def test_dashboard_skips_deploy_when_pages_is_not_enabled(self):
        workflow = (ROOT / ".github/workflows/dashboard-pages.yml").read_text(encoding="utf-8")
        self.assertIn("pages_enabled: ${{ steps.pages-preflight.outputs.enabled }}", workflow)
        self.assertIn("id: pages-preflight", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("request POST", workflow)
        self.assertIn('"build_type":"workflow"', workflow)
        self.assertIn("if: needs.build.outputs.pages_enabled == 'true'", workflow)
        self.assertIn("Repository administrator permission may be required", workflow)


    def test_scanner_step_environment_has_no_duplicate_keys(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scanner_step = workflow.split("      - name: Run BloxSwaps scanner", 1)[1].split("      - name:", 1)[0]
        env_block = scanner_step.split("        env:", 1)[1].split("        run:", 1)[0]
        keys = [line.strip().split(":", 1)[0] for line in env_block.splitlines() if line.startswith("          ")]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        self.assertEqual(duplicates, [])


    def test_preflight_runs_as_module_to_resolve_repository_imports(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("run: python -m tools.preflight --warn", workflow)
        self.assertNotIn("run: python tools/preflight.py --warn", workflow)


    def test_discord_enablement_requires_explicit_opt_in_and_private_state_key(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("default: false", workflow.split("enable_discord:", 1)[1].split("permissions:", 1)[0])
        dispatch = workflow.split("  workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("enable_discord:", dispatch)
        self.assertIn("default: false", dispatch)
        self.assertIn("if: env.PRIVATE_STATE_ENABLED == 'true'", workflow)
        self.assertIn('if [ "${#MM2_PRIVATE_STATE_KEY}" -lt 32 ]; then', workflow)
        self.assertIn("inputs.enable_discord", scheduler)
        self.assertIn("discord_only:", scheduler)
        for trigger in ("  workflow_call:", "  workflow_dispatch:"):
            trigger_inputs = workflow.split(trigger, 1)[1].split("\npermissions:", 1)[0]
            self.assertIn("discord_only:", trigger_inputs)
            self.assertIn("default: false", trigger_inputs)
        self.assertIn("contains(github.event.head_commit.message, '[discord-dry-run]')", scheduler)
        self.assertIn("DISCORD_ONLY: ${{ inputs.discord_only && 'true' || 'false' }}", workflow)
        self.assertIn("always() && inputs.discord_only != true && env.HEALTHCHECKS_PING_URL != ''", workflow)
        self.assertIn("if discord_only and not dry_run", (ROOT / "scanner.py").read_text(encoding="utf-8"))
        scanner_source = (ROOT / "scanner.py").read_text(encoding="utf-8")
        self.assertIn("Discord-only cash-market dry-run completed", scanner_source)
        self.assertIn("if discord_only and not dry_run", scanner_source)
        self.assertIn("DRY_RUN: ${{ inputs.dry_run && 'true' || 'false' }}", workflow)

    def test_cache_recovery_job_only_persists_validated_public_values(self):
        scheduler = (ROOT / ".github/workflows/scan-scheduler.yml").read_text(encoding="utf-8")
        self.assertIn("[recover-rblx-cache:44635098506]", scheduler)
        self.assertIn("name: Recover validated RBLXValue cache", scheduler)
        self.assertIn("validate_state.py --directory state-artifact/data --rblxvalue-cache-only", scheduler)
        self.assertIn('cp "$GITHUB_WORKSPACE/state-artifact/data/supreme_values.json" data/supreme_values.json', scheduler)
        self.assertIn('cp "$GITHUB_WORKSPACE/state-artifact/data/rblxvalue_meta.json" data/rblxvalue_meta.json', scheduler)
        self.assertNotIn("cp state-artifact/data/rblxvalue_request_budget.json", scheduler)


if __name__ == "__main__":
    unittest.main()
