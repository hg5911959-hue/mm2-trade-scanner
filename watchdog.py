#!/usr/bin/env python3
"""Alert when the MM2 scanner has not completed a successful run recently."""
import json
import os
from datetime import datetime, timezone
from urllib.request import Request

import scanner


# A scan may run for 20 minutes, followed by a 5-minute state-persistence job.
ACTIVE_RUN_GRACE_MINUTES = 27
SCAN_WORKFLOW_FILES = ("scan-scheduler.yml", "scan.yml")


def should_alert(age, threshold=20, interval=5, reminder=30):
    """Alert at the threshold, then every reminder window while scans stay stale."""
    return (
        threshold <= age < threshold + interval
        or (age >= reminder and age % reminder < interval)
    )


def should_suppress_stale_success(active_age, active_run, grace=ACTIVE_RUN_GRACE_MINUTES):
    """Suppress stale-success alerts while a scan workflow is within its runtime budget."""
    return active_run is not None and active_age is not None and active_age < grace


def _repository():
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository or repository.count("/") != 1:
        raise RuntimeError("GITHUB_REPOSITORY must be set to owner/repository.")
    return repository


def _workflow_runs(token, workflow_file, query, label):
    if not token:
        raise RuntimeError("Missing GitHub Actions token.")
    url = (
        f"https://api.github.com/repos/{_repository()}/actions/workflows/{workflow_file}/runs"
        f"?{query}"
    )
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MM2-Scanner-Watchdog",
    })
    raw = scanner.request_with_retry(request, 20, label)
    payload = json.loads(raw.decode("utf-8"))
    runs = payload.get("workflow_runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("GitHub returned an invalid workflow-runs payload.")
    return runs


def latest_success_age_minutes(token):
    """Find the latest successful scheduled or manually dispatched scan run."""
    runs = []
    for workflow_file in SCAN_WORKFLOW_FILES:
        runs.extend(_workflow_runs(
            token, workflow_file, "status=completed&per_page=100",
            f"GitHub completed {workflow_file} runs",
        ))
    successful = [run for run in runs if isinstance(run, dict) and run.get("conclusion") == "success"]
    if not successful:
        raise RuntimeError("GitHub returned no successful completed scan workflow runs.")
    successful.sort(key=lambda run: run.get("completed_at") or "", reverse=True)
    completed_at = successful[0].get("completed_at")
    if not completed_at:
        raise RuntimeError("Latest successful scan is missing completed_at.")
    completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    age = max(0.0, (datetime.now(timezone.utc) - completed).total_seconds() / 60.0)
    return age, successful[0]


def latest_active_scan_age_minutes(token):
    """Return the age of the oldest queued or running scan, if any."""
    runs = []
    for status in ("in_progress", "queued"):
        for workflow_file in SCAN_WORKFLOW_FILES:
            runs.extend(_workflow_runs(
                token, workflow_file, f"status={status}&per_page=20",
                f"GitHub {status} {workflow_file} runs",
            ))
    active = [
        run for run in runs
        if isinstance(run, dict) and run.get("status") in {"in_progress", "queued"}
    ]
    if not active:
        return None, None
    active.sort(key=lambda run: run.get("created_at") or "")
    run = active[0]
    created_at = run.get("created_at")
    if not created_at:
        raise RuntimeError("Active or queued scan is missing created_at.")
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    age = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 60.0)
    return age, run


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    active_age, active_run = latest_active_scan_age_minutes(token)
    if should_suppress_stale_success(active_age, active_run):
        print(
            f"[watchdog] Scan job is active ({active_age:.1f} minutes); "
            "suppressing stale-success alert."
        )
        return
    age, run = latest_success_age_minutes(token)
    print(f"[watchdog] Latest successful scan age: {age:.1f} minutes")
    if should_alert(age):
        scanner.send_telegram(
            "MM2 TRADE SCANNER WATCHDOG\n"
            f"No successful scan for {age:.0f} minutes.\n"
            f"Last successful run: {run.get('html_url', 'GitHub Actions')}\n"
            "Check GitHub Actions for delayed or failed scheduled runs."
        )
        print("[watchdog] Telegram alert sent")
    else:
        print("[watchdog] No alert due")


if __name__ == "__main__":
    main()
