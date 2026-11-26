"""Load a daily, exact-identity RBLXValue snapshot for value-profit shadow scans.

This loader is deliberately offline: it never reads an API key or calls RBLXValue.
Only value_source_b (Supreme Values) is used for trade-value calculations.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE = "RBLXValue official API v2"
EXPECTED_WEAPONS = 214
ALLOWED_TYPES = {"knife", "gun"}
ALLOWED_CATEGORIES = {"godly", "ancient", "vintage", "legendary", "rare", "uncommon", "common", "unique", "chroma"}
ALLOWED_STATUSES = {"matched", "matched_missing_blended_value", "matched_missing_supreme_value", "missing", "ambiguous", "ambiguous_identity_collision", "unsupported_category_or_type"}


def _name_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.casefold().replace("&", "and").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _range_bounds(_raw_range: object, value: float | None) -> tuple[float | None, float | None]:
    """Use only the scalar Supreme source value; ignore unverified provider ranges."""
    return (value, value) if value is not None else (None, None)


def load_values_from_snapshot(
    snapshot_path: str | Path,
    *,
    today: str | None = None,
    expected_count: int = EXPECTED_WEAPONS,
) -> tuple[dict, dict]:
    """Return the scanner's value catalogue and coverage summary from today's archive."""
    path = Path(snapshot_path)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("RBLXValue daily snapshot is missing or invalid; value shadow scan stopped") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("unsupported RBLXValue daily snapshot schema")
    if snapshot.get("source") != SOURCE:
        raise ValueError("RBLXValue snapshot source is not the official API archive")
    fields = snapshot.get("source_fields") or {}
    if fields.get("value_source_b") != "Supreme Values":
        raise ValueError("snapshot does not identify value_source_b as Supreme Values")
    authorization = snapshot.get("third_party_authorization")
    if (
        not isinstance(authorization, dict)
        or authorization.get("source") != "Supreme Values"
        or authorization.get("authorization_date") != "2026-11-26"
        or authorization.get("attribution") != "Supreme Values (supremevalues.com)"
        or not str(authorization.get("authorized_scope") or "").strip()
    ):
        raise ValueError("snapshot is missing the recorded Supreme Values authorization and attribution metadata")
    if snapshot.get("expected_weapons") != expected_count:
        raise ValueError(f"snapshot must cover exactly {expected_count} ROBASE weapons")
    rows = snapshot.get("items")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f"snapshot must contain exactly {expected_count} weapon rows")
    snapshot_date = str(snapshot.get("snapshot_date") or "")
    expected_date = today or datetime.now(ZoneInfo("Africa/Algiers")).date().isoformat()
    try:
        datetime.strptime(snapshot_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("snapshot date is invalid") from exc
    if snapshot_date != expected_date:
        raise ValueError(
            f"RBLXValue snapshot is not today's completed snapshot ({snapshot_date} != {expected_date})"
        )
    if not snapshot.get("dataset_last_updated"):
        raise ValueError("RBLXValue snapshot has no source update timestamp")

    seen_robase: set[str] = set()
    seen_blox: set[str] = set()
    seen_rblx: set[str] = set()
    grouped: dict[str, dict] = {}
    matched = supreme_numeric = stability_count = demand_count = alias_count = unmapped = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("snapshot contains a malformed weapon row")
        robase_slug = str(row.get("robase_slug") or "").strip().casefold()
        blox_slug = str(row.get("bloxswaps_slug") or "").strip().casefold()
        item_type = str(row.get("item_type") or "").strip().casefold()
        if row.get("snapshot_date") != snapshot_date:
            raise ValueError(f"row snapshot date mismatch for {robase_slug or 'unknown weapon'}")
        status = str(row.get("status") or "").strip()
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported row status in RBLXValue snapshot: {status}")
        if not robase_slug or robase_slug in seen_robase:
            raise ValueError("snapshot contains a missing or duplicate ROBASE slug")
        seen_robase.add(robase_slug)
        if not blox_slug or blox_slug in seen_blox:
            raise ValueError("snapshot contains a missing or duplicate BloxSwaps identity")
        seen_blox.add(blox_slug)
        if item_type not in ALLOWED_TYPES:
            raise ValueError(f"unsupported weapon type in snapshot: {item_type}")
        rblx_slug = str(row.get("rblxvalue_slug") or "").strip().casefold()
        rblx_name = str(row.get("rblxvalue_name") or "").strip()
        category = str(row.get("category") or "").strip().casefold()
        api_type = str(row.get("api_type") or "").strip().casefold()
        if not rblx_slug or not rblx_name:
            if status in {"matched", "matched_missing_blended_value", "matched_missing_supreme_value"}:
                raise ValueError(f"matched row is missing its RBLXValue identity: {robase_slug}")
            unmapped += 1
            continue
        if status in {"missing", "ambiguous", "ambiguous_identity_collision", "unsupported_category_or_type"}:
            raise ValueError(f"unmatched row unexpectedly contains an RBLXValue identity: {robase_slug}")
        if rblx_slug in seen_rblx:
            raise ValueError("snapshot assigns one RBLXValue identity to multiple weapons")
        seen_rblx.add(rblx_slug)
        if api_type != item_type or category not in ALLOWED_CATEGORIES:
            raise ValueError(f"RBLXValue type/category mismatch for {robase_slug}")
        expected_category = "chroma" if row.get("is_chroma") is True else str(row.get("item_rarity") or "").strip().casefold()
        if expected_category == "classic":
            expected_category = "vintage"
        if category != expected_category:
            raise ValueError(f"RBLXValue rarity/category mismatch for {robase_slug}")
        matched += 1
        if row.get("identity_method") == "explicit_verified_alias":
            alias_count += 1

        # Critical source rule: Supreme Values only. Never fall back to blended
        # value or MM2Values when value_source_b is absent or invalid.
        supreme = _positive_number(row.get("value_source_b"))
        low, high = _range_bounds(row.get("range"), supreme)
        stability = str(row.get("stability") or "").strip() or None
        demand = row.get("demand")
        rarity = row.get("rarity")
        if supreme is not None:
            supreme_numeric += 1
        if stability is not None:
            stability_count += 1
        if isinstance(row.get("demand"), int) and not isinstance(row.get("demand"), bool) and 0 <= row["demand"] <= 10:
            demand_count += 1

        variant = {
            "id": rblx_slug,
            # BloxSwaps slug is the exact feed identity; the RBLXValue slug is
            # retained separately for provenance and audit.
            "slug": blox_slug,
            "rblxvalue_slug": rblx_slug,
            "robase_slug": robase_slug,
            "name": rblx_name,
            "value": supreme,
            "value_source": "Supreme Values",
            "stability_source": "RBLXValue",
            "demand_source": "RBLXValue",
            "rarity_source": "RBLXValue",
            "value_min": low,
            "value_max": high,
            "raw_source_value": supreme,
            "rblxvalue_value": _positive_number(row.get("value")),
            "secondary_value": _positive_number(row.get("value_source_a")),
            "value_status": "numeric" if supreme is not None else "not_listed",
            "category": category,
            "type": api_type,
            "stability": stability,
            "demand": demand if isinstance(demand, int) and not isinstance(demand, bool) and 0 <= demand <= 10 else None,
            "rarity": rarity if isinstance(rarity, int) and not isinstance(rarity, bool) and 0 <= rarity <= 10 else None,
            "range": str(row.get("range") or ""),
            "last_updated": row.get("item_last_updated"),
        }
        key = _name_key(rblx_name)
        record = grouped.setdefault(key, {
            "name": rblx_name,
            "value": supreme,
            "value_min": low,
            "value_max": high,
            "secondary_value": variant["secondary_value"],
            "variants": [],
        })
        record["variants"].append(variant)
        if len(record["variants"]) > 1:
            record["value"] = record["value_min"] = record["value_max"] = None
            record["secondary_value"] = None

    if len(seen_robase) != expected_count or len(seen_blox) != expected_count:
        raise ValueError("snapshot identity coverage does not match the expected weapon set")
    summary = {
        "snapshot_date": snapshot_date,
        "source": SOURCE,
        "weapons": expected_count,
        "exactly_mapped": matched,
        "explicit_verified_aliases": alias_count,
        "unmapped": unmapped,
        "supreme_numeric": supreme_numeric,
        "stability_available": stability_count,
        "demand_available": demand_count,
        "value_basis": "value_source_b (Supreme Values) only; no blended or MM2Values fallback",
        "quality_fields_source": "RBLXValue only (Stability, Demand, Rarity)",
        "api_requests": 0,
    }
    return grouped, summary
