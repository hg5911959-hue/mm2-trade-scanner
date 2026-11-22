"""Build a compact weekly Telegram health report using standard library only."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.cash_market import load_robase_price_catalog
from tools.next_to_verify import format_blockers
from tools.preflight import collect_preflight_checks
from tools.telegram_transport import TELEGRAM_OPENER

DATA_DIR = Path("data")

def build_report(metrics, discord_status=None, key_state=None, daily_limit=60, now=None, cash_summary=None, blocker_summary=None, preflight_checks=None):
    current = now or datetime.now(UTC)
    days = [(current.date() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = [metrics.get(day, {}) for day in days]
    scans = sum(int(row.get("scans", 0)) for row in rows)
    failures = sum(int(row.get("scan_failures", 0)) for row in rows)
    sent = sum(int(row.get("alerts_sent", 0)) for row in rows)
    deferred = sum(int(row.get("alerts_deferred", 0)) for row in rows)
    requests = sum(int(row.get("rblxvalue_requests", 0)) for row in rows)
    success = 100.0 * max(0, scans - failures) / scans if scans else 0.0
    today = metrics.get(current.date().isoformat(), {})
    blocked = bool(key_state and (key_state.get("permanent") or float(key_state.get("blocked_until_epoch", 0) or 0) > current.timestamp()))
    key_label = "permanently blocked" if key_state and key_state.get("permanent") else ("blocked" if blocked else "not blocked")
    stale = bool(discord_status is None or discord_status.get("stale", True))
    trend = " | ".join(f"{day[5:]}:{int(metrics.get(day, {}).get('alerts_sent', 0))}" for day in days)
    return ("MM2 Market Intelligence — weekly report\n"
            f"Scan success: {success:.1f}% ({max(0, scans-failures)}/{scans})\n"
            f"Alerts sent: {sent} | deferred: {deferred}\n"
            f"RBLXValue requests today: {int(today.get('rblxvalue_requests', 0))}/{daily_limit}; "
            f"7-day total: {requests}\n"
            f"Key state: {key_label}\n"
            f"Discord manual import: {'STALE / missing' if stale else 'fresh'}\n"
            f"Cash prices: {cash_summary or 'catalogue unavailable'}\n"
            f"Last 7 days alerts: {trend}"
            + (f"\n\n{blocker_summary}" if blocker_summary else "")
            + ("\n\nPreflight checks: " + "; ".join(preflight_checks) if preflight_checks else "\n\nPreflight checks: PASS"))


def send_report(report, *, token, chat):
    """Send a weekly report and require Telegram's explicit API success."""
    if not token or not chat:
        raise RuntimeError("Telegram credentials are not configured")
    body = urllib.parse.urlencode({"chat_id": chat, "text": report}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body
    )
    with TELEGRAM_OPENER.open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("Telegram report delivery failed")
        try:
            result = json.loads(response.read(65536).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Telegram report returned an invalid response") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Telegram API rejected weekly report delivery")
    return True


def main():
    data = DATA_DIR
    try: metrics = json.loads((data / "metrics.json").read_text(encoding="utf-8"))
    except FileNotFoundError: metrics = {}
    except (OSError, ValueError): metrics = {}
    try: discord = json.loads((data / "discord_manual_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): discord = None
    try: key = json.loads((data / "api_key_blocked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): key = None
    cash_filter = os.environ.get("CASH_FILTER", "false").strip().casefold() in {"1", "true", "yes", "on"}
    cash_summary = "catalogue unavailable"
    blocker_summary = None
    if not cash_filter:
        cash_summary = "not included in this weekly report (production scanner remains cash-first)"
    else:
        try:
            cash = load_robase_price_catalog(
                data / "robase_prices.json", max_age_seconds=24 * 60 * 60
            )
            cash_summary = (
                f"verified={cash['verified_count']}/{cash['expected_count']}, "
                f"oldest_verified_at={cash['oldest_verified_at'] or 'none'}"
            )
            blocker_summary = format_blockers(metrics, cash, limit=10)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, OverflowError) as exc:
            cash_summary = f"unavailable ({type(exc).__name__})"
            blocker_summary = "Cash-price blocker ranking unavailable."
    try:
        preflight_checks = collect_preflight_checks(data_dir=data, env=os.environ)["checks"]
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, OverflowError) as exc:
        preflight_checks = ["preflight unavailable (" + type(exc).__name__ + ")"]
    report = build_report(
        metrics, discord, key, int(os.environ.get("RBLXVALUE_DAILY_REQUEST_LIMIT", "60")),
        cash_summary=cash_summary, blocker_summary=blocker_summary,
        preflight_checks=preflight_checks,
    )
    print("[weekly-report] " + report.replace("\n", " | "))
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    send_report(report, token=token, chat=chat)

if __name__ == "__main__":
    main()
