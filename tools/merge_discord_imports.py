"""Merge two manually supplied Discord JSON secrets without logging message text."""
from __future__ import annotations
import json, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

RETENTION_DAYS = 7
MAX_ROWS = 2000

def merge_imports(primary, secondary, path="data/discord_messages.json", now=None):
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    incoming = []
    valid_secrets = 0
    invalid_secrets = 0
    for raw in (primary, secondary):
        if not raw:
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
    target = Path(path)
    existing = []
    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            existing = payload.get("messages", []) if isinstance(payload, dict) else []
        except (OSError, ValueError):
            existing = []
    by_id = {}
    for row in existing + incoming:
        message_id = str(row.get("message_id") or "").strip()
        if not message_id:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("created_at_utc", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        by_id[message_id] = row
    rows = sorted(by_id.values(), key=lambda row: str(row.get("created_at_utc", "")), reverse=True)[:MAX_ROWS]
    newest = None
    if rows:
        newest = max(datetime.fromisoformat(str(row["created_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc) for row in rows)
    stale = newest is None or current - newest > timedelta(hours=48)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps({"messages": rows}, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)
    status = {"updated_at_utc": current.isoformat(), "rows": len(rows),
              "newest_message_at_utc": newest.isoformat() if newest else None,
              "stale": stale, "valid_secrets": valid_secrets, "invalid_secrets": invalid_secrets}
    status_path = target.parent / "discord_manual_status.json"
    status_tmp = status_path.with_name(status_path.name + ".tmp")
    status_tmp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    os.replace(status_tmp, status_path)
    print(f"[discord-manual] valid_secrets={valid_secrets} invalid_secrets={invalid_secrets} retained={len(rows)}")
    if stale:
        print("[discord-manual] import is stale")
    return status

if __name__ == "__main__":
    merge_imports(os.environ.get("DISCORD_MANUAL_MESSAGES_JSON", ""),
                  os.environ.get("DISCORD_MANUAL_MESSAGES_JSON_2", ""))
