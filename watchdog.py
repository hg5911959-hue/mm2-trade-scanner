#!/usr/bin/env python3
"""Alert when the MM2 scanner has not completed a successful run recently."""
import json
import os
from datetime import datetime, timezone
from urllib.request import Request

import scanner


def should_alert(age, threshold=25, interval=5, reminder=360):
    """Alert in the first interval after threshold, then once per reminder window."""
    return (
        threshold <= age < threshold + interval
        or (age >= reminder and age % reminder < interval)
    )


def latest_success_age_minutes(token):
    if not token:
        raise RuntimeError("Missing GitHub Actions token.")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository or repository.count("/") != 1:
        raise RuntimeError("GITHUB_REPOSITORY must be set to owner/repository.")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/scan.yml/runs"
        "?status=success&per_page=1"
    )
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MM2-Scanner-Watchdog",
    })
    raw = scanner.request_with_retry(request, 20, "GitHub latest successful scan")
    payload = json.loads(raw.decode("utf-8"))
    runs = payload.get("workflow_runs", [])
    if not runs:
        raise RuntimeError("GitHub returned no successful scan workflow runs.")
    completed = datetime.fromisoformat(runs[0]["updated_at"].replace("Z", "+00:00"))
    age = max(0.0, (datetime.now(timezone.utc) - completed).total_seconds() / 60.0)
    return age, runs[0]


def latest_active_scan_age_minutes(token):
    if not token:
        raise RuntimeError("Missing GitHub Actions token.")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository or repository.count("/") != 1:
        raise RuntimeError("GITHUB_REPOSITORY must be set to owner/repository.")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/scan.yml/runs"
        "?status=in_progress&per_page=1"
    )
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MM2-Scanner-Watchdog",
    })
    raw = scanner.request_with_retry(request, 20, "GitHub active scan run")
    payload = json.loads(raw.decode("utf-8"))
    runs = payload.get("workflow_runs", [])
    if not runs:
        return None, None
    created = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
    age = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 60.0)
    return age, runs[0]


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    active_age, active_run = latest_active_scan_age_minutes(token)
    if active_run and active_age < 16:
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
