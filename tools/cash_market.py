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
from datetime import datetime, timedelta, timezone
from pathlib import Path

REQUIRED_COLUMNS = {
    "name", "cash_price_usd", "tier", "status", "verified_at_utc", "source", "notes"
}


def cash_price_is_fresh(entry, max_age_days=14, now=None):
    """Only recent, verified UTC rows can contribute to a live cash calculation."""
    if entry.get("status") != "verified":
        return False
    try:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() != timedelta(0):
            return False
        verified = datetime.fromisoformat(str(entry.get("verified_at_utc", "")).replace("Z", "+00:00"))
        if verified.tzinfo is None or verified.utcoffset() != timedelta(0):
            return False
        age_seconds = (current - verified).total_seconds()
        return 0 <= age_seconds <= float(max_age_days) * 86400
    except (TypeError, ValueError):
        return False


def normalize_cash_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text)


def load_cash_price_catalog(path="data/cash_prices.csv", max_age_days=14):
    path = Path(path)
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
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            raise RuntimeError("[cash] Cash-price CSV header is incomplete.")
        for line_number, row in enumerate(reader, start=2):
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
                "tier": str(row.get("tier") or "").strip(),
                "status": status,
                "verified_at_utc": verified_at,
                "source": str(row.get("source") or "").strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
    except csv.Error as exc:
        raise RuntimeError("[cash] Malformed cash-price CSV.") from exc
    now_utc = datetime.now(timezone.utc)
    fresh_rows = [row for row in rows.values() if cash_price_is_fresh(row, max_age_days=max_age_days, now=now_utc)]
    expiring_rows = []
    for row in fresh_rows:
        verified = datetime.fromisoformat(row["verified_at_utc"].replace("Z", "+00:00"))
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


def calculate_cash_side(items, catalog, side, sell_haircut_pct=15.0, max_age_days=14, now=None):
    """Return conservative cash bounds; receive-side proceeds include haircut."""
    if side not in {"give", "receive"}:
        raise ValueError("side must be give or receive")
    try:
        haircut = float(sell_haircut_pct)
    except (TypeError, ValueError):
        raise ValueError("sell_haircut_pct must be numeric")
    if not math.isfinite(haircut) or not 0 <= haircut < 100:
        raise ValueError("sell_haircut_pct must be between 0 and 100")

    low_total = high_total = 0.0
    lines, missing = [], []
    items_by_name = catalog.get("items", {})
    for item in items:
        name = str(item.get("canonical_name") or item.get("name") or "").strip()
        key = normalize_cash_name(name)
        entry = items_by_name.get(key)
        if not entry:
            missing.append(f"{name} (cash price missing)")
            continue
        if entry.get("status") != "verified":
            missing.append(f"{name} (cash price unverified)")
            continue
        if not cash_price_is_fresh(entry, max_age_days=max_age_days, now=now):
            missing.append(f"{name} (cash price expired)")
            continue
        try:
            qmin = int(item.get("quantity_min", item.get("quantity", 1)))
            qmax_raw = item.get("quantity_max", item.get("quantity", qmin))
            qmax = int(qmax_raw) if qmax_raw is not None else None
        except (TypeError, ValueError):
            missing.append(f"{name} (quantity invalid)")
            continue
        if qmin < 1 or qmin > 100 or qmax is None or qmax < qmin or qmax > 100:
            missing.append(f"{name} (quantity uncertain/unbounded)")
            continue
        unit = float(entry["cash_price_usd"])
        if side == "receive":
            unit_low = unit * (1.0 - haircut / 100.0)
            unit_high = unit_low
            basis = "net resale"
        else:
            unit_low = unit_high = unit
            basis = "replacement cost"
        line_low = unit_low * qmin
        line_high = unit_high * qmax
        low_total += line_low
        high_total += line_high
        quantity_label = f"x{qmin}" if qmin == qmax else f"x? ({qmin}..{qmax})"
        lines.append(
            f"• {name} {quantity_label} — ${unit_low:.2f} each "
            f"({basis}) = ${line_low:.2f}"
            + (f".. ${line_high:.2f}" if line_high != line_low else "")
        )
    return low_total, high_total, lines, missing


def cash_profit_bounds(give_bounds, receive_bounds):
    give_min, give_max = give_bounds
    receive_min, receive_max = receive_bounds
    return receive_min - give_max, receive_max - give_min
