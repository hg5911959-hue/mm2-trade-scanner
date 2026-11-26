"""Archive one trustworthy ROBASE rolling-sales snapshot per UTC day.

This history is observational: each point is the current chromas.gg Price (7d)
value only. No 30-day price or other-market fallback is archived.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOT = Path("data/robase_prices.json")
DEFAULT_HISTORY = Path("data/robase_price_history.json")
SOURCE = "https://robase.gg/"
PRICE_BASIS = "chromas_gg_7d_price"
HISTORY_SCHEMA_VERSION = 2
DEFAULT_RETENTION_DAYS = 400


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _build_daily_entry(snapshot: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("ROBASE snapshot root must be an object")
    if snapshot.get("schema_version") != 6:
        raise ValueError("unsupported ROBASE snapshot schema")
    if snapshot.get("catalog_complete") is not True:
        raise ValueError("ROBASE snapshot catalogue is incomplete")
    if snapshot.get("price_basis") != PRICE_BASIS:
        raise ValueError("ROBASE snapshot price basis is not chromas.gg Price (7d)")
    expected = snapshot.get("catalog_expected_count")
    items = snapshot.get("items")
    if type(expected) is not int or expected < 200 or not isinstance(items, list) or len(items) != expected:
        raise ValueError("ROBASE snapshot does not contain its complete item catalogue")

    fetched_at = _parse_utc(snapshot.get("fetched_at_utc"))
    if fetched_at.date() != now.date():
        raise ValueError("ROBASE snapshot was not fetched on the current UTC date")
    if fetched_at > now.replace(microsecond=0):
        raise ValueError("ROBASE snapshot timestamp is in the future")

    archived_items = []
    seen_slugs = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("ROBASE item row must be an object")
        slug = item.get("robase_slug")
        name = item.get("name")
        price = item.get("cash_price_usd")
        window = item.get("price_window_days")
        selected_marketplace = item.get("selected_marketplace")
        chromas_sales = _nonnegative_int(item.get("selected_marketplace_sold_7d", 0), "selected_marketplace_sold_7d")
        if not isinstance(slug, str) or not slug or slug in seen_slugs:
            raise ValueError("ROBASE item slug is missing or duplicated")
        seen_slugs.add(slug)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("ROBASE item name is invalid")
        if item.get("status") != "verified":
            raise ValueError(f"ROBASE item page is not verified: {slug}")
        if selected_marketplace is None:
            if chromas_sales != 0 or price is not None or window is not None:
                raise ValueError(f"ROBASE missing Chroma 7d price has inconsistent sales data: {slug}")
            price_value = None
            sample_quality = "no_chromas_7d_sales"
        else:
            if selected_marketplace != "chromas.gg" or chromas_sales <= 0 or type(window) is not int or window != 7:
                raise ValueError(f"ROBASE price is not exclusively chromas.gg 7d: {slug}")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise ValueError(f"ROBASE chromas.gg Price (7d) is invalid for {slug}")
            price_value = round(float(price), 6)
            sample_quality = "low_sample_7d" if chromas_sales < 3 else "7d_sample"
        archived_items.append({
            "robase_slug": slug,
            "name": name.strip(),
            "price_usd": price_value,
            "price_window_days": 7 if price_value is not None else None,
            "sold_7d": chromas_sales,
            "sample_quality": sample_quality,
        })

    archived_items.sort(key=lambda row: row["robase_slug"])
    if len(archived_items) != expected:
        raise ValueError("ROBASE daily archive item count mismatch")
    return {
        "date_utc": now.date().isoformat(),
        "captured_at_utc": fetched_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "item_count": expected,
        "items": archived_items,
    }


def _load_history(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "source": SOURCE,
            "price_basis": PRICE_BASIS,
            "series_definition": "One daily observation of chromas.gg Price (7d) only; no other marketplace or 30-day fallback.",
            "days": [],
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("existing ROBASE history file is unreadable") from exc

    if (
        isinstance(document, dict)
        and document.get("schema_version") == 1
        and document.get("source") == SOURCE
        and document.get("price_basis") == "seller_proceeds_after_marketplace_fees"
        and isinstance(document.get("days"), list)
    ):
        # Never mix legacy blended/30d-fallback prices into the Chroma-only series.
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "source": SOURCE,
            "price_basis": PRICE_BASIS,
            "series_definition": "One daily observation of chromas.gg Price (7d) only; no other marketplace or 30-day fallback.",
            "legacy_days_discarded": len(document["days"]),
            "days": [],
        }
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != HISTORY_SCHEMA_VERSION
        or document.get("source") != SOURCE
        or document.get("price_basis") != PRICE_BASIS
        or not isinstance(document.get("days"), list)
    ):
        raise ValueError("existing ROBASE history schema or source is invalid")
    seen_dates = set()
    for entry in document["days"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise ValueError("existing ROBASE history day entry is invalid")
        try:
            day = date.fromisoformat(entry.get("date_utc", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("existing ROBASE history contains an invalid date") from exc
        if day.isoformat() in seen_dates:
            raise ValueError("existing ROBASE history contains duplicate dates")
        seen_dates.add(day.isoformat())
    return document


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_daily_snapshot(
    snapshot: dict[str, Any],
    history_path: str | Path = DEFAULT_HISTORY,
    *,
    now: datetime | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, Any]:
    """Record a complete, same-day snapshot once; repeated runs are idempotent."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    if type(retention_days) is not int or retention_days < 1:
        raise ValueError("retention_days must be a positive integer")
    daily = _build_daily_entry(snapshot, current)
    path = Path(history_path)
    history = _load_history(path)
    day_key = daily["date_utc"]
    if any(entry.get("date_utc") == day_key for entry in history["days"]):
        return {
            "status": "already_recorded",
            "date_utc": day_key,
            "item_count": daily["item_count"],
            "history_days": len(history["days"]),
        }

    history["days"].append(daily)
    history["days"].sort(key=lambda entry: entry["date_utc"])
    cutoff = current.date().toordinal() - retention_days + 1
    history["days"] = [
        entry for entry in history["days"]
        if date.fromisoformat(entry["date_utc"]).toordinal() >= cutoff
    ]
    _atomic_write(path, history)
    return {
        "status": "recorded",
        "date_utc": day_key,
        "item_count": daily["item_count"],
        "history_days": len(history["days"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_HISTORY)
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        result = record_daily_snapshot(snapshot, args.output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[robase-history] skipped: {exc}")
        return 1
    print(
        "[robase-history] status={status} date={date_utc} items={item_count} days={history_days}".format(
            **result
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
