"""Merge manual Discord imports without forging collector provenance."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

RETENTION_DAYS = 7
MAX_ROWS = 2_000
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_SECRET_BYTES = 1 * 1024 * 1024


def _snowflake(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate.isdigit() and 15 <= len(candidate) <= 22 else ""


def _collector_envelope(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("schema_version") == 2
        and payload.get("source") == "discord"
        and isinstance(payload.get("messages"), list)
        and isinstance(payload.get("configured_guild_ids"), list)
        and isinstance(payload.get("configured_channel_ids"), list)
        and isinstance(payload.get("scanned_channel_ids"), list)
    )


def _manual_record(row: dict[str, Any]) -> dict[str, Any]:
    """Remove identity provenance from owner-supplied JSON before merging."""
    result = dict(row)
    result["source"] = "manual_import"
    result["author_id"] = ""
    result["author_id_source"] = ""
    result["manual_imported"] = True
    return result


def _read_existing(target: Path) -> dict[str, Any]:
    if target.is_symlink():
        raise RuntimeError("Existing Discord state is a symlink; refusing to overwrite it")
    if not target.exists():
        return {}
    if not target.is_file() or target.stat().st_size > MAX_INPUT_BYTES:
        raise RuntimeError("Existing Discord state is not a bounded regular file")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Existing Discord state is unreadable; refusing to reset history") from None
    return payload if isinstance(payload, dict) else {}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def merge_imports(primary, secondary, path="data/discord_messages.json", now=None):
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    target = Path(path)
    existing_payload = _read_existing(target)
    collector_valid = _collector_envelope(existing_payload)
    existing_rows = existing_payload.get("messages", []) if isinstance(existing_payload.get("messages"), list) else []
    incoming: list[dict[str, Any]] = []
    valid_secrets = 0
    invalid_secrets = 0
    for raw in (primary, secondary):
        if not raw:
            continue
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SECRET_BYTES:
            invalid_secrets += 1
            continue
        try:
            payload = json.loads(raw)
            rows = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("schema")
            incoming.extend(row for row in rows if isinstance(row, dict))
            valid_secrets += 1
        except (ValueError, TypeError):
            invalid_secrets += 1

    by_id: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        if not isinstance(row, dict):
            continue
        message_id = _snowflake(row.get("message_id"))
        if not message_id:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("created_at_utc", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            if stamp < cutoff:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        if collector_valid and row.get("source") == "discord":
            by_id[message_id] = row
        else:
            by_id.setdefault(message_id, _manual_record(row))

    # Owner-supplied JSON is never promoted to a Discord API record. A genuine
    # collector record wins if both sources contain the same message ID.
    for row in incoming:
        message_id = _snowflake(row.get("message_id"))
        if not message_id or message_id in by_id:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("created_at_utc", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp < cutoff:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        by_id[message_id] = _manual_record(row)

    rows = sorted(
        by_id.values(),
        key=lambda row: str(row.get("created_at_utc", "")),
        reverse=True,
    )[:MAX_ROWS]
    newest = None
    if rows:
        stamps = []
        for row in rows:
            try:
                stamp = datetime.fromisoformat(str(row["created_at_utc"]).replace("Z", "+00:00"))
                if stamp.tzinfo is not None:
                    stamps.append(stamp.astimezone(timezone.utc))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        newest = max(stamps) if stamps else None
    stale = newest is None or current - newest > timedelta(hours=48)

    if collector_valid:
        output = {
            "schema_version": 2,
            "source": "discord",
            "updated_at_utc": current.isoformat(),
            "configured_guild_ids": existing_payload["configured_guild_ids"],
            "configured_channel_ids": existing_payload["configured_channel_ids"],
            "scanned_channel_ids": existing_payload["scanned_channel_ids"],
            "message_count": len(rows),
            "messages": rows,
        }
    else:
        # Manual-only data is explicitly labelled and cannot satisfy the
        # collector schema or become an actionable author-verified offer.
        output = {
            "schema_version": 1,
            "source": "manual_import",
            "updated_at_utc": current.isoformat(),
            "message_count": len(rows),
            "messages": rows,
        }
    _atomic_write(target, output)

    status = {
        "updated_at_utc": current.isoformat(),
        "rows": len(rows),
        "newest_message_at_utc": newest.isoformat() if newest else None,
        "stale": stale,
        "valid_secrets": valid_secrets,
        "invalid_secrets": invalid_secrets,
        "collector_envelope_preserved": collector_valid,
        "manual_rows": sum(row.get("source") == "manual_import" for row in rows),
    }
    status_path = target.parent / "discord_manual_status.json"
    _atomic_write(status_path, status)
    print(
        f"[discord-manual] valid_secrets={valid_secrets} invalid_secrets={invalid_secrets} "
        f"retained={len(rows)} collector_envelope_preserved={collector_valid}"
    )
    if stale:
        print("[discord-manual] import is stale")
    return status


if __name__ == "__main__":
    try:
        merge_imports(
            os.environ.get("DISCORD_MANUAL_MESSAGES_JSON", ""),
            os.environ.get("DISCORD_MANUAL_MESSAGES_JSON_2", ""),
        )
    except RuntimeError as exc:
        print(f"[discord-manual] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
