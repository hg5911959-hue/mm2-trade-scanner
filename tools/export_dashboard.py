"""Export a public-safe dashboard JSON payload from the SQLite history database."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _decode_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (ValueError, TypeError):
        return fallback


def _robase_dashboard_payload(catalogue_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    empty = {
        "available": False, "itemCount": 0, "expectedCount": 0,
        "verifiedCount": 0, "totalSold7d": 0, "totalSold30d": 0,
        "missingSupplyCount": 0, "lastCheckedAt": None,
        "latestPriceUpdateAt": None,
    }
    path = Path(catalogue_path)
    if not path.exists():
        return empty, []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            return empty, []
        rows = snapshot.get("items")
        expected = int(snapshot.get("catalog_expected_count") or 0)
    except (OSError, ValueError, TypeError):
        return empty, []
    if snapshot.get("catalog_complete") is not True or not isinstance(rows, list) or expected <= 0 or len(rows) != expected:
        return empty, []

    def optional_count(value):
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    clean = []
    checked_times = []
    updated_times = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "verified" or not row.get("name"):
            return empty, []
        try:
            price = float(row.get("cash_price_usd"))
            sold_7d = int(row.get("sold_7d"))
            sold_30d = int(row.get("sold_30d"))
            window_days = int(row.get("price_window_days"))
        except (TypeError, ValueError, OverflowError):
            return empty, []
        if not math.isfinite(price) or price < 0 or sold_7d < 0 or sold_30d < 0 or window_days not in {7, 30}:
            return empty, []
        checked = str(row.get("last_checked_at_utc") or "")
        updated = str(row.get("price_updated_at_utc") or "")
        if checked:
            checked_times.append(checked)
        if updated:
            updated_times.append(updated)
        clean.append({
            "name": str(row["name"]),
            "category": str(row.get("robase_category") or ""),
            "priceUsd": price,
            "sold7d": sold_7d,
            "sold30d": sold_30d,
            "copies": optional_count(row.get("bloxswaps_copies")),
            "holders": optional_count(row.get("bloxswaps_holders")),
            "priceWindowDays": window_days,
            "lastCheckedAt": checked or None,
            "priceUpdatedAt": updated or None,
            "url": str(row.get("source") or ""),
        })
    summary = {
        "available": True,
        "itemCount": len(clean),
        "expectedCount": expected,
        "verifiedCount": len(clean),
        "totalSold7d": sum(row["sold7d"] for row in clean),
        "totalSold30d": sum(row["sold30d"] for row in clean),
        "missingSupplyCount": sum(row["copies"] is None or row["holders"] is None for row in clean),
        "lastCheckedAt": max(checked_times) if checked_times else None,
        "latestPriceUpdateAt": max(updated_times) if updated_times else None,
    }
    return summary, clean


def build_dashboard_data(db_path: str | Path, robase_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(db_path)
    generated = datetime.now(UTC).isoformat()
    robase_summary, robase_items = _robase_dashboard_payload(robase_path or "data/robase_prices.json")
    empty = {
        "generatedAt": generated,
        "summary": {
            "trackedListings": 0, "opportunities7d": 0, "trackedItems": 0,
            "crossChecked": 0, "conflicts": 0, "estimated": 0,
            "latestSnapshot": None,
        },
        "opportunities": [],
        "recentListings": [],
        "valueChecks": [],
        "priceHistory": [],
        "robaseSummary": robase_summary,
        "robaseItems": robase_items,
    }
    if not path.exists():
        return empty

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        tables = {
            row["name"] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"market_snapshots", "market_values", "listings", "opportunities"}.issubset(tables):
            return empty

        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        summary = {
            "trackedListings": db.execute(
                "SELECT COUNT(*) FROM listings WHERE lower(source) != 'discord'"
            ).fetchone()[0],
            "opportunities7d": db.execute(
                "SELECT COUNT(*) FROM opportunities WHERE lower(source) != 'discord' AND detected_at_utc >= ?",
                (since,),
            ).fetchone()[0],
            "trackedItems": db.execute(
                "SELECT COUNT(DISTINCT item_key) FROM market_values"
            ).fetchone()[0],
            "crossChecked": db.execute(
                "SELECT COUNT(*) FROM market_values WHERE snapshot_id=(SELECT MAX(id) FROM market_snapshots) AND confidence='cross_checked'"
            ).fetchone()[0],
            "conflicts": db.execute(
                "SELECT COUNT(*) FROM market_values WHERE snapshot_id=(SELECT MAX(id) FROM market_snapshots) AND confidence='conflict'"
            ).fetchone()[0],
            "estimated": db.execute(
                "SELECT COUNT(*) FROM market_values WHERE snapshot_id=(SELECT MAX(id) FROM market_snapshots) AND confidence='estimated'"
            ).fetchone()[0],
            "latestSnapshot": db.execute(
                "SELECT captured_at_utc FROM market_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone(),
        }
        summary["latestSnapshot"] = summary["latestSnapshot"][0] if summary["latestSnapshot"] else None

        opportunity_rows = db.execute(
            """
            SELECT listing_id, source, detected_at_utc, give_total, receive_total,
                   profit, margin_percent, listing_url, details_json
            FROM opportunities
            WHERE lower(source) != 'discord'
            ORDER BY margin_percent DESC, detected_at_utc DESC
            LIMIT 30
            """
        ).fetchall()
        opportunities = []
        for row in opportunity_rows:
            details = _decode_json(row["details_json"], {})
            opportunities.append({
                "listingId": row["listing_id"],
                "source": row["source"],
                "detectedAt": row["detected_at_utc"],
                "giveTotal": row["give_total"],
                "receiveTotal": row["receive_total"],
                "profit": row["profit"],
                "margin": row["margin_percent"],
                "url": row["listing_url"],
                "giveItems": details.get("give_items", []),
                "receiveItems": details.get("receive_items", []),
                "manualReview": bool(details.get("manual_review")),
            })

        listing_rows = db.execute(
            """
            SELECT source, owner_name, owner_url, listing_url, first_seen_at_utc,
                   last_seen_at_utc, give_items_json, receive_items_json
            FROM listings
            WHERE lower(source) != 'discord'
            ORDER BY last_seen_at_utc DESC
            LIMIT 40
            """
        ).fetchall()
        recent_listings = [{
            "source": row["source"],
            "owner": row["owner_name"],
            "ownerUrl": row["owner_url"],
            "url": row["listing_url"],
            "firstSeen": row["first_seen_at_utc"],
            "lastSeen": row["last_seen_at_utc"],
            "giveItems": _decode_json(row["give_items_json"], []),
            "receiveItems": _decode_json(row["receive_items_json"], []),
        } for row in listing_rows]

        latest_values = db.execute(
            """
            SELECT item_name, category, item_type, supreme_value, secondary_value,
                   difference_percent, confidence, stability
            FROM market_values
            WHERE snapshot_id=(SELECT MAX(id) FROM market_snapshots)
              AND (supreme_value IS NOT NULL OR secondary_value IS NOT NULL)
            ORDER BY CASE confidence WHEN 'conflict' THEN 0 WHEN 'cross_checked' THEN 1 ELSE 2 END,
                     difference_percent DESC
            LIMIT 100
            """
        ).fetchall()
        value_checks = [{
            "name": row["item_name"], "category": row["category"],
            "type": row["item_type"], "supreme": row["supreme_value"],
            "secondary": row["secondary_value"], "differencePercent": row["difference_percent"],
            "confidence": row["confidence"], "stability": row["stability"],
        } for row in latest_values]

        trending = db.execute(
            """
            SELECT item_key
            FROM market_values
            GROUP BY item_key
            HAVING COUNT(DISTINCT snapshot_id) > 1
            ORDER BY COUNT(DISTINCT snapshot_id) DESC
            LIMIT 12
            """
        ).fetchall()
        price_history = []
        for item in trending:
            rows = db.execute(
                """
                SELECT v.item_name, v.supreme_value, v.secondary_value,
                       v.confidence, s.captured_at_utc
                FROM market_values v
                JOIN market_snapshots s ON s.id=v.snapshot_id
                WHERE v.item_key=?
                ORDER BY s.id DESC
                LIMIT 12
                """,
                (item["item_key"],),
            ).fetchall()
            price_history.append({
                "name": rows[0]["item_name"] if rows else item["item_key"],
                "points": [{
                    "at": row["captured_at_utc"],
                    "supreme": row["supreme_value"],
                    "secondary": row["secondary_value"],
                    "confidence": row["confidence"],
                } for row in reversed(rows)],
            })

    return {
        "generatedAt": generated,
        "summary": summary,
        "opportunities": opportunities,
        "recentListings": recent_listings,
        "valueChecks": value_checks,
        "priceHistory": price_history,
        "robaseSummary": robase_summary,
        "robaseItems": robase_items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/market_history.sqlite3")
    parser.add_argument("--output", default="public/dashboard-data.json")
    parser.add_argument("--robase-catalogue", default="data/robase_prices.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_data(args.database, args.robase_catalogue)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(
        f"[dashboard-export] listings={payload['summary']['trackedListings']} "
        f"opportunities={payload['summary']['opportunities7d']} "
        f"tracked_items={payload['summary']['trackedItems']} "
        f"discord_private_records=excluded output={output}"
    )


if __name__ == "__main__":
    main()
