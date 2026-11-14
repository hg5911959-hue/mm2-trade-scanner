"""Rank cash-catalogue items that block the most listings (stdlib only)."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tools.cash_market import load_cash_price_catalog

METRICS_FILE = Path("data/metrics.json")
CATALOG_FILE = Path("data/cash_prices.csv")


def rank_blockers(metrics, catalog, *, now=None, limit=15):
    current = now or datetime.now(UTC)
    counts = {}
    groups = {}
    for day in metrics.values():
        if not isinstance(day, dict):
            continue
        for name, count in (day.get("cash_blockers") or {}).items():
            if isinstance(name, str) and type(count) is int and count > 0:
                counts[name] = counts.get(name, 0) + count
        for group, count in (day.get("cash_blocker_groups") or {}).items():
            if isinstance(group, str) and type(count) is int and count > 0:
                groups[group] = groups.get(group, 0) + count
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    total_blocked = sum(groups.values())
    items = catalog.get("items", {})
    ranked = []
    selected = set()
    for name, count in ordered:
        selected.add(name)
        entry = items.get(name)
        price = entry.get("cash_price_usd") if entry else None
        verified_at = entry.get("verified_at_utc") if entry else None
        age_days = None
        if verified_at:
            try:
                verified = datetime.fromisoformat(str(verified_at).replace("Z", "+00:00"))
                if verified.tzinfo is not None:
                    age_days = max(0.0, (current - verified.astimezone(UTC)).total_seconds() / 86400)
            except ValueError:
                pass
        unlocked = sum(
            group_count for group, group_count in groups.items()
            if set(group.split("|")).issubset(selected)
        )
        ranked.append({
            "name": name, "count": count, "price": price,
            "age_days": age_days, "unlocked": unlocked,
            "unlocked_percent": (100.0 * unlocked / total_blocked) if total_blocked else 0.0,
        })
    return ranked, total_blocked


def format_blockers(metrics, catalog, *, now=None, limit=15):
    ranked, total = rank_blockers(metrics, catalog, now=now, limit=limit)
    if not ranked:
        return "No cash-price blockers recorded in the last 14 days."
    lines = [f"Top cash-price blockers ({total} blocked listing instances / 14 days):"]
    for index, item in enumerate(ranked, 1):
        price = f"${item['price']:.2f}" if isinstance(item["price"], (int, float)) else "not in catalogue"
        age = f"{item['age_days']:.1f}d" if item["age_days"] is not None else "unknown"
        lines.append(
            f"{index}. {item['name']} — {item['count']} listings; catalogue {price}; age {age}"
        )
        lines.append(
            f"   If top {index} verified: {item['unlocked_percent']:.1f}% of blocked listings unlocked"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, default=METRICS_FILE)
    parser.add_argument("--csv", type=Path, default=CATALOG_FILE)
    args = parser.parse_args(argv)
    try:
        metrics = json.loads(args.metrics.read_text(encoding="utf-8")) if args.metrics.exists() else {}
        catalog = load_cash_price_catalog(args.csv)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[next-to-verify] Could not read blocker data ({type(exc).__name__}).")
        return 0
    print(format_blockers(metrics, catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
