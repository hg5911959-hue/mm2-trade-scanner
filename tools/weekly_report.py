"""Build a compact weekly Telegram health report using standard library only."""
from __future__ import annotations
import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tools.cash_market import cash_price_is_fresh, load_cash_price_catalog

def build_report(metrics, discord_status=None, key_state=None, daily_limit=60, now=None, cash_summary=None):
    current = now or datetime.now(timezone.utc)
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
            f"Last 7 days alerts: {trend}")

def main():
    data = Path("data")
    try: metrics = json.loads((data / "metrics.json").read_text(encoding="utf-8"))
    except FileNotFoundError: metrics = {}
    except (OSError, ValueError): metrics = {}
    try: discord = json.loads((data / "discord_manual_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): discord = None
    try: key = json.loads((data / "api_key_blocked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): key = None
    cash_summary = None
    try:
        max_age = int(os.environ.get("CASH_MAX_AGE_DAYS", "14"))
        cash = load_cash_price_catalog(data / "cash_prices.csv", max_age_days=max_age)
        fresh = [row for row in cash["items"].values() if cash_price_is_fresh(row, max_age)]
        cash_summary = (
            f"verified={len(fresh)}, expiring<=3d={cash['expiring_within_3_days']}, "
            f"oldest_verified_at={cash['oldest_verified_at'] or 'none'}"
        )
    except Exception as exc:
        cash_summary = f"unavailable ({type(exc).__name__})"
    report = build_report(metrics, discord, key, int(os.environ.get("RBLXVALUE_DAILY_REQUEST_LIMIT", "60")), cash_summary=cash_summary)
    print("[weekly-report] " + report.replace("\n", " | "))
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        body = urllib.parse.urlencode({"chat_id": chat, "text": report}).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200: raise RuntimeError("Telegram report delivery failed")
    else:
        raise RuntimeError("Telegram credentials are not configured")

if __name__ == "__main__":
    main()
