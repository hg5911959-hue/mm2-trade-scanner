"""Conservative USD cash-price catalogue and valuation helpers.

The CSV is manually maintained. Historical/unverified rows are never used to
send live alerts. No Supreme value is converted into a cash price.
"""
from __future__ import annotations

import csv
import hashlib
import math
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

REQUIRED_COLUMNS = {
    "name", "cash_price_usd", "tier", "status", "verified_at_utc", "source", "notes"
}
DEFAULT_CASH_PRICE_FILE = Path("data/cash_prices.csv")


def parse_haircut_tiers(raw):
    """Parse contiguous USD bands; upper bounds are exclusive."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("CASH_SELL_HAIRCUT_TIERS must not be empty")
    tokens = raw.split(",")
    tiers = []
    expected_lower = 0.0
    for index, token in enumerate(tokens):
        try:
            band, raw_pct = token.strip().split(":", 1)
            lower_text, upper_text = band.split("-", 1)
            lower = float(lower_text)
            upper = float(upper_text) if upper_text.strip() else None
            pct = float(raw_pct)
        except (ValueError, TypeError) as exc:
            raise ValueError("CASH_SELL_HAIRCUT_TIERS has an invalid range:percent entry") from exc
        if (not math.isfinite(lower) or lower != expected_lower
                or (upper is not None and (not math.isfinite(upper) or upper <= lower))
                or not math.isfinite(pct) or not 0 <= pct <= 99):
            raise ValueError("CASH_SELL_HAIRCUT_TIERS contains a gap, overlap, invalid bound, or haircut")
        if upper is None and index != len(tokens) - 1:
            raise ValueError("CASH_SELL_HAIRCUT_TIERS open-ended band must be last")
        tiers.append((lower, upper, pct))
        if upper is not None:
            expected_lower = upper
    if not tiers or tiers[0][0] != 0 or tiers[-1][1] is not None:
        raise ValueError("CASH_SELL_HAIRCUT_TIERS must cover USD prices from 0 to infinity")
    return tuple(tiers)


def haircut_for_price(price, tiers):
    value = float(price)
    if not math.isfinite(value) or value < 0:
        raise ValueError("cash price for haircut tier must be finite and non-negative")
    for lower, upper, pct in tiers:
        if value >= lower and (upper is None or value < upper):
            return pct
    raise ValueError("cash price does not fall into a configured haircut tier")


def cash_price_is_fresh(entry, max_age_days=14, now=None):
    """Only recent, verified UTC rows can contribute to a live cash calculation."""
    if entry.get("status") != "verified":
        return False
    try:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() != timedelta(0):
            return False
        verified = datetime.fromisoformat(str(entry.get("verified_at_utc", "")))
        if verified.tzinfo is None or verified.utcoffset() != timedelta(0):
            return False
        age_seconds = (current - verified).total_seconds()
        return 0 <= age_seconds <= float(max_age_days) * 86400
    except (TypeError, ValueError):
        return False


def _normalize_robase_slug(value):
    slug = str(value or "").strip().casefold()
    return slug if re.fullmatch(r"[a-z0-9-]{1,100}", slug) else ""


def normalize_cash_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text)


def load_cash_price_catalog(path=None, max_age_days=14, now=None):
    path = Path(path) if path is not None else DEFAULT_CASH_PRICE_FILE
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except OSError as exc:
        raise RuntimeError(f"[cash] Cannot read cash-price catalogue: {path}") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("[cash] Cash-price catalogue must be UTF-8.") from exc

    digest = hashlib.sha256(raw).hexdigest()
    rows = {}
    try:
        reader = csv.DictReader(text.splitlines())
        if (
            not reader.fieldnames
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames))
        ):
            raise RuntimeError("[cash] Cash-price CSV header is incomplete or contains duplicate columns.")
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise RuntimeError(f"[cash] Extra CSV fields on line {line_number}.")
            if any(row.get(column) is None for column in REQUIRED_COLUMNS):
                raise RuntimeError(f"[cash] Missing CSV field on line {line_number}.")
            name = str(row.get("name") or "").strip()
            key = normalize_cash_name(name)
            if not name or not key:
                raise RuntimeError(f"[cash] Missing item name on CSV line {line_number}.")
            if key in rows:
                raise RuntimeError(f"[cash] Duplicate normalized item name on CSV line {line_number}: {name}")
            try:
                price = float(str(row.get("cash_price_usd") or "").strip())
            except (TypeError, ValueError):
                raise RuntimeError(f"[cash] Invalid USD price on CSV line {line_number}: {name}")
            if not math.isfinite(price) or price <= 0:
                raise RuntimeError(f"[cash] USD price must be finite and positive on line {line_number}: {name}")
            try:
                price_low = float(str(row.get("price_low_usd") or price).strip())
                price_high = float(str(row.get("price_high_usd") or price).strip())
            except (TypeError, ValueError):
                raise RuntimeError(f"[cash] Invalid USD price range on CSV line {line_number}: {name}")
            if (not math.isfinite(price_low) or not math.isfinite(price_high)
                    or price_low <= 0 or price_high <= 0
                    or price_low > price or price > price_high):
                raise RuntimeError(f"[cash] USD price range must satisfy low <= price <= high on line {line_number}: {name}")
            status = str(row.get("status") or "").strip().casefold()
            if status not in {"verified", "unverified", "disabled"}:
                raise RuntimeError(f"[cash] Invalid status on CSV line {line_number}: {name}")
            verified_at = str(row.get("verified_at_utc") or "").strip()
            if status == "verified":
                if not verified_at:
                    raise RuntimeError(f"[cash] Verified row requires verified_at_utc: {name}")
                try:
                    parsed_verified_at = datetime.fromisoformat(verified_at)
                except ValueError as exc:
                    raise RuntimeError(f"[cash] verified_at_utc must be ISO-8601: {name}") from exc
                if parsed_verified_at.tzinfo is None or parsed_verified_at.utcoffset() != timedelta(0):
                    raise RuntimeError(f"[cash] verified_at_utc must be UTC: {name}")
                if not str(row.get("source") or "").strip():
                    raise RuntimeError(f"[cash] Verified row requires a source: {name}")
            rows[key] = {
                "name": name,
                "cash_price_usd": price,
                "price_low_usd": price_low,
                "price_high_usd": price_high,
                "tier": str(row.get("tier") or "").strip(),
                "status": status,
                "verified_at_utc": verified_at,
                "source": str(row.get("source") or "").strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
    except csv.Error as exc:
        raise RuntimeError("[cash] Malformed cash-price CSV.") from exc
    now_utc = now or datetime.now(UTC)
    fresh_rows = [row for row in rows.values() if cash_price_is_fresh(row, max_age_days=max_age_days, now=now_utc)]
    expiring_rows = []
    for row in fresh_rows:
        verified = datetime.fromisoformat(row["verified_at_utc"])
        age_days = (now_utc - verified).total_seconds() / 86400
        if max(0, float(max_age_days) - 3) <= age_days <= float(max_age_days):
            expiring_rows.append(row)
    oldest_verified_at = min((row["verified_at_utc"] for row in fresh_rows), default=None)
    return {
        "items": rows, "sha256": digest, "row_count": len(rows),
        "verified_count": len(fresh_rows),
        "expiring_within_3_days": len(expiring_rows),
        "oldest_verified_at": oldest_verified_at,
    }


def calculate_cash_side(
    items, catalog, side, sell_haircut_pct=15.0, max_age_days=14, now=None,
    allow_unverified_estimates=False, estimate_uncertainty_pct=30.0,
    sell_haircut_tiers=None,
):
    """Return cash bounds; unverified legacy prices are estimate-only and opt-in.

    Estimate mode is for offline research only. It must never be used to qualify
    live trade alerts; all such outputs are explicitly labelled ESTIMATE.
    """
    if side not in {"give", "receive"}:
        raise ValueError("side must be give or receive")
    try:
        haircut = float(sell_haircut_pct)
    except (TypeError, ValueError):
        raise ValueError("sell_haircut_pct must be numeric")
    if not math.isfinite(haircut) or not 0 <= haircut < 100:
        raise ValueError("sell_haircut_pct must be between 0 and 100")
    try:
        uncertainty = float(estimate_uncertainty_pct)
    except (TypeError, ValueError) as exc:
        raise ValueError("estimate_uncertainty_pct must be numeric") from exc
    if not math.isfinite(uncertainty) or not 0 <= uncertainty < 100:
        raise ValueError("estimate_uncertainty_pct must be between 0 and 100")

    low_total = high_total = 0.0
    lines, missing = [], []
    items_by_name = catalog.get("items", {})
    items_by_slug = catalog.get("items_by_slug", {})
    for item in items:
        name = str(item.get("canonical_name") or item.get("name") or "").strip()
        key = normalize_cash_name(name)
        robase_slug = _normalize_robase_slug(item.get("robase_slug"))
        entry = items_by_slug.get(robase_slug) if robase_slug else items_by_name.get(key)
        if not entry:
            missing.append(f"{name} (cash price missing)")
            continue
        if entry.get("tier") == "robase" and entry.get("status") != "verified":
            sample_count = entry.get("price_sample_count")
            if sample_count is None or sample_count == 0:
                missing.append(f"{name} (cash price unavailable: no eligible chromas.gg sales in the last 7 days)")
                continue
        if entry.get("tier") == "robase":
            sample_count = entry.get("price_sample_count")
            sample_window = entry.get("price_window_days")
            if type(sample_count) is not int or sample_count < 3:
                sample_label = (
                    f"{sample_count}/{sample_window}d"
                    if type(sample_count) is int and sample_window in (7, 30)
                    else "unknown"
                )
                missing.append(
                    f"{name} (cash price low sample: {sample_label}; minimum 3 completed sales)"
                )
                continue
        estimate_only = False
        if entry.get("status") == "unverified" and allow_unverified_estimates:
            estimate_only = True
        elif entry.get("status") != "verified":
            missing.append(f"{name} (cash price unverified)")
            continue
        elif not cash_price_is_fresh(entry, max_age_days=max_age_days, now=now):
            missing.append(f"{name} (cash price expired)")
            continue
        try:
            qmin_raw = item.get("quantity_min", item.get("quantity", 1))
            qmax_raw = item.get("quantity_max", item.get("quantity", qmin_raw))
            qmin = strict_quantity(qmin_raw)
            qmax = strict_quantity(qmax_raw) if qmax_raw is not None else None
        except (TypeError, ValueError):
            missing.append(f"{name} (quantity invalid)")
            continue
        if qmax is None or qmax < qmin:
            missing.append(f"{name} (quantity uncertain/unbounded)")
            continue
        unit = float(entry["cash_price_usd"])
        price_low = float(entry.get("price_low_usd", unit))
        price_high = float(entry.get("price_high_usd", unit))
        uncertainty_factor = uncertainty / 100.0 if estimate_only else 0.0
        applied_haircut = (
            haircut_for_price(price_low, sell_haircut_tiers)
            if side == "receive" and sell_haircut_tiers is not None else haircut
        )
        if side == "receive":
            unit_low = price_low * (1.0 - uncertainty_factor) * (1.0 - applied_haircut / 100.0)
            unit_high = price_high * (1.0 + uncertainty_factor) * (1.0 - applied_haircut / 100.0)
        else:
            unit_low = price_low * (1.0 - uncertainty_factor)
            unit_high = price_high * (1.0 + uncertainty_factor)
        line_low = unit_low * qmin
        line_high = unit_high * qmax
        low_total += line_low
        high_total += line_high
        quantity_label = f"x{qmin}" if qmin == qmax else f"x? ({qmin}..{qmax})"
        estimate_label = " [ESTIMATE — UNVERIFIED LEGACY PRICE]" if estimate_only else ""
        display_name = str(item.get("display_name") or name)
        price_display = str(entry.get("price_display") or "")
        if (
            not estimate_only and qmin == qmax and unit_low == unit_high
            and price_display.startswith("$") and applied_haircut == 0
        ):
            unit_label = price_display
            total_label = "$" + f"{line_low:.2f}"
        else:
            unit_label = "$" + f"{unit_low:.2f}.." + "$" + f"{unit_high:.2f}"
            total_label = "$" + f"{line_low:.2f}.." + "$" + f"{line_high:.2f}"
        haircut_label = f" (haircut {applied_haircut:.0f}%)" if side == "receive" and applied_haircut > 0 else ""
        sample_count = entry.get("price_sample_count")
        sample_window = entry.get("price_window_days")
        sample_label = ""
        if (
            entry.get("tier") == "robase"
            and type(sample_count) is int
            and sample_count < 3
            and sample_window in (7, 30)
        ):
            sample_label = f" [LOW SAMPLE {sample_count}/{sample_window}d]"
        marketplace_label = (
            f" [{entry.get('selected_marketplace')}]"
            if entry.get("tier") == "robase" and entry.get("selected_marketplace")
            else ""
        )
        lines.append(
            f"• {display_name} {quantity_label}{marketplace_label} — {unit_label} each = {total_label}"
            f"{haircut_label}{sample_label}{estimate_label}"
        )
    return low_total, high_total, lines, missing


def strict_quantity(value):
    """Parse a positive whole-item quantity without silently truncating data."""
    if isinstance(value, bool):
        raise TypeError("quantity must be a whole number, not a boolean")
    if isinstance(value, int):
        quantity = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("quantity must be a finite whole number")
        quantity = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        quantity = int(value.strip())
    else:
        raise ValueError("quantity must be a whole number")
    if not 1 <= quantity <= 9999:
        raise ValueError("quantity must be between 1 and 9999")
    return quantity


def cash_profit_bounds(give_bounds, receive_bounds):
    """Return conservative profit bounds after validating both monetary ranges."""
    try:
        give_min, give_max = (float(value) for value in give_bounds)
        receive_min, receive_max = (float(value) for value in receive_bounds)
    except (TypeError, ValueError) as exc:
        raise ValueError("cash bounds must contain two numeric values") from exc
    values = (give_min, give_max, receive_min, receive_max)
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise ValueError("cash bounds must be finite and non-negative")
    if give_min > give_max or receive_min > receive_max:
        raise ValueError("cash lower bounds cannot exceed upper bounds")
    return receive_min - give_max, receive_max - give_min


def load_robase_price_catalog(path="data/robase_prices.json", max_age_days=1, now=None, max_age_seconds=20 * 60):
    """Load ROBASE rows by immutable source slug; display names are not unique."""
    import json
    from datetime import UTC, datetime

    snapshot_path = Path(path)
    try:
        raw = snapshot_path.read_bytes()
        snapshot = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("[cash] ROBASE price snapshot is missing or invalid.") from exc

    if snapshot.get("schema_version") != 6 or snapshot.get("price_basis") != "chromas_gg_7d_price":
        raise RuntimeError("[cash] ROBASE snapshot is not the chromas.gg 7-day price schema.")
    expected = snapshot.get("catalog_expected_count")
    rows = snapshot.get("items")
    if (
        type(expected) is not int or expected < 200
        or snapshot.get("catalog_complete") is not True
        or not isinstance(rows, list)
        or len(rows) != expected
    ):
        raise RuntimeError("[cash] ROBASE full catalogue is incomplete; refusing cash alerts.")

    current_dt = now if isinstance(now, datetime) else datetime.now(UTC)
    if current_dt.tzinfo is None or current_dt.utcoffset() != timedelta(0):
        raise RuntimeError("[cash] Current time must be timezone-aware UTC.")
    current = current_dt.timestamp()
    items_by_slug = {}
    name_groups = {}
    page_stale_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("[cash] Invalid ROBASE item row.")
        name = str(row.get("name") or "").strip()
        slug = str(row.get("robase_slug") or "").strip().casefold()
        key = normalize_cash_name(name)
        display = str(row.get("price_display") or "").strip()
        source = str(row.get("source") or "")
        if not slug or not re.fullmatch(r"[a-z0-9-]{1,100}", slug):
            raise RuntimeError(f"[cash] Invalid ROBASE source slug: {name or 'unknown'}")
        if source != f"https://robase.gg/item/{slug}":
            raise RuntimeError(f"[cash] ROBASE item URL/slug mismatch: {name or slug}")
        try:
            checked_dt = datetime.fromisoformat(
                str(row.get("last_checked_at_utc") or "")
            )
            if checked_dt.tzinfo is None or checked_dt.utcoffset() != timedelta(0):
                raise ValueError("last_checked_at_utc must be UTC-aware")
            checked_at = checked_dt.timestamp()
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError("[cash] Invalid ROBASE price or verification timestamp.")
        age = current - checked_at
        fresh = 0 <= age <= max_age_seconds
        page_verified = row.get("status") == "verified" and fresh
        if not page_verified:
            page_stale_rows.append(name or slug)
        marketplace_prices = row.get("marketplace_prices")
        if not isinstance(marketplace_prices, dict):
            raise TypeError(f"[cash] ROBASE marketplace breakdown is missing: {name or slug}")
        if set(marketplace_prices) - {"chromas.gg"}:
            raise RuntimeError(f"[cash] Non-Chroma marketplace data is forbidden: {name or slug}")
        chromas_row = marketplace_prices.get("chromas.gg")
        if chromas_row is not None:
            if not isinstance(chromas_row, dict) or chromas_row.get("marketplace") != "chromas.gg":
                raise RuntimeError(f"[cash] Invalid chromas.gg marketplace identity: {name or slug}")
            market_sales = chromas_row.get("sold_7d")
            if type(market_sales) is not int or market_sales < 0:
                raise RuntimeError(f"[cash] Invalid chromas.gg Sold (7d) data: {name or slug}")
            raw_market_price = chromas_row.get("price_usd")
            raw_market_display = str(chromas_row.get("price_display") or "").strip()
            if market_sales == 0:
                if raw_market_price is not None or raw_market_display:
                    raise RuntimeError(f"[cash] A zero-sale chromas.gg row cannot carry a usable 7d price: {name or slug}")
            else:
                try:
                    market_price = float(raw_market_price)
                    display_market_price = float(raw_market_display.removeprefix("$").replace(",", ""))
                except (TypeError, ValueError, OverflowError):
                    raise RuntimeError(f"[cash] Invalid chromas.gg 7d price data: {name or slug}")
                if (
                    not math.isfinite(market_price) or market_price <= 0
                    or not math.isfinite(display_market_price) or abs(display_market_price - market_price) > 0.0000001
                    or not raw_market_display.startswith("$")
                ):
                    raise RuntimeError(f"[cash] Invalid chromas.gg 7d price data: {name or slug}")
        selected_marketplace = row.get("selected_marketplace")
        selected_sales = row.get("selected_marketplace_sold_7d", 0)
        if selected_marketplace is None:
            if row.get("cash_price_usd") is not None or display:
                raise RuntimeError(f"[cash] Missing chromas.gg price has a fallback value: {name or slug}")
            if chromas_row is not None and chromas_row.get("sold_7d", 0) > 0:
                raise RuntimeError(f"[cash] chromas.gg sales exist but its price was not selected: {name or slug}")
            price = None
            display_price = None
        else:
            if selected_marketplace != "chromas.gg" or chromas_row is None:
                raise RuntimeError(f"[cash] Only chromas.gg may supply a live cash price: {name or slug}")
            selected_row = chromas_row
            try:
                price = float(row.get("cash_price_usd"))
                display_price = float(display.removeprefix("$").replace(",", ""))
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError(f"[cash] Invalid chromas.gg selected price: {name or slug}")
            if (
                selected_sales <= 0
                or not math.isfinite(price) or price <= 0
                or not math.isfinite(display_price) or abs(display_price - price) > 0.0000001
                or not display.startswith("$")
                or abs(price - float(selected_row["price_usd"])) > 0.0000001
                or selected_sales != selected_row["sold_7d"]
            ):
                raise RuntimeError(f"[cash] Selected price does not match chromas.gg Price (7d): {name or slug}")
        if (
            not name or len(name) > 100 or not key
            or not row.get("robase_category")
        ):
            raise RuntimeError(f"[cash] Invalid ROBASE item identity: {name or slug}")
        if slug in items_by_slug:
            raise RuntimeError(f"[cash] Duplicate ROBASE source slug: {slug}")
        price_window_days = row.get("price_window_days")
        if price_window_days is not None and (
            type(price_window_days) is not int or price_window_days != 7
        ):
            raise RuntimeError(f"[cash] Invalid ROBASE price sample window: {name or slug}")
        sold_7d = row.get("sold_7d", 0)
        sold_30d = row.get("sold_30d", 0)
        if (
            type(sold_7d) is not int or sold_7d < 0
            or type(sold_30d) is not int or sold_30d < 0
        ):
            raise RuntimeError(f"[cash] Invalid ROBASE sales sample counts: {name or slug}")
        price_sample_count = selected_sales if selected_marketplace else 0
        verified = page_verified and selected_marketplace is not None
        entry = {
            "name": name,
            "cash_price_usd": price,
            "price_low_usd": price,
            "price_high_usd": price,
            "price_display": display,
            "tier": "robase",
            "status": "verified" if verified else "unverified",
            "verified_at_utc": row.get("last_checked_at_utc") if verified else "",
            "last_checked_at_utc": row.get("last_checked_at_utc"),
            "price_updated_at_utc": row.get("price_updated_at_utc"),
            "robase_slug": slug,
            "robase_category": row.get("robase_category"),
            "og_image_url": row.get("og_image_url"),
            "og_image_slug": row.get("og_image_slug"),
            "bloxswaps_slug": row.get("bloxswaps_slug"),
            "bloxswaps_value": row.get("bloxswaps_value"),
            "bloxswaps_copies": row.get("bloxswaps_copies"),
            "bloxswaps_holders": row.get("bloxswaps_holders"),
            "sold_7d": sold_7d,
            "sold_30d": sold_30d,
            "price_window_days": 7,
            "price_sample_count": price_sample_count,
            "selected_marketplace": selected_marketplace,
            "marketplace_prices": marketplace_prices,
            "source": source,
            "notes": "ROBASE chromas.gg Price (7d)" if selected_marketplace else "No chromas.gg sales in the last 7 days",
        }
        items_by_slug[slug] = entry
        name_groups.setdefault(key, []).append(entry)

    if len(items_by_slug) != expected:
        raise RuntimeError(f"[cash] ROBASE catalogue count mismatch: {len(items_by_slug)}/{expected}.")

    # Name lookup remains only for unique labels; duplicate names resolve by slug.
    items = {key: group[0] for key, group in name_groups.items() if len(group) == 1}
    duplicate_name_keys = sorted(key for key, group in name_groups.items() if len(group) > 1)
    price_rows = [
        (
            slug,
            row["price_display"],
            row["status"],
            row.get("selected_marketplace"),
            row.get("price_sample_count"),
        )
        for slug, row in sorted(items_by_slug.items())
    ]
    price_digest = hashlib.sha256(
        json.dumps(price_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if page_stale_rows:
        raise RuntimeError(
            f"[cash] ROBASE item pages are not all freshly verified: {len(page_stale_rows)} stale/unverified rows."
        )
    verified_rows = [row for row in items_by_slug.values() if row["status"] == "verified"]
    cash_eligible_rows = [
        row for row in verified_rows
        if type(row.get("price_sample_count")) is int and row["price_sample_count"] >= 3
    ]
    return {
        "items": items,
        "items_by_slug": items_by_slug,
        "duplicate_name_keys": duplicate_name_keys,
        "sha256": price_digest,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "row_count": len(items_by_slug),
        "verified_count": len(verified_rows),
        "cash_eligible_count": len(cash_eligible_rows),
        "expected_count": expected,
        "catalog_complete": True,
        "expiring_within_3_days": 0,
        "oldest_verified_at": min(
            (row["verified_at_utc"] for row in verified_rows), default=None
        ),
    }
