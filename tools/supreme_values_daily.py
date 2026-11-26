#!/usr/bin/env python3
"""Direct, read-only Supreme Values MM2 snapshot collector.

Fetches the site's own category HTML and parses its embedded _svPopup JSON.
It never uses RBLXValue as a source or fallback. Network failures, challenges,
missing categories, ambiguous identities, and incomplete identity coverage
fail closed; the last successful archive is left untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
import unicodedata
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "Supreme Values direct website"
SOURCE_BASE = "https://supremevalues.com/mm2"
EXPECTED_WEAPONS = 214  # Current exact ROBASE crosswalk; expand only after reconciling the 8-row gap.
CATEGORIES = (
    "sets", "uniques", "ancients", "vintages", "chromas", "godlies",
    "legendaries", "rares", "uncommons", "commons", "pets", "misc",
)
CATEGORY_MAP = {
    "godly": "godlies", "ancient": "ancients", "vintage": "vintages",
    "classic": "vintages", "legendary": "legendaries", "rare": "rares",
    "uncommon": "uncommons", "common": "commons", "unique": "uniques",
    "chroma": "chromas",
}
STABILITY_SCORES = {
    "Rising": 9, "Overpaid For": 8, "Doing Well": 7, "Improving": 6,
    "Fluctuating": 5, "Receding": 4, "Stable": 3, "Underpaid For": 2,
    "Decreasing": 1, "Unsafe": 0,
}
CSV_FIELDS = (
    "snapshot_date", "fetched_at_utc", "source_last_updated",
    "robase_slug", "robase_name", "bloxswaps_slug", "item_type",
    "item_rarity", "is_chroma", "source_category", "source_name",
    "source_url", "value", "value_display", "value_min", "value_max",
    "stability", "stability_score", "demand", "rarity", "origin",
    "change_in_value", "change_percent", "aliases", "rise_chance",
    "status",
)


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def parse_number(value: object) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and value >= 0 else None
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if number.is_integer() else number


def parse_range(value: object) -> tuple[int | float | None, int | float | None]:
    if not isinstance(value, str) or value.strip().casefold() in {"", "[n/a]", "n/a", "na"}:
        return None, None
    match = re.fullmatch(r"\s*([\d,]+(?:\.\d+)?)\s*-\s*([\d,]+(?:\.\d+)?)\s*", value)
    if not match:
        return None, None
    low, high = parse_number(match.group(1)), parse_number(match.group(2))
    if low is None or high is None or low <= 0 or high < low:
        raise ValueError(f"Invalid Supreme range: {value!r}")
    return low, high


def extract_sv_popup(html: str) -> dict:
    """Parse the JSON-compatible _svPopup assignment without evaluating JS."""
    if not isinstance(html, str):
        raise TypeError("Supreme page HTML must be text")
    match = re.search(r"\b(?:var|let|const)\s+_svPopup\s*=\s*", html)
    if not match:
        raise ValueError("Supreme page has no _svPopup data object")
    start = match.end()
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        raise ValueError("Supreme _svPopup is not valid embedded JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("Supreme _svPopup is empty or not an object")
    for name, item in parsed.items():
        if not isinstance(name, str) or not name or not isinstance(item, dict):
            raise ValueError("Supreme _svPopup contains a malformed item")
    return parsed


def _source_timestamp(html: str) -> str | None:
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    match = re.search(
        r"Values\s+Last\s+Updated\s*[-:]*\s*([^<\r\n]{5,80}?)\s*(?://|Join our Discord|$)",
        text,
        re.IGNORECASE,
    )
    return " ".join(match.group(1).split()) if match else None


def fetch_category(category: str, *, timeout: int = 30, opener=urlopen) -> tuple[dict, str | None, str]:
    url = f"{SOURCE_BASE}/{category}"
    request = Request(
        url,
        headers={
            "User-Agent": "MM2-Trade-Scanner/1.0 (authorized read-only Supreme Values data collection)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"Supreme Values returned HTTP {status} for {category}")
            body = response.read(8_000_001)
    except HTTPError as exc:
        raise RuntimeError(f"Supreme Values returned HTTP {exc.code} for {category}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Supreme Values request failed for {category}: {type(exc).__name__}") from exc
    if len(body) > 8_000_000:
        raise ValueError(f"Supreme Values {category} page exceeded 8 MB")
    html = body.decode("utf-8", errors="strict")
    if re.search(r"cloudflare|captcha|verify you are human|access denied", html, re.IGNORECASE):
        raise RuntimeError(f"Supreme Values access challenge detected on {category}; no bypass attempted")
    return extract_sv_popup(html), _source_timestamp(html), url


def fetch_all_categories(*, sleep=time.sleep, opener=urlopen, delay: float = 0.75) -> tuple[dict, dict]:
    all_items: dict[tuple[str, str], dict] = {}
    timestamps: list[str] = []
    category_counts: dict[str, int] = {}
    for index, category in enumerate(CATEGORIES):
        raw_items, updated, url = fetch_category(category, opener=opener)
        category_counts[category] = len(raw_items)
        if updated:
            timestamps.append(updated)
        for name, raw in raw_items.items():
            key = (category, normalize_name(name))
            if key in all_items:
                raise ValueError(f"Duplicate item identity on Supreme category {category}: {name}")
            all_items[key] = {"name": name, "raw": raw, "category": category, "url": url}
        if index < len(CATEGORIES) - 1 and delay > 0:
            sleep(delay)
    if len(category_counts) != len(CATEGORIES):
        raise ValueError("Not all configured Supreme categories were fetched")
    timestamps_complete = len(timestamps) == len(CATEGORIES)
    timestamps_consistent = timestamps_complete and len(set(timestamps)) == 1
    metadata = {
        "category_count": len(category_counts),
        "category_counts": category_counts,
        "source_last_updated": timestamps[0] if timestamps_consistent else None,
        "source_timestamp_consistent": timestamps_consistent,
        "source_timestamp_count": len(timestamps),
        "source_items": len(all_items),
    }
    return all_items, metadata


def build_snapshot(identity_map: dict, source_items: dict, metadata: dict, *, snapshot_date: str, fetched_at_utc: str) -> dict:
    if not isinstance(identity_map, dict) or len(identity_map) != EXPECTED_WEAPONS:
        raise ValueError(f"Expected exactly {EXPECTED_WEAPONS} identities in current ROBASE crosswalk")
    if not isinstance(source_items, dict) or not source_items:
        raise ValueError("Direct Supreme source dataset is empty")
    rows = []
    seen_robase: set[str] = set()
    seen_blox: set[str] = set()
    claimed: dict[tuple[str, str], list[int]] = {}
    for blox_slug, identity in sorted(identity_map.items()):
        if not isinstance(identity, dict):
            raise ValueError(f"Malformed identity row: {blox_slug}")
        robase_slug = str(identity.get("robase_slug") or "").strip().casefold()
        robase_name = str(identity.get("robase_name") or "").strip()
        if not robase_slug or not robase_name or robase_slug in seen_robase or blox_slug in seen_blox:
            raise ValueError(f"Missing or duplicate ROBASE/BloxSwaps identity: {blox_slug}")
        seen_robase.add(robase_slug)
        seen_blox.add(blox_slug)
        rarity = str(identity.get("bloxswaps_rarity") or "").casefold()
        category = "chromas" if identity.get("bloxswaps_chroma") is True else CATEGORY_MAP.get(rarity)
        name_key = normalize_name(robase_name)
        candidates = [
            entry for (source_category, source_name), entry in source_items.items()
            if source_category == category and source_name == name_key
        ] if category else []
        status = "missing" if not candidates else ("ambiguous" if len(candidates) > 1 else "matched")
        row = {
            "snapshot_date": snapshot_date,
            "fetched_at_utc": fetched_at_utc,
            "source_last_updated": metadata.get("source_last_updated"),
            "robase_slug": robase_slug,
            "robase_name": robase_name,
            "bloxswaps_slug": str(blox_slug),
            "item_type": str(identity.get("bloxswaps_type") or "").casefold(),
            "item_rarity": rarity,
            "is_chroma": identity.get("bloxswaps_chroma") is True,
            "source_category": category,
            "source_name": None,
            "source_url": None,
            "value": None,
            "value_display": None,
            "value_min": None,
            "value_max": None,
            "stability": None,
            "stability_score": None,
            "demand": None,
            "rarity": None,
            "origin": None,
            "change_in_value": None,
            "change_percent": None,
            "aliases": None,
            "rise_chance": None,
            "status": status,
        }
        if len(candidates) == 1:
            item = candidates[0]
            raw = item["raw"]
            value = parse_number(raw.get("rawValue"))
            if value is None:
                value = parse_number(raw.get("value"))
            low, high = parse_range(raw.get("range"))
            stability = str(raw.get("stability") or "").strip() or None
            if stability and stability not in STABILITY_SCORES:
                raise ValueError(f"Unknown Supreme Stability label for {robase_name}: {stability}")
            row.update({
                "source_name": item["name"],
                "source_url": item["url"],
                "value": value,
                "value_display": str(raw.get("value") or "").strip() or None,
                "value_min": low,
                "value_max": high,
                "stability": stability,
                "stability_score": STABILITY_SCORES.get(stability),
                "demand": parse_number(raw.get("demand")),
                "rarity": parse_number(raw.get("rarity")),
                "origin": raw.get("origin"),
                "change_in_value": raw.get("diff"),
                "change_percent": raw.get("pctChange"),
                "aliases": raw.get("aliases"),
                "rise_chance": parse_number(raw.get("riseChance")),
            })
            claimed.setdefault((category, name_key), []).append(len(rows))
        rows.append(row)
    for key, indexes in claimed.items():
        if len(indexes) > 1:
            for index in indexes:
                rows[index]["status"] = "ambiguous_identity_collision"
                for field in ("value", "value_display", "value_min", "value_max", "stability", "stability_score", "demand", "rarity"):
                    rows[index][field] = None
    counts = {
        "expected_weapons": len(rows),
        "matched": sum(row["status"] == "matched" for row in rows),
        "missing": sum(row["status"] == "missing" for row in rows),
        "ambiguous": sum(row["status"] in {"ambiguous", "ambiguous_identity_collision"} for row in rows),
        "with_numeric_value": sum(row["value"] is not None for row in rows),
        "with_stability": sum(row["stability"] is not None for row in rows),
        "with_demand": sum(row["demand"] is not None for row in rows),
    }
    return {
        "schema_version": 1,
        "source": SOURCE,
        "source_url": "https://supremevalues.com/values.php",
        "source_base_url": SOURCE_BASE,
        "snapshot_date": snapshot_date,
        "fetched_at_utc": fetched_at_utc,
        "source_last_updated": metadata.get("source_last_updated"),
        "source_timestamp_consistent": metadata.get("source_timestamp_consistent"),
        "category_count": metadata.get("category_count"),
        "category_counts": metadata.get("category_counts"),
        "source_items_count": metadata.get("source_items"),
        "expected_weapons": EXPECTED_WEAPONS,
        "counts": counts,
        "third_party_authorization": {
            "source": "Supreme Values",
            "authorization_date": "2026-11-26",
            "authorized_scope": "User-provided written authorization statement for accessing, extracting, processing, and displaying Supreme Values item values, ranges, Stability, Demand, Rarity, and related fields with accurate source attribution",
            "attribution": "Supreme Values (supremevalues.com)",
            "verification_status": "authorization statement supplied by user; external authenticity not independently verified",
        },
        "stability_methodology": {
            "source_field": "Supreme Values _svPopup.stability",
            "score_mapping": STABILITY_SCORES,
            "note": "Stability is a time-varying source label. Each daily row preserves the source label and its published score; do not infer future state from a prior snapshot.",
        },
        "items": rows,
    }


def write_outputs(snapshot: dict, json_path: Path, csv_path: Path) -> None:
    if len(snapshot.get("items", [])) != EXPECTED_WEAPONS:
        raise ValueError("Refusing to write an incomplete Supreme snapshot")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp, csv_tmp = json_path.with_suffix(json_path.suffix + ".tmp"), csv_path.with_suffix(csv_path.suffix + ".tmp")
    json_tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(snapshot["items"])
    json_tmp.replace(json_path)
    csv_tmp.replace(csv_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-map", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--csv-output", required=True, type=Path)
    parser.add_argument("--min-matched", type=int, default=EXPECTED_WEAPONS)
    args = parser.parse_args()
    identity_map = json.loads(args.identity_map.read_text(encoding="utf-8"))
    source_items, metadata = fetch_all_categories()
    now = datetime.now(ZoneInfo("Africa/Algiers"))
    snapshot = build_snapshot(
        identity_map, source_items, metadata,
        snapshot_date=now.date().isoformat(),
        fetched_at_utc=datetime.now(ZoneInfo("UTC")).isoformat(),
    )
    if snapshot["counts"]["matched"] < args.min_matched or snapshot["counts"]["ambiguous"]:
        raise SystemExit(
            "Supreme direct identity validation failed: "
            + json.dumps(snapshot["counts"], sort_keys=True)
        )
    write_outputs(snapshot, args.json_output, args.csv_output)
    print(json.dumps({
        "source": snapshot["source"],
        "snapshot_date": snapshot["snapshot_date"],
        "source_last_updated": snapshot["source_last_updated"],
        "categories": snapshot["category_counts"],
        "counts": snapshot["counts"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
