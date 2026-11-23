"""Fail-safe preflight checks for the MM2 cash-first scanner."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from tools.cash_market import load_robase_price_catalog
from tools.telegram_transport import TELEGRAM_OPENER

MAINTENANCE_FILE = Path("data/maintenance_alerts.json")
SUPREME_FILE = Path("data/supreme_values.json")
BREAKER_FILE = Path("data/api_key_blocked.json")
BUDGET_FILE = Path("data/rblxvalue_request_budget.json")


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


def check_telegram_connection(*, env=None):
    """Verify bot authentication and target-chat access without sending a message."""
    environment = os.environ if env is None else env
    token = str(environment.get("TELEGRAM_BOT_TOKEN", "")).strip()
    chat = str(environment.get("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat:
        return ["Telegram credentials are missing"]

    for method, params, failure in (
        ("getMe", {}, "Telegram Bot API rejected the bot token"),
        ("getChat", {"chat_id": chat}, "Telegram bot cannot access the configured target chat"),
    ):
        url = f"https://api.telegram.org/bot{token}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with TELEGRAM_OPENER.open(request, timeout=10) as response:
                if response.status != 200:
                    return [failure]
                payload = json.loads(response.read(8192).decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                return [failure]
        except (OSError, TimeoutError, ValueError, urllib.error.URLError):
            return [f"{failure} (connection or response error)"]
    print("[preflight] Telegram getMe and getChat checks passed; no message was sent.")
    return []


def _collect_budget_warning(data_dir, environment, current):
    """Read the local rolling ledger; never make an API request to check its usage."""
    path = Path(data_dir) / BUDGET_FILE.name
    if not path.exists():
        return None, None
    state = _read_json(path)
    if not isinstance(state, dict):
        return None, "RBLXValue request budget ledger is unreadable"
    timestamps = state.get("request_timestamps_epoch", [])
    limit = state.get("limit", environment.get("RBLXVALUE_DAILY_REQUEST_LIMIT", "60"))
    if (
        not isinstance(timestamps, list)
        or len(timestamps) > 60
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 60
    ):
        return None, "RBLXValue request budget ledger is invalid"
    cutoff = current.timestamp() - 86400
    used = 0
    for value in timestamps:
        if isinstance(value, bool):
            return None, "RBLXValue request budget ledger has an invalid timestamp"
        try:
            stamp = float(value)
        except (TypeError, ValueError, OverflowError):
            return None, "RBLXValue request budget ledger has an invalid timestamp"
        if not math.isfinite(stamp) or stamp <= 0:
            return None, "RBLXValue request budget ledger has an invalid timestamp"
        # Future timestamps are counted, matching the API guard's clock-skew policy.
        if stamp > cutoff:
            used += 1
    threshold = math.ceil(limit * 0.8)
    if used >= threshold:
        return {"used": used, "limit": limit, "threshold": threshold}, None
    return None, None


def send_budget_warning(warning, *, data_dir="data", env=None, now=None):
    """Send a separately throttled 80%-budget warning without spending API quota."""
    if not isinstance(warning, dict):
        return False
    environment = os.environ if env is None else env
    token = str(environment.get("TELEGRAM_BOT_TOKEN", "")).strip()
    chat = str(environment.get("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat:
        return False
    current = now or datetime.now(UTC)
    key = "rblxvalue-budget-80pct"
    path = Path(data_dir) / MAINTENANCE_FILE.name
    state = _read_json(path)
    if not isinstance(state, dict):
        state = {}
    try:
        last_sent = float(state.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        last_sent = 0
    if last_sent > 0 and current.timestamp() - last_sent < 12 * 60 * 60:
        return False

    used = int(warning["used"])
    limit = int(warning["limit"])
    threshold = int(warning["threshold"])
    state[key] = current.timestamp()
    _atomic_json(path, state)
    message = (
        "MM2 RBLXVALUE API BUDGET WARNING\\n"
        f"Rolling 24-hour usage reached {used}/{limit} requests "
        f"(80% warning threshold: {threshold}/{limit}).\\n"
        "The scanner still enforces the hard request limit; this notice itself uses no RBLXValue request."
    )
    body = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage", data=body
    )
    try:
        with TELEGRAM_OPENER.open(request, timeout=15) as response:
            if response.status != 200:
                state.pop(key, None)
                _atomic_json(path, state)
                return False
            result = json.loads(response.read(4096).decode("utf-8"))
            if not isinstance(result, dict) or result.get("ok") is not True:
                state.pop(key, None)
                _atomic_json(path, state)
                return False
        print(f"[preflight] RBLXValue API budget warning sent: {used}/{limit}")
        return True
    except urllib.error.HTTPError as exc:
        # A definite client rejection can be retried next scan; 429/5xx remain throttled.
        if 400 <= exc.code < 500 and exc.code != 429:
            state.pop(key, None)
            _atomic_json(path, state)
        print("[preflight] RBLXValue budget notice failed: HTTP " + str(exc.code))
        return False
    except (OSError, TimeoutError, ValueError) as exc:
        # Delivery may be uncertain; keep the pre-send guard to avoid duplicate notices.
        print("[preflight] RBLXValue budget notice failed: " + type(exc).__name__)
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

    cash_filter = environment.get("CASH_FILTER", "false").strip().casefold() in {"1", "true", "yes", "on"}
    fresh = None
    if cash_filter:
        try:
            snapshot_path = catalog_path or root / "robase_prices.json"
            catalog = load_robase_price_catalog(
                snapshot_path, int(environment.get("CASH_MAX_AGE_DAYS", "1")), now=current,
            )
            fresh = catalog["verified_count"]
            if fresh < minimum:
                checks.append("fresh verified ROBASE cash prices: " + str(fresh) + " / " + str(minimum))
        except (OSError, ValueError, RuntimeError, TypeError):
            fresh = 0
            checks.append("ROBASE cash-price snapshot unavailable or invalid")

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
    elif environment.get("TELEGRAM_PREFLIGHT_CHECK", "false").strip().casefold() in {"1", "true", "yes", "on"}:
        checks.extend(check_telegram_connection(env=environment))

    checker = state_checker or _state_branch_readable
    try:
        readable = bool(checker())
    except Exception:  # noqa: BLE001 - state-check failures must fail closed
        readable = False
    if not readable:
        checks.append("state branch is unreadable")

    budget_warning, budget_error = _collect_budget_warning(root, environment, current)
    if budget_error:
        checks.append(budget_error)

    return {
        "checks": checks,
        "fresh_verified_rows": fresh,
        "minimum_verified_rows": minimum,
        "expiring_within_3_days": catalog.get("expiring_within_3_days", 0) if cash_filter and "catalog" in locals() else 0,
        "rblxvalue_budget_warning": budget_warning,
    }


def _atomic_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    if (
        isinstance(last, (int, float))
        and not isinstance(last, bool)
        and datetime.fromtimestamp(last, UTC).date() == current.date()
    ):
        return False
    message = "MM2 PREFLIGHT WARNING\n" + "\n".join("- " + check for check in checks)
    body = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage", data=body
    )
    try:
        with TELEGRAM_OPENER.open(request, timeout=15) as response:
            if response.status != 200:
                return False
            result = json.loads(response.read(4096).decode("utf-8"))
            if not isinstance(result, dict) or result.get("ok") is not True:
                return False
        state["preflight-daily"] = current.timestamp()
        _atomic_json(path, state)
        return True
    except (OSError, TimeoutError, ValueError) as exc:
        print("[preflight] Telegram notice failed: " + type(exc).__name__)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warn", action="store_true", help="report failures but keep the scan workflow running")
    args = parser.parse_args(argv)
    result = collect_preflight_checks()
    budget_warning = result.get("rblxvalue_budget_warning")
    if result["checks"] or budget_warning:
        for check in result["checks"]:
            print("[preflight] FAIL: " + check)
        if budget_warning:
            print(
                "[preflight] WARN: RBLXValue rolling 24-hour API budget "
                f"{budget_warning['used']}/{budget_warning['limit']} "
                f"(80% threshold {budget_warning['threshold']})"
            )
        if args.warn:
            notices_enabled = os.environ.get(
                "PREFLIGHT_SEND_NOTICES", "true"
            ).strip().casefold() in {"1", "true", "yes", "on"}
            if notices_enabled:
                if result["checks"]:
                    send_daily_notice(result["checks"])
                if budget_warning:
                    send_budget_warning(budget_warning)
            else:
                print("[preflight] Telegram notice suppressed for dry-run execution")
            return 0
        return 1
    print("[preflight] PASS: all configured checks are healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
