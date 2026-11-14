"""Fail-safe preflight checks for the MM2 cash-first scanner."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from tools.cash_market import cash_price_is_fresh, load_cash_price_catalog

MAINTENANCE_FILE = Path("data/maintenance_alerts.json")
SUPREME_FILE = Path("data/supreme_values.json")
BREAKER_FILE = Path("data/api_key_blocked.json")
CASH_FILE = Path("data/cash_prices.csv")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _state_branch_readable():
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", "refs/heads/state"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def collect_preflight_checks(data_dir="data", *, env=None, now=None,
                             state_checker=None, catalog_path=None):
    environment = os.environ if env is None else env
    root = Path(data_dir)
    current = now or datetime.now(UTC)
    checks = []
    try:
        minimum = int(environment.get("CASH_MIN_VERIFIED_ROWS", "8"))
        if minimum < 0:
            raise ValueError
    except (TypeError, ValueError):
        minimum = 8
        checks.append("CASH_MIN_VERIFIED_ROWS is invalid")

    try:
        private_csv = environment.get("CASH_PRICES_CSV", "")
        if catalog_path is not None:
            catalog = load_cash_price_catalog(catalog_path, int(environment.get("CASH_MAX_AGE_DAYS", "14")), now=current)
        elif private_csv:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "cash_prices.csv"
                path.write_text(private_csv, encoding="utf-8")
                catalog = load_cash_price_catalog(path, int(environment.get("CASH_MAX_AGE_DAYS", "14")), now=current)
        else:
            catalog = load_cash_price_catalog(root / "cash_prices.csv", int(environment.get("CASH_MAX_AGE_DAYS", "14")), now=current)
        fresh = sum(
            cash_price_is_fresh(row, int(environment.get("CASH_MAX_AGE_DAYS", "14")), now=current)
            for row in catalog["items"].values()
        )
        if fresh < minimum:
            checks.append("fresh verified cash prices: " + str(fresh) + " / " + str(minimum))
    except (OSError, ValueError, RuntimeError, TypeError):
        fresh = 0
        checks.append("cash-price catalogue unavailable or invalid")

    breaker = _read_json(root / BREAKER_FILE.name)
    if breaker is not None:
        blocked = bool(
            isinstance(breaker, dict)
            and (breaker.get("permanent") is True
                 or (isinstance(breaker.get("blocked_until_epoch"), (int, float))
                     and not isinstance(breaker.get("blocked_until_epoch"), bool)
                     and breaker["blocked_until_epoch"] > current.timestamp()))
        )
        if blocked:
            checks.append("RBLXValue API key is blocked")
    elif (root / BREAKER_FILE.name).exists():
        checks.append("RBLXValue breaker state is unreadable")

    supreme = _read_json(root / SUPREME_FILE.name)
    fetched_at = supreme.get("fetched_at") if isinstance(supreme, dict) else None
    if (isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float))
            or not math.isfinite(fetched_at) or current.timestamp() - fetched_at > 86400
            or fetched_at > current.timestamp()):
        checks.append("Supreme cache is missing, invalid, or older than 24 hours")

    if not environment.get("TELEGRAM_BOT_TOKEN") or not environment.get("TELEGRAM_CHAT_ID"):
        checks.append("Telegram credentials are missing")

    checker = state_checker or _state_branch_readable
    try:
        readable = bool(checker())
    except Exception:
        readable = False
    if not readable:
        checks.append("state branch is unreadable")

    return {
        "checks": checks,
        "fresh_verified_rows": fresh,
        "minimum_verified_rows": minimum,
        "expiring_within_3_days": catalog.get("expiring_within_3_days", 0) if "catalog" in locals() else 0,
    }


def _atomic_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def send_daily_notice(checks, *, data_dir="data", env=None, now=None):
    if not checks:
        return False
    environment = os.environ if env is None else env
    token = environment.get("TELEGRAM_BOT_TOKEN", "")
    chat = environment.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    current = now or datetime.now(UTC)
    path = Path(data_dir) / MAINTENANCE_FILE.name
    state = _read_json(path) or {}
    if not isinstance(state, dict):
        state = {}
    last = state.get("preflight-daily")
    if isinstance(last, (int, float)) and not isinstance(last, bool):
        if datetime.fromtimestamp(last, UTC).date() == current.date():
            return False
    state["preflight-daily"] = current.timestamp()
    _atomic_json(path, state)
    message = "MM2 PREFLIGHT WARNING\n" + "\n".join("- " + check for check in checks)
    body = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage", data=body
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except Exception as exc:
        print("[preflight] Telegram notice failed: " + type(exc).__name__)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warn", action="store_true", help="report failures but keep the scan workflow running")
    args = parser.parse_args(argv)
    result = collect_preflight_checks()
    if result["checks"]:
        for check in result["checks"]:
            print("[preflight] FAIL: " + check)
        if args.warn:
            send_daily_notice(result["checks"])
            return 0
        return 1
    print("[preflight] PASS: all configured checks are healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
