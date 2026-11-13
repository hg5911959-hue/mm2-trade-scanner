"""Persistent request budget and circuit breaker for RBLXValue API calls."""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUDGET_FILE = Path("data/rblxvalue_request_budget.json")
BLOCK_FILE = Path("data/api_key_blocked.json")
DAILY_REQUEST_LIMIT = max(1, int(os.environ.get("RBLXVALUE_DAILY_REQUEST_LIMIT", "60")))
BLOCK_SECONDS = 6 * 60 * 60
ALERT_INTERVAL_SECONDS = 24 * 60 * 60
ALERT_RETRY_SECONDS = 5 * 60
REQUESTS_THIS_RUN = 0


class RblxValueGuardError(RuntimeError):
    """Base exception for safe API guard stops."""


class RblxValueKeyBlocked(RblxValueGuardError):
    """Credential is blocked or currently inside its circuit-breaker window."""


class RblxValueBudgetExceeded(RblxValueGuardError):
    """The persistent rolling 24-hour API request budget is exhausted."""


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).isoformat()


def key_fingerprint(key: str | None = None) -> str:
    secret = (os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key).strip()
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16] if secret else "missing"


def _read_json(path: Path) -> dict[str, Any]:
    """Read guard state without silently resetting a corrupt safety ledger."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RblxValueGuardError(
            f"[rblxvalue-state] cannot read safety state file {path.name}; refusing API request"
        ) from exc
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RblxValueGuardError(
            f"[rblxvalue-state] safety state file {path.name} is invalid JSON; refusing API request"
        ) from exc
    if not isinstance(value, dict):
        raise RblxValueGuardError(
            f"[rblxvalue-state] safety state file {path.name} has an invalid root; refusing API request"
        )
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _active_block(now: float, key: str, block_path: Path) -> dict[str, Any] | None:
    state = _read_json(block_path)
    if not state:
        return None
    fingerprint = key_fingerprint(key)
    if state.get("key_fingerprint") != fingerprint:
        # A rotated credential must not inherit the previous key's breaker.
        try:
            block_path.unlink()
        except FileNotFoundError:
            pass
        return None
    try:
        until = float(state.get("blocked_until_epoch", 0))
    except (TypeError, ValueError):
        until = 0
    return state if until > now else None


def is_key_blocked(*, now: float | None = None, key: str | None = None,
                   block_path: Path | None = None) -> bool:
    current = time.time() if now is None else float(now)
    secret = os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key
    return _active_block(current, secret, block_path or BLOCK_FILE) is not None


def mark_key_blocked(reason: str, *, now: float | None = None, key: str | None = None,
                     block_path: Path | None = None) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    secret = os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key
    path = block_path or BLOCK_FILE
    old = _read_json(path)
    fingerprint = key_fingerprint(secret)
    same_key = old.get("key_fingerprint") == fingerprint
    state = {
        "schema_version": 1,
        "blocked_at_utc": _now_iso(current),
        "blocked_until_utc": _now_iso(current + BLOCK_SECONDS),
        "blocked_until_epoch": current + BLOCK_SECONDS,
        "key_fingerprint": fingerprint,
        "reason": str(reason)[:120],
        "last_alert_attempt_epoch": old.get("last_alert_attempt_epoch") if same_key else None,
        "last_alert_sent_epoch": old.get("last_alert_sent_epoch") if same_key else None,
    }
    _atomic_json(path, state)
    return state


def claim_block_alert(*, now: float | None = None, key: str | None = None,
                      block_path: Path | None = None) -> bool:
    current = time.time() if now is None else float(now)
    secret = os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key
    path = block_path or BLOCK_FILE
    state = _read_json(path)
    if not state or state.get("key_fingerprint") != key_fingerprint(secret):
        return False
    try:
        last_sent = float(state.get("last_alert_sent_epoch") or 0)
    except (TypeError, ValueError):
        last_sent = 0
    if last_sent > 0 and current - last_sent < ALERT_INTERVAL_SECONDS:
        return False
    try:
        last_attempt = float(state.get("last_alert_attempt_epoch") or 0)
    except (TypeError, ValueError):
        last_attempt = 0
    if last_attempt > 0 and current - last_attempt < ALERT_RETRY_SECONDS:
        return False
    state["last_alert_attempt_epoch"] = current
    state["last_alert_attempt_at_utc"] = _now_iso(current)
    _atomic_json(path, state)
    return True


def mark_block_alert_sent(*, now: float | None = None, key: str | None = None,
                          block_path: Path | None = None) -> bool:
    """Record successful Telegram delivery; failed attempts remain retryable."""
    current = time.time() if now is None else float(now)
    secret = os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key
    path = block_path or BLOCK_FILE
    state = _read_json(path)
    if not state or state.get("key_fingerprint") != key_fingerprint(secret):
        return False
    state["last_alert_sent_epoch"] = current
    state["last_alert_sent_at_utc"] = _now_iso(current)
    _atomic_json(path, state)
    return True


def reserve_request(*, now: float | None = None, key: str | None = None,
                    budget_path: Path | None = None, block_path: Path | None = None,
                    limit: int | None = None) -> int:
    """Persist one attempted HTTP request before network I/O; retries count too."""
    global REQUESTS_THIS_RUN
    current = time.time() if now is None else float(now)
    secret = os.environ.get("RBLXVALUE_API_KEY", "") if key is None else key
    path = budget_path or BUDGET_FILE
    active = _active_block(current, secret, block_path or BLOCK_FILE)
    if active:
        raise RblxValueKeyBlocked(
            f"[rblxvalue-key-blocked] API key circuit breaker active until {active.get('blocked_until_utc')}; no request sent"
        )
    state = _read_json(path)
    cutoff = current - 86400
    raw_timestamps = state.get("request_timestamps_epoch", [])
    if not isinstance(raw_timestamps, list):
        raise RblxValueGuardError(
            "[rblxvalue-state] request budget timestamps are not a list; refusing API request"
        )
    timestamps = []
    for value in raw_timestamps:
        try:
            stamp = float(value)
        except (TypeError, ValueError) as exc:
            raise RblxValueGuardError(
                "[rblxvalue-state] request budget contains an invalid timestamp; refusing API request"
            ) from exc
        if not (stamp > 0 and stamp < float("inf")):
            raise RblxValueGuardError(
                "[rblxvalue-state] request budget contains a non-finite timestamp; refusing API request"
            )
        # Keep future timestamps too: a clock-skewed ledger must consume budget,
        # not silently grant extra requests until wall-clock time catches up.
        if stamp > cutoff:
            timestamps.append(stamp)
    cap = DAILY_REQUEST_LIMIT if limit is None else max(1, int(limit))
    if len(timestamps) >= cap:
        _atomic_json(path, {
            "schema_version": 1,
            "window": "rolling_24_hours",
            "limit": cap,
            "request_timestamps_epoch": timestamps,
            "updated_at_utc": _now_iso(current),
        })
        raise RblxValueBudgetExceeded(
            f"[rblxvalue-budget-exhausted] rolling 24-hour request budget reached ({len(timestamps)}/{cap}); refusing API call"
        )
    timestamps.append(current)
    _atomic_json(path, {
        "schema_version": 1,
        "window": "rolling_24_hours",
        "limit": cap,
        "request_timestamps_epoch": timestamps,
        "updated_at_utc": _now_iso(current),
    })
    REQUESTS_THIS_RUN += 1
    return len(timestamps)


def requests_this_run() -> int:
    return REQUESTS_THIS_RUN


def reset_run_counter() -> None:
    global REQUESTS_THIS_RUN
    REQUESTS_THIS_RUN = 0


def log_request_count() -> None:
    print(f"[rblxvalue-budget] requests_this_run={REQUESTS_THIS_RUN}; daily_limit={DAILY_REQUEST_LIMIT}")


def is_key_error(status: int, detail: str) -> bool:
    text = str(detail or "").casefold()
    # A generic "account suspended/inactive" message is not proof that the
    # API credential itself is invalid. Require explicit credential context.
    key_context = any(marker in text for marker in (
        "api key", "api-key", "apikey", "access key", "credential", "token",
    ))
    invalid_marker = any(marker in text for marker in (
        "invalid", "inactive", "suspended", "deactivated", "revoked", "expired",
        "not activated", "not active", "unactivated", "activation required", "disabled",
    ))
    return int(status) == 401 or (key_context and invalid_marker)


def raise_for_key_error(status: int, detail: str, *, key: str | None = None, now: float | None = None) -> None:
    if is_key_error(status, detail):
        reason = f"HTTP {status}" if int(status) in (401, 403) else "invalid/inactive/suspended credential"
        mark_key_blocked(reason, key=key, now=now)
        raise RblxValueKeyBlocked(
            f"[rblxvalue-key-blocked] RBLXValue credential rejected ({reason}); circuit breaker set for 6 hours"
        )


def jittered_delay(delay: float) -> float:
    base = max(0.0, float(delay))
    return base + random.uniform(0.0, min(1.0, max(0.05, base * 0.1)))
