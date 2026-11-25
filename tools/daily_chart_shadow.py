"""Build an isolated, fail-closed cash catalogue from yesterday's ROBASE chart.

This module is shadow-only: it never mutates the live catalogue or scanner state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import math


def build_daily_chart_shadow_catalog(
    live_catalog: dict,
    daily_prices: dict[str, dict],
    *,
    now: datetime | None = None,
    expected_count: int = 214,
) -> tuple[dict, dict]:
    """Clone the live catalogue and expose only eligible daily-chart weapon prices.

    All entries are disabled first. Only exact ROBASE slugs present in the
    previous-day matrix with >=3 sales receive a verified one-day price.
    No current/rolling price can leak through for unavailable entries.
    """
    if not isinstance(live_catalog, dict) or not isinstance(live_catalog.get("items_by_slug"), dict):
        raise ValueError("live ROBASE catalogue is invalid")
    if not isinstance(daily_prices, dict) or len(daily_prices) != expected_count:
        raise ValueError(f"daily chart matrix must contain exactly {expected_count} weapons")
    items_by_slug = live_catalog["items_by_slug"]
    if any(slug not in items_by_slug for slug in daily_prices):
        raise ValueError("daily chart contains a weapon absent from the live exact-slug catalogue")
    dates = {row.get("date") for row in daily_prices.values() if isinstance(row, dict)}
    if len(dates) != 1 or None in dates:
        raise ValueError("daily chart rows must share one completed date")

    shadow = deepcopy(live_catalog)
    shadow_rows = shadow["items_by_slug"]
    # Disable every catalogue row, including non-weapons and manual/name-only
    # entries, so no current/rolling value can silently become a fallback.
    seen = set()
    for entry in list(shadow_rows.values()) + list(shadow.get("items", {}).values()):
        if id(entry) in seen:
            continue
        seen.add(id(entry))
        entry["cash_price_usd"] = None
        entry["price_low_usd"] = None
        entry["price_high_usd"] = None
        entry["price_display"] = ""
        entry["status"] = "unverified"
        entry["verified_at_utc"] = ""
        entry["price_sample_count"] = 0
        entry["price_window_days"] = 1
        entry["notes"] = "Disabled in daily-chart shadow mode; no previous-day eligible observation"
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() != UTC.utcoffset(current):
        raise ValueError("shadow verification time must be timezone-aware UTC")
    stamp = current.astimezone(UTC).isoformat()
    eligible = unavailable = 0
    for slug, observation in daily_prices.items():
        if not isinstance(observation, dict) or observation.get("robase_slug") != slug:
            raise ValueError(f"daily chart slug identity mismatch: {slug}")
        entry = shadow_rows[slug]
        sold = observation.get("all_marketplaces_sold")
        price = observation.get("cash_price_usd")
        if type(sold) is not int or sold < 0:
            raise ValueError(f"invalid daily sales count for {slug}")
        if price is None or not observation.get("eligible_for_cash_alerts") or sold < 3:
            unavailable += 1
            entry["price_sample_count"] = sold
            entry["notes"] = f"Excluded from daily shadow: {observation.get('status', 'unavailable')}"
            continue
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
            raise ValueError(f"invalid daily after-fee price for {slug}")
        entry.update({
            "cash_price_usd": float(price),
            "price_low_usd": float(price),
            "price_high_usd": float(price),
            "price_display": f"${float(price):.2f}",
            "status": "verified",
            "verified_at_utc": stamp,
            "price_sample_count": sold,
            "price_window_days": 1,
            "notes": f"ROBASE All marketplaces price after fee; daily close {next(iter(dates))}; {sold} sales",
        })
        eligible += 1
    return shadow, {
        "date": next(iter(dates)),
        "weapons_total": expected_count,
        "eligible": eligible,
        "unavailable_or_low_sample": unavailable,
        "non_daily_catalogue_entries_disabled": len(shadow_rows) - expected_count,
    }
