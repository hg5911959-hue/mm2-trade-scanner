#!/usr/bin/env python3
"""Daily, source-specific Supreme Values snapshot for the ROBASE weapon crosswalk.

This collector reads only the public Supreme Values category pages. It does not
use RBLXValue blended estimates, rolling ROBASE cash prices, or inferred values.
Unmatched, ambiguous, tiered, and stability-missing records remain explicit.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE_BASE = "https://supremevalues.com/mm2"
SOURCE_CATEGORIES = (
    "sets", "uniques", "ancients", "vintages", "chromas", "godlies",
    "legendaries", "rares", "uncommons", "commons", "pets", "misc",
)
RARITY_CATEGORY = {
    "godly": "godlies",
    "ancient": "ancients",
    "vintage": "vintages",
    "legendary": "legendaries",
    "rare": "rares",
    "uncommon": "uncommons",
    "common": "commons",
    "unique": "uniques",
    "set": "sets",
    "misc": "misc",
}
STABILITY_SCORE = {
    "rising": 9,
    "overpaid for": 8,
    "doing well": 7,
    "improving": 6,
    "fluctuating": 5,
    "receding": 4,
    "stable": 3,
    "underpaid for": 2,
    "decreasing": 1,
    "unsafe": 0,
}
CSV_FIELDS = (
    "snapshot_date", "source_updated", "fetched_at_utc", "robase_slug",
    "robase_name", "bloxswaps_slug", "item_type", "item_rarity", "is_chroma",
    "supreme_name", "supreme_category", "value", "value_type", "value_display",
    "range_min", "range_max", "range_display", "stability", "stability_score",
    "demand", "rarity", "change_in_value", "change_percent", "status",
)


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def extract_sv_popup(page_html: str) -> dict:
    """Extract JSON-compatible var _svPopup={...}; without greedy brace regex."""
    if not isinstance(page_html, str):
        raise TypeError("Supreme page HTML must be text")
    match = re.search(r"(?:\b(?:var|let|const)\s+)?_svPopup\s*=\s*", page_html)
    if not match:
        raise ValueError("Supreme page did not expose the _svPopup data object")
    start = match.end()
    while start < len(page_html) and page_html[start].isspace():
        start += 1
    if start >= len(page_html) or page_html[start] != "{":
        raise ValueError("_svPopup assignment is not an object literal")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(page_html)):
        char = page_html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload = json.loads(page_html[start:index + 1])
                if not isinstance(payload, dict) or not payload:
                    raise ValueError("_svPopup object is empty or invalid")
                return payload
    raise ValueError("_svPopup object is incomplete")


def extract_source_updated(page_html: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]*>", " ", page_html))
    plain = re.sub(r"\s+", " ", plain)
    match = re.search(
        r"Values\s+Last\s+Updated\s*[-:]\s*(.{3,100}?)(?:\s*//|\s+Join\s+our\s+Discord|\s*$)",
        plain,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Supreme page is missing its visible 'Values Last Updated' timestamp")
    return match.group(1).strip()


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            return None
        parsed = float(text)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def parse_value_fields(item: dict) -> dict:
    display = str(item.get("value") or "").strip()
    raw_value = item.get("rawValue")
    numeric = _numeric(raw_value)
    if numeric is None:
        numeric = _numeric(display)
    if numeric is not None:
        value_type = "numeric"
    elif display and re.search(r"\bx\s*\d+\b|\bt\d+\b", display, re.IGNORECASE):
        value_type = "tiered_items"
    else:
        value_type = "unknown"

    range_display = str(item.get("range") or "").strip()
    if not range_display or range_display.casefold() in {"n/a", "[n/a]"}:
        range_min = range_max = None
    else:
        match = re.search(
            r"([\d,]+(?:\.\d+)?)\s*(?:-|–|to)\s*([\d,]+(?:\.\d+)?)",
            range_display,
            flags=re.IGNORECASE,
        )
        if match:
            range_min = _numeric(match.group(1))
            range_max = _numeric(match.group(2))
            if range_min is None or range_max is None or range_min > range_max:
                range_min = range_max = None
        else:
            range_min = range_max = None

    stability = " ".join(str(item.get("stability") or "").split()).strip() or None
    stability_score = STABILITY_SCORE.get(stability.casefold()) if stability else None
    demand = _small_int(item.get("demand"), 0, 10)
    rarity = _small_int(item.get("rarity"), 0, 10)
    return {
        "value": numeric,
        "value_type": value_type,
        "value_display": display,
        "range_min": range_min,
        "range_max": range_max,
        "range_display": range_display,
        "stability": stability,
        "stability_score": stability_score,
        "demand": demand,
        "rarity": rarity,
        "change_in_value": str(item.get("diff") or "").strip() or None,
        "change_percent": str(item.get("pctChange") or "").strip() or None,
        "aliases": _aliases(item.get("aliases")),
        "image_key": str(item.get("imageKey") or "").strip() or None,
        "source_class": str(item.get("class") or "").strip() or None,
    }


def _small_int(value: object, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _aliases(value: object) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = re.split(r"[,;|]", value)
    else:
        values = []
    return [str(entry).strip() for entry in values if str(entry).strip()]


def fetch_page(category: str, *, timeout: int = 30, retries: int = 2) -> tuple[dict, str]:
    url = f"{SOURCE_BASE}/{quote(category, safe='')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://supremevalues.com/",
    }
    last_error = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"Supreme category {category} returned HTTP {response.status}")
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise ValueError(f"Supreme category {category} exceeded the 8 MB response cap")
                page_html = body.decode("utf-8", "replace")
            items = extract_sv_popup(page_html)
            updated = extract_source_updated(page_html)
            time.sleep(0.6)
            return items, updated
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not retrieve Supreme category {category}: {type(last_error).__name__}: {last_error}")


def _candidate_matches(identity: dict, category_items: dict) -> list[tuple[str, dict, dict]]:
    target = normalize_name(identity.get("robase_name"))
    if not target:
        return []
    matches = []
    for source_name, raw in category_items.items():
        if not isinstance(raw, dict):
            continue
        fields = parse_value_fields(raw)
        names = [source_name, *fields["aliases"]]
        if target in {normalize_name(name) for name in names}:
            matches.append((str(source_name), raw, fields))
    return matches


def build_weapon_snapshot(
    identity_map: dict,
    pages: dict[str, dict],
    source_updated: str,
    *,
    fetched_at_utc: str,
    snapshot_date: str,
    expected_count: int = 214,
) -> dict:
    if not isinstance(identity_map, dict) or len(identity_map) != expected_count:
        raise ValueError(f"Expected exactly {expected_count} weapon identities in ROBASE crosswalk")
    if set(pages) != set(SOURCE_CATEGORIES):
        raise ValueError("Supreme category coverage is incomplete")
    if not source_updated or not fetched_at_utc or not snapshot_date:
        raise ValueError("Snapshot timestamps are required")

    rows = []
    source_claims: dict[tuple[str, str], list[int]] = {}
    for blox_slug, identity in sorted(identity_map.items()):
        if not isinstance(identity, dict):
            raise ValueError(f"Invalid identity record for {blox_slug}")
        rarity = str(identity.get("bloxswaps_rarity") or "").strip().casefold()
        is_chroma = identity.get("bloxswaps_chroma") is True
        expected_category = "chromas" if is_chroma else RARITY_CATEGORY.get(rarity)
        if not expected_category or expected_category not in pages:
            candidates = []
        else:
            candidates = _candidate_matches(identity, pages[expected_category])

        row = {
            "snapshot_date": snapshot_date,
            "source_updated": source_updated,
            "fetched_at_utc": fetched_at_utc,
            "robase_slug": str(identity.get("robase_slug") or ""),
            "robase_name": str(identity.get("robase_name") or ""),
            "bloxswaps_slug": str(blox_slug),
            "item_type": str(identity.get("bloxswaps_type") or ""),
            "item_rarity": rarity,
            "is_chroma": is_chroma,
            "supreme_name": None,
            "supreme_category": expected_category,
            "value": None,
            "value_type": "unknown",
            "value_display": "",
            "range_min": None,
            "range_max": None,
            "range_display": "",
            "stability": None,
            "stability_score": None,
            "demand": None,
            "rarity": None,
            "change_in_value": None,
            "change_percent": None,
            "status": "missing" if expected_category else "unsupported_source_category",
            "source_image_key": None,
            "source_aliases": [],
        }
        if len(candidates) == 1:
            source_name, _raw, fields = candidates[0]
            row.update({
                "supreme_name": source_name,
                "value": fields["value"],
                "value_type": fields["value_type"],
                "value_display": fields["value_display"],
                "range_min": fields["range_min"],
                "range_max": fields["range_max"],
                "range_display": fields["range_display"],
                "stability": fields["stability"],
                "stability_score": fields["stability_score"],
                "demand": fields["demand"],
                "rarity": fields["rarity"],
                "change_in_value": fields["change_in_value"],
                "change_percent": fields["change_percent"],
                "source_image_key": fields["image_key"],
                "source_aliases": fields["aliases"],
                "status": (
                    "matched_numeric" if fields["value_type"] == "numeric"
                    else "matched_tiered" if fields["value_type"] == "tiered_items"
                    else "matched_unknown_value"
                ),
            })
            source_claims.setdefault((expected_category, normalize_name(source_name)), []).append(len(rows))
        elif len(candidates) > 1:
            row["status"] = "ambiguous"
        rows.append(row)

    # A single Supreme record must not be silently assigned to two ROBASE weapons.
    for indexes in source_claims.values():
        if len(indexes) > 1:
            for index in indexes:
                rows[index]["status"] = "ambiguous_identity_collision"
                rows[index]["value"] = None
                rows[index]["stability"] = None
                rows[index]["stability_score"] = None

    counts = {
        "weapons": len(rows),
        "matched_numeric": sum(row["status"] == "matched_numeric" for row in rows),
        "matched_tiered": sum(row["status"] == "matched_tiered" for row in rows),
        "matched_unknown_value": sum(row["status"] == "matched_unknown_value" for row in rows),
        "missing": sum(row["status"] == "missing" for row in rows),
        "ambiguous": sum(row["status"] in {"ambiguous", "ambiguous_identity_collision"} for row in rows),
        "unsupported_source_category": sum(row["status"] == "unsupported_source_category" for row in rows),
        "with_stability": sum(bool(row["stability"]) for row in rows),
        "numeric_with_stability": sum(
            row["status"] == "matched_numeric" and row["stability"] is not None for row in rows
        ),
    }
    return {
        "schema_version": 1,
        "source": "Supreme Values official public category pages",
        "source_base_url": SOURCE_BASE,
        "source_updated": source_updated,
        "fetched_at_utc": fetched_at_utc,
        "snapshot_date": snapshot_date,
        "expected_weapons": expected_count,
        "category_count": len(pages),
        "counts": counts,
        "policy": {
            "identity": "exact normalized ROBASE display name or Supreme-published alias within the expected rarity/chroma category",
            "ambiguous_identity": "rejected; no fuzzy fallback",
            "non_numeric_value": "preserved as display text; excluded from numeric Value-profit calculation",
            "missing_stability": "preserved as null; excluded from any stability-dependent rule",
            "source_values": "Supreme source only; not RBLXValue blended estimates",
        },
        "items": rows,
    }


def collect_snapshot(identity_map_path: Path, *, now: datetime | None = None) -> dict:
    identity_map = json.loads(identity_map_path.read_text(encoding="utf-8"))
    if not isinstance(identity_map, dict):
        raise ValueError("ROBASE identity map must be a JSON object")
    pages = {}
    updated_labels = set()
    for category in SOURCE_CATEGORIES:
        items, updated = fetch_page(category)
        pages[category] = items
        updated_labels.add(updated)
        print(f"[supreme-daily] category={category} records={len(items)} source_updated={updated}")
    if len(updated_labels) != 1:
        raise ValueError(f"Supreme pages disagree on source update timestamp: {sorted(updated_labels)}")
    local_now = now or datetime.now(ZoneInfo("Africa/Algiers"))
    if local_now.tzinfo is None or local_now.utcoffset() is None:
        raise ValueError("Snapshot time must be timezone-aware")
    snapshot_date = local_now.astimezone(ZoneInfo("Africa/Algiers")).date().isoformat()
    fetched_at_utc = local_now.astimezone(ZoneInfo("UTC")).isoformat()
    snapshot = build_weapon_snapshot(
        identity_map, pages, next(iter(updated_labels)),
        fetched_at_utc=fetched_at_utc, snapshot_date=snapshot_date,
        expected_count=214,
    )
    print("[supreme-daily] coverage=" + json.dumps(snapshot["counts"], sort_keys=True))
    return snapshot


def write_outputs(snapshot: dict, json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(snapshot["items"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-map", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = collect_snapshot(args.identity_map)
    write_outputs(snapshot, args.json_output, args.csv_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
