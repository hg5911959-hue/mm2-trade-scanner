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


def _finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


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

    budget = documents.get("rblxvalue_request_budget.json")
    if (root / "rblxvalue_request_budget.json").exists() and isinstance(budget, dict):
        timestamps = budget.get("request_timestamps_epoch")
        if not isinstance(timestamps, list):
            errors.append("RBLXValue request budget timestamps must be a list")
        elif any(not _finite_positive(value) for value in timestamps):
            errors.append("RBLXValue request budget contains an invalid timestamp")
        try:
            limit = int(budget.get("limit"))
            if limit < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("RBLXValue request budget limit is invalid")

    health = documents.get("rblxvalue_key_health.json")
    if (root / "rblxvalue_key_health.json").exists() and isinstance(health, dict):
        fingerprint = str(health.get("key_fingerprint") or "")
        if len(fingerprint) != 16 or any(char not in "0123456789abcdef" for char in fingerprint.casefold()):
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
        except sqlite3.DatabaseError:
            errors.append("market history database is unreadable or corrupt")
    elif require_cache:
        errors.append("successful scan is missing market_history.sqlite3")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="state-artifact/data")
    parser.add_argument("--require-cache", action="store_true")
    args = parser.parse_args()
    errors = validate_state(args.directory, require_cache=args.require_cache)
    if errors:
        for error in errors:
            print(f"[state-validation] ERROR: {error}")
        return 1
    print("[state-validation] PASS: downloaded state is structurally safe to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
