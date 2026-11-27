"""Validate scanner state artifacts before replacing the durable state branch."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


MAX_STATE_JSON_BYTES = 20 * 1024 * 1024
MAX_API_SAFETY_FILE_BYTES = 256 * 1024


def _read_json_bounded(path: Path, *, max_bytes: int) -> Any:
    """Read a bounded regular JSON file without following symlinks or blocking on FIFOs."""
    if path.is_symlink():
        raise ValueError("state file is a symlink")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("state path is not a regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("state file exceeds the configured size limit")
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("state file exceeds the configured size limit")
    return json.loads(raw.decode("utf-8"))


def _validate_api_key_block_document(block: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["RBLXValue breaker root must be an object"]
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


def _validate_public_dashboard_history(path: Path) -> list[str]:
    """Validate the deliberately public, BloxSwaps-only dashboard database."""
    errors: list[str] = []
    max_bytes = 64 * 1024 * 1024
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            return ["public dashboard history is not a regular file or exceeds 64 MiB"]
    except OSError:
        return ["public dashboard history cannot be safely inspected"]
    required_columns = {
        "listings": {
            "listing_id", "source", "source_id", "owner_name", "owner_url",
            "listing_url", "first_seen_at_utc", "last_seen_at_utc",
            "give_items_json", "receive_items_json", "content_hash",
        },
        "opportunities": {
            "opportunity_id", "listing_id", "source", "detected_at_utc",
            "give_total", "receive_total", "profit", "margin_percent",
            "listing_url", "details_json",
        },
    }

    def safe_url(value: object, *, optional: bool = False) -> bool:
        if optional and value in (None, ""):
            return True
        if not isinstance(value, str) or not value:
            return False
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() in {"bloxswaps.com", "www.bloxswaps.com"}
            and port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )

    def safe_items(raw: object) -> bool:
        try:
            items = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            return False
        if not isinstance(items, list) or len(items) > 20:
            return False
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "quantity"}:
                return False
            if not isinstance(item.get("name"), str) or not item["name"] or len(item["name"]) > 60:
                return False
            quantity = item.get("quantity")
            if quantity is not None and (type(quantity) is not int or not 1 <= quantity <= 9999):
                return False
        return True

    def valid_timestamp(value: object) -> bool:
        if not isinstance(value, str) or not value or len(value) > 64:
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True

    def valid_optional_number(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(value)

    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return ["public dashboard SQLite integrity check failed"]
            version = db.execute("PRAGMA user_version").fetchone()
            if not version or version[0] != 1:
                errors.append("public dashboard history schema version is unsupported")
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if tables != {"listings", "opportunities"}:
                return errors + ["public dashboard history contains unexpected or missing tables"]
            for table, columns in required_columns.items():
                actual = {
                    row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if actual != columns:
                    errors.append(f"public dashboard table has an unexpected or incomplete schema: {table}")
            if errors:
                return errors
            listing_count = db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            opportunity_count = db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
            if type(listing_count) is not int or not 0 <= listing_count <= 2_500:
                errors.append("public dashboard listing count exceeds its safety bound")
            if type(opportunity_count) is not int or not 0 <= opportunity_count <= 1_000:
                errors.append("public dashboard opportunity count exceeds its safety bound")
            for row in db.execute(
                "SELECT listing_id, source, source_id, owner_name, owner_url, listing_url, "
                "first_seen_at_utc, last_seen_at_utc, give_items_json, receive_items_json, content_hash "
                "FROM listings"
            ):
                if (
                    not isinstance(row[0], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row[0])
                    or not isinstance(row[2], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", row[2])
                    or row[1] != "bloxswaps"
                    or (row[3] is not None and (not isinstance(row[3], str) or len(row[3]) > 80))
                    or not safe_url(row[4], optional=True) or not safe_url(row[5])
                    or not valid_timestamp(row[6]) or not valid_timestamp(row[7])
                    or not safe_items(row[8]) or not safe_items(row[9])
                    or not isinstance(row[10], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", row[10])
                ):
                    errors.append("public dashboard listing contains non-public or invalid data")
                    break
            for row in db.execute(
                "SELECT opportunity_id, listing_id, source, detected_at_utc, give_total, "
                "receive_total, profit, margin_percent, listing_url, details_json FROM opportunities"
            ):
                try:
                    details = json.loads(row[9])
                except (TypeError, ValueError):
                    details = None
                if (
                    not isinstance(row[0], str) or not re.fullmatch(r"[0-9a-f]{64}", row[0])
                    or not isinstance(row[1], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row[1])
                    or row[2] != "bloxswaps" or not valid_timestamp(row[3])
                    or not valid_optional_number(row[4]) or not valid_optional_number(row[5])
                    or not _finite_positive(row[6]) or not valid_optional_number(row[7])
                    or not safe_url(row[8]) or not isinstance(details, dict)
                    or set(details) != {"give_items", "receive_items", "manual_review", "valuation_mode"}
                    or details.get("valuation_mode") != "cash"
                    or not safe_items(json.dumps(details.get("give_items")))
                    or not safe_items(json.dumps(details.get("receive_items")))
                    or not isinstance(details.get("manual_review"), bool)
                ):
                    errors.append("public dashboard opportunity contains non-cash or invalid data")
                    break
    except (OSError, sqlite3.DatabaseError, ValueError):
        errors.append("public dashboard history database is unreadable or corrupt")
    return errors


def validate_state(directory: str | Path, *, require_cache: bool = False) -> list[str]:
    """Return validation errors; never mutate the supplied state directory."""
    root = Path(directory)
    if root.is_symlink():
        return ["state directory is a symlink"]
    if not root.exists():
        return ["state directory is missing"] if require_cache else []
    paths = list(root.rglob("*"))
    symlinks = [path for path in paths if path.is_symlink()]
    if symlinks:
        return ["state contains symlink paths"]
    files = [path for path in paths if path.is_file()]
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
            documents[path.name] = _read_json_bounded(path, max_bytes=MAX_STATE_JSON_BYTES)
        except (OSError, UnicodeDecodeError, ValueError):
            errors.append(f"invalid JSON state file (oversized or unsafe): {path.name}")

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
            blockers = row.get("cash_blockers", {})
            groups = row.get("cash_blocker_groups", {})
            if not isinstance(blockers, dict) or any(
                not isinstance(name, str) or not name.isalnum()
                or type(count) is not int or count < 0
                for name, count in blockers.items()
            ):
                errors.append("metrics contains invalid cash blocker counters")
                break
            if not isinstance(groups, dict) or any(
                not isinstance(group, str)
                or group.split("|") != sorted(set(group.split("|")))
                or any(not part.isalnum() for part in group.split("|"))
                or type(count) is not int or count < 0
                for group, count in groups.items()
            ):
                errors.append("metrics contains invalid cash blocker groups")
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

    uncertain_context = documents.get("telegram_uncertain_context.json")
    uncertain_context_path = root / "telegram_uncertain_context.json"
    if uncertain_context_path.exists():
        if (
            not isinstance(uncertain_context, dict)
            or type(uncertain_context.get("version")) is not int
            or uncertain_context.get("version") != 1
            or not isinstance(uncertain_context.get("details"), dict)
            or len(uncertain_context.get("details", {})) > 500
        ):
            errors.append("Telegram uncertain-delivery context root, version, or size is invalid")
        else:
            allowed_hosts = {"bloxswaps.com", "www.bloxswaps.com", "discord.com", "www.discord.com"}
            for trade_id, entry in uncertain_context["details"].items():
                if not isinstance(trade_id, str) or not trade_id or not isinstance(entry, dict):
                    errors.append("Telegram uncertain-delivery context entry is invalid")
                    break
                fields = ("source_ad_id", "listing_url", "owner_url", "source", "delivery_path", "attempted_at_utc")
                if any(not isinstance(entry.get(field, ""), str) for field in fields):
                    errors.append("Telegram uncertain-delivery context fields are invalid")
                    break
                if entry.get("delivery_path") not in {"individual", "digest", "recovered"}:
                    errors.append("Telegram uncertain-delivery context delivery path is invalid")
                    break
                unsafe_link = False
                for field in ("listing_url", "owner_url"):
                    value = entry.get(field, "")
                    if value:
                        try:
                            parsed = urlsplit(value)
                            port = parsed.port
                        except ValueError:
                            unsafe_link = True
                            break
                        if (
                            parsed.scheme != "https"
                            or (parsed.hostname or "").casefold() not in allowed_hosts
                            or port not in (None, 443)
                            or parsed.username is not None
                            or parsed.password is not None
                        ):
                            unsafe_link = True
                            break
                if unsafe_link:
                    errors.append("Telegram uncertain-delivery context contains an unsafe link")
                    break

    budget = documents.get("rblxvalue_request_budget.json")
    budget_path = root / "rblxvalue_request_budget.json"
    if budget_path.exists() and not isinstance(budget, dict):
        errors.append("RBLXValue request budget root must be an object")
    elif budget_path.exists():
        version = budget.get("schema_version", 1)
        if type(version) is not int or version != 1:
            errors.append("RBLXValue request budget schema is unsupported")
        if budget.get("window", "rolling_24_hours") != "rolling_24_hours":
            errors.append("RBLXValue request budget window is invalid")
        timestamps = budget.get("request_timestamps_epoch")
        if (
            not isinstance(timestamps, list)
            or len(timestamps) > 60
            or any(not _finite_positive(value) for value in timestamps)
        ):
            errors.append("RBLXValue request budget contains an invalid timestamp")
        limit = budget.get("limit")
        if type(limit) is not int or not 1 <= limit <= 60:
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

    breaker_path = root / "api_key_blocked.json"
    if breaker_path.exists():
        errors.extend(_validate_api_key_block_document(documents.get("api_key_blocked.json")))

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

    public_dashboard_path = root / "public_dashboard_history.sqlite3"
    if public_dashboard_path.exists():
        errors.extend(_validate_public_dashboard_history(public_dashboard_path))

    return errors


def validate_api_safety_state(directory: str | Path) -> list[str]:
    """Validate the small API safety ledger using bounded, no-follow reads."""
    root = Path(directory)
    if root.is_symlink():
        return ["API safety state directory is a symlink"]
    if not root.exists():
        return []
    paths = list(root.rglob("*"))
    symlinks = [path for path in paths if path.is_symlink()]
    files = [path for path in paths if not path.is_symlink() and path.is_file()]
    allowed = {"rblxvalue_request_budget.json", "api_key_blocked.json"}
    errors: list[str] = []
    if symlinks:
        errors.append("API safety artifact contains symlink paths")
    unexpected = [path.name for path in files if path.name not in allowed]
    if unexpected:
        errors.append("API safety artifact contains unexpected files")
    if any(path.name.endswith(".tmp") for path in files):
        errors.append("API safety artifact contains an unfinished temporary file")

    budget_path = root / "rblxvalue_request_budget.json"
    if budget_path.exists() or budget_path.is_symlink():
        try:
            budget = _read_json_bounded(budget_path, max_bytes=MAX_API_SAFETY_FILE_BYTES)
        except (OSError, UnicodeDecodeError, ValueError):
            budget = None
            errors.append("RBLXValue request budget JSON is invalid, oversized, or unsafe")
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
    if block_path.exists() or block_path.is_symlink():
        try:
            block = _read_json_bounded(block_path, max_bytes=MAX_API_SAFETY_FILE_BYTES)
        except (OSError, UnicodeDecodeError, ValueError):
            block = None
            errors.append("RBLXValue breaker JSON is invalid, oversized, or unsafe")
        errors.extend(_validate_api_key_block_document(block))
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
