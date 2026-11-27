"""Optional, offline Supreme/RBLXValue reference data for Telegram display only."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_WEAPONS = 214
SOURCE = "RBLXValue official API v2"
MAX_SNAPSHOT_AGE_SECONDS = 36 * 60 * 60


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _positive_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if number.is_integer() else number


def load_telegram_market_info(
    snapshot_path: str | Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_SNAPSHOT_AGE_SECONDS,
    expected_count: int = EXPECTED_WEAPONS,
) -> tuple[dict[str, dict], dict]:
    """Load a fresh, exact-identity daily archive; never calls an API or raises scan gates."""
    path = Path(snapshot_path)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("optional RBLXValue/Supreme display snapshot is missing or invalid") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("unsupported optional RBLXValue display snapshot schema")
    if snapshot.get("source") != SOURCE:
        raise ValueError("optional display snapshot source is not the official RBLXValue API archive")
    fields = snapshot.get("source_fields") or {}
    if fields.get("value_source_b") != "Supreme Values (exclusive trade-price source)":
        raise ValueError("optional display snapshot does not identify Supreme as value_source_b")
    if fields.get("stability") != "RBLXValue":
        raise ValueError("optional display snapshot does not identify the Stability field source")
    if snapshot.get("expected_weapons") != expected_count:
        raise ValueError(f"optional display snapshot must cover exactly {expected_count} weapons")
    rows = snapshot.get("items")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f"optional display snapshot must contain exactly {expected_count} rows")
    fetched_raw = str(snapshot.get("fetched_at_utc") or "").strip()
    if not fetched_raw:
        raise ValueError("optional display snapshot has no fetch timestamp")
    try:
        fetched = datetime.fromisoformat(fetched_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("optional display snapshot fetch timestamp is invalid") from exc
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ValueError("optional display snapshot fetch timestamp must be timezone-aware")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("current time must be timezone-aware")
    age = (current.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > max_age_seconds:
        raise ValueError("optional RBLXValue/Supreme display snapshot is stale or future-dated")

    mapping: dict[str, dict] = {}
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("optional display snapshot contains a malformed row")
        slug = _slug(row.get("bloxswaps_slug"))
        status = str(row.get("status") or "")
        if not slug or slug in seen:
            raise ValueError("optional display snapshot contains a missing or duplicate BloxSwaps slug")
        seen.add(slug)
        if status not in {"matched", "matched_missing_blended_value", "matched_missing_supreme_value"}:
            continue
        supreme = _positive_number(row.get("value_source_b"))
        stability = str(row.get("stability") or "").strip() or None
        if supreme is None and stability is None:
            continue
        mapping[slug] = {
            "name": str(row.get("robase_name") or row.get("rblxvalue_name") or "").strip(),
            "supreme_value": supreme,
            "stability": stability,
            "value_source": "Supreme Values via RBLXValue",
            "stability_source": "RBLXValue",
        }
    summary = {
        "snapshot_date": str(snapshot.get("snapshot_date") or ""),
        "fetched_at_utc": fetched.astimezone(timezone.utc).isoformat(),
        "age_hours": round(max(0.0, age) / 3600, 2),
        "mapped_items": len(mapping),
        "with_supreme_value": sum(row["supreme_value"] is not None for row in mapping.values()),
        "with_stability": sum(row["stability"] is not None for row in mapping.values()),
        "source": SOURCE,
        "value_source": "Supreme Values (value_source_b)",
        "stability_source": "RBLXValue",
    }
    return mapping, summary
