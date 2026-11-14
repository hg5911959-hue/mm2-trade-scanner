"""Validate scanner state artifacts before replacing the durable state branch."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

REQUIRED_MARKET_TABLES = {
    "market_snapshots",
    "market_values",
    "listings",
    "opportunities",
}
REQUIRED_MARKET_COLUMNS = {
    "market_snapshots": {
        "id", "captured_at_utc", "snapshot_hash", "item_count", "source",
    },
    "market_values": {
        "snapshot_id", "item_key", "item_name", "slug", "category", "item_type",
        "supreme_value", "secondary_value", "difference_percent", "confidence", "stability",
    },
    "listings": {
        "listing_id", "source", "source_id", "owner_name", "owner_url", "listing_url",
        "first_seen_at_utc", "last_seen_at_utc", "give_items_json", "receive_items_json", "content_hash",
    },
    "opportunities": {
        "opportunity_id", "listing_id", "source", "detected_at_utc", "give_total",
        "receive_total", "profit", "margin_percent", "listing_url", "details_json",
    },
}

VALID_PENDING_REASONS = {
    "legacy_unclassified", "unmatched_value", "stability_unverified",
    "stability_blocked", "quantity_uncertain", "unparsed_feed",
    "telegram_failure", "deferred_offer",
}


def _finite_positive(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def validate_state(directory: str | Path, *, require_cache: bool = False) -> list[str]:
    """Return validation errors; never mutate the supplied state directory."""
    root = Path(directory)
    if not root.exists():
        return ["state directory is missing"] if require_cache else []
    files = [path for path in root.rglob("*") if path.is_file()]
    if not files:
        if require_cache:
            return [
                "successful scan is missing supreme_values.json",
                "successful scan is missing market_history.sqlite3",
            ]
        return []

    errors: list[str] = []
    if any(path.name == "discord_messages.json" for path in files):
        errors.append("private Discord message file must not enter durable public state")
    temporary = [path for path in files if path.name.endswith(".tmp")]
    if temporary:
        errors.append(f"unfinished atomic-write temporary file found: {temporary[0].name}")

    documents: dict[str, Any] = {}
    for path in files:
        if path.suffix.lower() != ".json":
            continue
        try:
            documents[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"invalid JSON state file: {path.name}")

    cache_path = root / "supreme_values.json"
    cache = documents.get("supreme_values.json")
    if cache_path.exists():
        if not isinstance(cache, dict):
            errors.append("Supreme cache root must be a JSON object")
        else:
            if cache.get("schema_version") not in (3, 4):
                errors.append("Supreme cache schema version is unsupported")
            if not isinstance(cache.get("values"), dict) or not cache.get("values"):
                errors.append("Supreme cache has no normalized item values")
            if not isinstance(cache.get("pages"), dict) or not cache.get("pages"):
                errors.append("Supreme cache has no source pages")
            if not _finite_positive(cache.get("fetched_at")):
                errors.append("Supreme cache fetched_at timestamp is invalid")
    elif require_cache:
        errors.append("successful scan is missing supreme_values.json")

    metrics = documents.get("metrics.json")
    metrics_path = root / "metrics.json"
    if metrics_path.exists() and not isinstance(metrics, dict):
        errors.append("metrics root must be an object")
    elif metrics_path.exists():
        for day, row in metrics.items():
            try:
                from datetime import date
                date.fromisoformat(day)
            except (TypeError, ValueError):
                errors.append("metrics contains an invalid UTC date key")
                break
            if not isinstance(row, dict):
                errors.append("metrics daily row must be an object")
                break
            if any(type(row.get(field, 0)) is not int or row.get(field, 0) < 0
                   for field in ("scans", "scan_failures", "alerts_sent", "alerts_deferred",
                                 "rblxvalue_requests", "key_blocked_seconds", "parse_empty")):
                errors.append("metrics contains an invalid counter")
                break

    pending_schedule = documents.get("pending_recheck_schedule.json")
    pending_schedule_path = root / "pending_recheck_schedule.json"
    if pending_schedule_path.exists():
        if (
            not isinstance(pending_schedule, dict)
            or type(pending_schedule.get("version")) is not int
            or pending_schedule.get("version") != 1
        ):
            errors.append("pending recheck schedule root or version is invalid")
        elif not isinstance(pending_schedule.get("schedule"), dict):
            errors.append("pending recheck schedule entries must be an object")
        else:
            for entry in pending_schedule["schedule"].values():
                if not isinstance(entry, dict):
                    errors.append("pending recheck schedule entry is invalid")
                    break
                attempts = entry.get("attempts")
                next_check = entry.get("next_check_at")
                reason = entry.get("reason")
                if (
                    type(attempts) is not int or attempts < 0
                    or not _finite_positive(next_check)
                    or (reason is not None and (not isinstance(reason, str) or reason not in VALID_PENDING_REASONS))
                ):
                    errors.append("pending recheck schedule counter, timestamp, or reason is invalid")
                    break

    budget = documents.get("rblxvalue_request_budget.json")
    budget_path = root / "rblxvalue_request_budget.json"
    if budget_path.exists() and not isinstance(budget, dict):
        errors.append("RBLXValue request budget root must be an object")
    elif budget_path.exists():
        timestamps = budget.get("request_timestamps_epoch")
        if not isinstance(timestamps, list):
            errors.append("RBLXValue request budget timestamps must be a list")
        elif any(not _finite_positive(value) for value in timestamps):
            errors.append("RBLXValue request budget contains an invalid timestamp")
        limit = budget.get("limit")
        if type(limit) is not int or limit < 1:
            errors.append("RBLXValue request budget limit is invalid")

    health = documents.get("rblxvalue_key_health.json")
    health_path = root / "rblxvalue_key_health.json"
    if health_path.exists() and not isinstance(health, dict):
        errors.append("RBLXValue health root must be an object")
    elif health_path.exists():
        fingerprint = health.get("key_fingerprint")
        if (
            not isinstance(fingerprint, str) or len(fingerprint) != 16
            or any(char not in "0123456789abcdef" for char in fingerprint.casefold())
        ):
            errors.append("RBLXValue health key fingerprint is invalid")
        if not _finite_positive(health.get("last_attempt_at_epoch")):
            errors.append("RBLXValue health attempt timestamp is invalid")
        last_success = health.get("last_success_at_epoch")
        if last_success is not None and not _finite_positive(last_success):
            errors.append("RBLXValue health success timestamp is invalid")

    database_path = root / "market_history.sqlite3"
    if database_path.exists():
        try:
            with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    errors.append("market history SQLite integrity check failed")
                tables = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = REQUIRED_MARKET_TABLES - tables
                if missing:
                    errors.append("market history database is missing required tables")
                else:
                    for table, required_columns in REQUIRED_MARKET_COLUMNS.items():
                        actual_columns = {
                            row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
                        }
                        if not required_columns.issubset(actual_columns):
                            errors.append(f"market history table has an incomplete schema: {table}")
        except sqlite3.DatabaseError:
            errors.append("market history database is unreadable or corrupt")
    elif require_cache:
        errors.append("successful scan is missing market_history.sqlite3")

    return errors


def validate_api_safety_state(directory: str | Path) -> list[str]:
    """Validate only the small API safety ledger used after failed scans."""
    root = Path(directory)
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if path.is_file()]
    allowed = {"rblxvalue_request_budget.json", "api_key_blocked.json"}
    errors: list[str] = []
    unexpected = [path.name for path in files if path.name not in allowed]
    if unexpected:
        errors.append("API safety artifact contains unexpected files")
    if any(path.name.endswith(".tmp") for path in files):
        errors.append("API safety artifact contains an unfinished temporary file")

    budget_path = root / "rblxvalue_request_budget.json"
    if budget_path.exists():
        try:
            budget = json.loads(budget_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            budget = None
            errors.append("RBLXValue request budget JSON is invalid")
        if not isinstance(budget, dict):
            errors.append("RBLXValue request budget root must be an object")
        else:
            version = budget.get("schema_version", 1)
            if type(version) is not int or version != 1:
                errors.append("RBLXValue request budget schema is unsupported")
            if budget.get("window", "rolling_24_hours") != "rolling_24_hours":
                errors.append("RBLXValue request budget window is invalid")
            limit = budget.get("limit")
            if type(limit) is not int or not 1 <= limit <= 60:
                errors.append("RBLXValue request budget limit is invalid")
            timestamps = budget.get("request_timestamps_epoch")
            if (
                not isinstance(timestamps, list)
                or len(timestamps) > 60
                or any(not _finite_positive(value) for value in timestamps)
            ):
                errors.append("RBLXValue request budget timestamps are invalid")

    block_path = root / "api_key_blocked.json"
    if block_path.exists():
        try:
            block = json.loads(block_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            block = None
            errors.append("RBLXValue breaker JSON is invalid")
        if not isinstance(block, dict):
            errors.append("RBLXValue breaker root must be an object")
        else:
            fingerprint = block.get("key_fingerprint")
            if (
                not isinstance(fingerprint, str) or len(fingerprint) != 16
                or any(char not in "0123456789abcdef" for char in fingerprint)
            ):
                errors.append("RBLXValue breaker fingerprint is invalid")
            permanent = block.get("permanent")
            consecutive = block.get("consecutive_blocks")
            seconds = block.get("blocked_seconds")
            until = block.get("blocked_until_epoch")
            if (
                type(block.get("schema_version")) is not int
                or block.get("schema_version") != 2
                or not isinstance(permanent, bool)
                or type(consecutive) is not int or consecutive < 1
                or not _finite_positive(seconds)
                or seconds not in (21600, 43200, 86400, 172800)
                or not _finite_positive(until)
            ):
                errors.append("RBLXValue breaker timing or schema is invalid")
            else:
                expected_permanent = consecutive >= 3
                expected_seconds = (21600, 43200, 86400, 172800)[min(consecutive - 1, 3)]
                if permanent != expected_permanent or seconds != expected_seconds:
                    errors.append("RBLXValue breaker escalation is inconsistent")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="state-artifact/data")
    parser.add_argument("--require-cache", action="store_true")
    parser.add_argument("--api-safety-only", action="store_true")
    args = parser.parse_args()
    errors = (
        validate_api_safety_state(args.directory)
        if args.api_safety_only
        else validate_state(args.directory, require_cache=args.require_cache)
    )
    if errors:
        for error in errors:
            print(f"[state-validation] ERROR: {error}")
        return 1
    print("[state-validation] PASS: downloaded state is structurally safe to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
