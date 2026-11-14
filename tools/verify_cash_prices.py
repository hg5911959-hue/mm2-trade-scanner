"""Safely verify cash-price rows from a pasted marketplace list (stdlib only).

Input price values are never echoed. Matching is exact-first and ambiguous names
are skipped; this tool never creates or deletes catalogue rows.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools.cash_market import normalize_cash_name

DEFAULT_CSV = Path("data/cash_prices.csv")
LINE_RE = re.compile(r"^\s*(?:(x\d+)\s+)?(.+?)\s+(\d+(?:\.\d+)?)\$(?:/(\d+))?\s*$", re.I)
WEAPON_SUFFIXES = ("knife", "gun", "sword")


def parse_line(line: str):
    match = LINE_RE.match(line)
    if not match:
        return None, "invalid format"
    quantity_prefix, raw_name, raw_price, unit_marker = match.groups()
    quantity = int(quantity_prefix[1:]) if quantity_prefix else 1
    if quantity < 1:
        return None, "invalid quantity"
    if quantity > 1 and not unit_marker:
        return None, "multi-quantity total has no explicit /N unit marker"
    if unit_marker and int(unit_marker) < 1:
        return None, "invalid /N marker"
    price = float(raw_price)
    if price <= 0:
        return None, "price must be positive"
    return {"name": raw_name.strip(), "price": price}, None


def match_catalog_name(raw_name, names):
    normalized = normalize_cash_name(raw_name)
    exact = [name for name in names if normalize_cash_name(name) == normalized]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, "ambiguous exact match"
    folded = raw_name.strip().casefold()
    for suffix in WEAPON_SUFFIXES:
        if folded.endswith(" " + suffix):
            candidate = normalize_cash_name(folded[:-(len(suffix) + 1)])
            matches = [name for name in names if normalize_cash_name(name) == candidate]
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                return None, "ambiguous weapon-suffix match"
    return None, "unknown item"


def atomic_write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def verify(input_path, csv_path=DEFAULT_CSV, source="manual_market_list", max_change_pct=60.0, force=False, dry_run=False):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        required = {"name", "cash_price_usd", "tier", "status", "verified_at_utc", "source", "notes"}
        if not required.issubset(fields):
            raise ValueError("cash-price catalogue header is incomplete")
        rows = list(reader)
    by_name = {row["name"]: row for row in rows}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated, skipped, large_changes = 0, [], []
    with input_path.open("r", encoding="utf-8-sig") as handle:
        for line_no, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            parsed, error = parse_line(raw)
            if error:
                skipped.append((line_no, "<unparsed line>", error))
                continue
            name, error = match_catalog_name(parsed["name"], list(by_name))
            if error:
                skipped.append((line_no, parsed["name"], error))
                continue
            row = by_name[name]
            try:
                old = float(row["cash_price_usd"])
            except (TypeError, ValueError):
                old = 0.0
            change = abs(parsed["price"] - old) / old * 100 if old > 0 else 0.0
            if old > 0 and change > max_change_pct and not force:
                large_changes.append((name, change))
                continue
            row["cash_price_usd"] = f'{parsed["price"]:.2f}'
            row["status"] = "verified"
            row["verified_at_utc"] = now
            row["source"] = source or "manual_market_list"
            updated += 1
    if large_changes:
        raise ValueError(f"{len(large_changes)} price changes exceed --max-change-pct; nothing was written (use --force after review)")
    if not dry_run and updated:
        atomic_write_csv(csv_path, fields, rows)
    return {"verified": updated, "skipped": skipped, "dry_run": dry_run}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source", default="manual_market_list")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-change-pct", type=float, default=60.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args(argv)
    if args.max_change_pct < 0:
        parser.error("--max-change-pct must be non-negative")
    try:
        result = verify(args.input, args.csv, args.source, args.max_change_pct, args.force, args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"[cash-verify] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"[cash-verify] verified={result['verified']} skipped={len(result['skipped'])} dry_run={result['dry_run']}")
    for line_no, name, reason in result["skipped"]:
        print(f"[cash-verify] skipped line {line_no}: {name!r} ({reason})")
    if result["dry_run"]:
        print("[cash-verify] dry run; no file changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
