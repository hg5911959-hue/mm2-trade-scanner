"""Read-only lookup for the completed ROBASE All marketplaces daily close.

This module deliberately does not change production valuation. It provides a
strict, independently testable selector for the previous Algeria calendar day.
"""
from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FIELDS = (
    "robase_slug",
    "item_id",
    "item_name",
    "date",
    "all_marketplaces_sold",
    "all_marketplaces_price_after_fee",
)
DEFAULT_TIMEZONE = "Africa/Algiers"
DEFAULT_HISTORY = Path("robase-daily-history/data/weapons_daily_all_marketplaces_after_fees.csv")
DEFAULT_IDENTITY_MAP = Path("robase-daily-history/data/weapon_identity_map.json")
MIN_DAILY_SALES_FOR_ALERTS = 3


def previous_completed_day(now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    """Return yesterday in the requested local timezone; reject naive timestamps."""
    zone = ZoneInfo(timezone_name)
    current = now if now is not None else datetime.now(zone)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return (current.astimezone(zone).date() - timedelta(days=1)).isoformat()


def expected_weapon_slugs(identity_map_path: str | Path = DEFAULT_IDENTITY_MAP, expected_count: int = 214) -> set[str]:
    """Load exact ROBASE slugs from the persisted exact-identity crosswalk."""
    import json

    try:
        mapping = json.loads(Path(identity_map_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("exact weapon identity map is missing or invalid") from exc
    if not isinstance(mapping, dict):
        raise ValueError("exact weapon identity map must be an object")
    if len(mapping) != expected_count:
        raise ValueError(f"exact weapon identity map must contain exactly {expected_count} BloxSwaps identities")
    slugs = set()
    for bloxswaps_slug, value in mapping.items():
        if not isinstance(bloxswaps_slug, str) or not bloxswaps_slug.strip() or not isinstance(value, dict):
            raise ValueError("exact weapon identity map contains an invalid BloxSwaps identity")
        slug = value.get("robase_slug")
        if not isinstance(slug, str) or not slug.strip() or slug in slugs:
            raise ValueError("exact weapon identity map must contain unique, non-empty ROBASE slugs")
        slugs.add(slug)
    if len(slugs) != expected_count:
        raise ValueError(f"exact weapon identity map must contain {expected_count} unique ROBASE slugs")
    return slugs


def load_daily_prices(
    history_path: str | Path,
    target_date: str,
    weapon_slugs: set[str],
) -> dict[str, dict]:
    """Load exactly one complete weapon/date matrix; blank prices remain unavailable."""
    try:
        parsed_target = date.fromisoformat(target_date)
        if parsed_target.isoformat() != target_date:
            raise ValueError("non-canonical date")
    except (TypeError, ValueError) as exc:
        raise ValueError("target_date must be an ISO calendar date (YYYY-MM-DD)") from exc
    if not isinstance(weapon_slugs, set) or not weapon_slugs or any(not isinstance(x, str) or not x for x in weapon_slugs):
        raise ValueError("weapon_slugs must be a non-empty set of exact ROBASE slugs")

    result: dict[str, dict] = {}
    try:
        handle = Path(history_path).open(encoding="utf-8", newline="")
    except OSError as exc:
        raise ValueError("persistent ROBASE daily history is unavailable") from exc

    with handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("ROBASE daily history CSV schema mismatch")
        for row in reader:
            if None in row or any(row.get(field) is None for field in FIELDS):
                raise ValueError("ROBASE daily history contains a malformed CSV row")
            if row.get("date") != target_date:
                continue
            slug = row.get("robase_slug", "")
            if slug not in weapon_slugs:
                raise ValueError(f"unexpected weapon slug in daily history: {slug}")
            if slug in result:
                raise ValueError(f"duplicate weapon/date observation: {slug}/{target_date}")
            try:
                sold = int(row["all_marketplaces_sold"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid daily sales count for {slug}") from exc
            if sold < 0:
                raise ValueError(f"negative daily sales count for {slug}")
            raw_price = row["all_marketplaces_price_after_fee"]
            if raw_price == "":
                price = None
            else:
                try:
                    price = float(raw_price)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid daily after-fee price for {slug}") from exc
                if not math.isfinite(price) or price <= 0 or sold == 0:
                    raise ValueError(f"invalid priced-sales observation for {slug}")
            result[slug] = {
                "robase_slug": slug,
                "date": target_date,
                "all_marketplaces_sold": sold,
                "cash_price_usd": price,
                "price_display": f"${price:.2f}" if price is not None else "",
                "status": (
                    "unavailable_no_priced_sales" if price is None
                    else "low_sample_daily_close" if sold < MIN_DAILY_SALES_FOR_ALERTS
                    else "verified_daily_close"
                ),
                "eligible_for_cash_alerts": price is not None and sold >= MIN_DAILY_SALES_FOR_ALERTS,
                "source": "ROBASE All marketplaces price after fee",
            }

    if set(result) != weapon_slugs:
        missing = sorted(weapon_slugs - set(result))
        raise ValueError(f"incomplete daily weapon coverage for {target_date}: {len(missing)} missing")
    return result


def load_previous_day_prices(
    history_path: str | Path,
    weapon_slugs: set[str],
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[str, dict[str, dict]]:
    """Resolve the previous completed local day and require its full weapon matrix."""
    target = previous_completed_day(now=now, timezone_name=timezone_name)
    return target, load_daily_prices(history_path, target, weapon_slugs)


def load_exact_previous_day_prices(
    history_path: str | Path = DEFAULT_HISTORY,
    identity_map_path: str | Path = DEFAULT_IDENTITY_MAP,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    expected_count: int = 214,
) -> tuple[str, dict[str, dict]]:
    """Resolve yesterday using only the persisted exact weapon identity map."""
    slugs = expected_weapon_slugs(identity_map_path, expected_count=expected_count)
    return load_previous_day_prices(
        history_path,
        slugs,
        now=now,
        timezone_name=timezone_name,
    )
