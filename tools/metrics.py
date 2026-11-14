"""Atomic UTC daily metrics ledger with 14-day retention."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

FIELDS = ("scans", "scan_failures", "alerts_sent", "alerts_deferred",
          "rblxvalue_requests", "key_blocked_seconds", "parse_empty")

DEFAULT_METRICS_FILE = Path("data/metrics.json")


def update_metrics(path=None, *, increments=None, set_values=None,
                   cash_blockers=None, cash_blocker_groups=None, now=None):
    target = Path(path) if path is not None else DEFAULT_METRICS_FILE
    current = now or datetime.now(timezone.utc)
    day = current.astimezone(timezone.utc).date()
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("metrics ledger is unreadable; refusing silent reset") from exc
    if not isinstance(data, dict):
        raise RuntimeError("metrics ledger root must be an object")
    cutoff = day - timedelta(days=13)
    data = {key: value for key, value in data.items()
            if _valid_day(key) and cutoff <= datetime.strptime(key, "%Y-%m-%d").date() <= day}
    key = day.isoformat()
    row = data.setdefault(key, {field: 0 for field in FIELDS})
    if not isinstance(row, dict):
        raise RuntimeError("metrics daily row must be an object")
    for field in FIELDS:
        row.setdefault(field, 0)
    row.setdefault("cash_blockers", {})
    row.setdefault("cash_blocker_groups", {})
    if not isinstance(row["cash_blockers"], dict) or not isinstance(row["cash_blocker_groups"], dict):
        raise RuntimeError("metrics cash blocker counters must be objects")
    for field, amount in (increments or {}).items():
        if field not in FIELDS:
            raise ValueError("unknown metric")
        row[field] = max(0, int(row[field]) + int(amount))
    for field, value in (set_values or {}).items():
        if field not in FIELDS:
            raise ValueError("unknown metric")
        row[field] = max(0, int(value))
    for item, amount in (cash_blockers or {}).items():
        if not isinstance(item, str) or not item or not item.isalnum():
            raise ValueError("cash blocker item key is invalid")
        if type(amount) is not int or amount < 0:
            raise ValueError("cash blocker increment is invalid")
        existing = row["cash_blockers"].get(item, 0)
        if type(existing) is not int or existing < 0:
            raise RuntimeError("metrics contains an invalid existing cash blocker counter")
        row["cash_blockers"][item] = existing + amount
    for group, amount in (cash_blocker_groups or {}).items():
        parts = group.split("|") if isinstance(group, str) else []
        if not parts or any(not part or not part.isalnum() for part in parts) or parts != sorted(set(parts)):
            raise ValueError("cash blocker group is invalid")
        if type(amount) is not int or amount < 0:
            raise ValueError("cash blocker group increment is invalid")
        existing = row["cash_blocker_groups"].get(group, 0)
        if type(existing) is not int or existing < 0:
            raise RuntimeError("metrics contains an invalid existing cash blocker group")
        row["cash_blocker_groups"][group] = existing + amount
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return row

def _valid_day(value):
    try:
        return len(value) == 10 and datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except (TypeError, ValueError):
        return False
