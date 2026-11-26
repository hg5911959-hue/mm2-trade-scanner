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
    """Export only Chroma completed-sales Price (7d) and Sold (7d)."""
    empty = {
        "available": False, "itemCount": 0, "pricedItemCount": 0,
        "totalSold7d": 0, "missingPriceCount": 0,
        "missingSupplyCount": 0, "lastCheckedAt": None,
        "latestPriceUpdateAt": None, "priceBasis": "chromas_gg_7d_price",
    }
    path = Path(catalogue_path)
    if not path.exists():
        return empty, []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            return empty, []
        rows = snapshot.get("items")
        expected = snapshot.get("catalog_expected_count")
    except (OSError, ValueError, TypeError):
        return empty, []
    if (
        snapshot.get("schema_version") != 6
        or snapshot.get("price_basis") != "chromas_gg_7d_price"
        or snapshot.get("catalog_complete") is not True
        or type(expected) is not int or expected <= 0
        or not isinstance(rows, list) or len(rows) != expected
    ):
        return empty, []

    def optional_count(value):
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    clean, checked_times, updated_times = [], [], []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "verified" or not row.get("name"):
            return empty, []
        markets = row.get("marketplace_prices")
        if not isinstance(markets, dict) or set(markets) - {"chromas.gg"}:
            return empty, []
        chroma = markets.get("chromas.gg")
        selected = row.get("selected_marketplace")
        sales = row.get("selected_marketplace_sold_7d", 0)
        price, display = row.get("cash_price_usd"), str(row.get("price_display") or "")
        if selected is None:
            if price is not None or display or type(sales) is not int or sales != 0:
                return empty, []
            # A zero-sales Chroma row is valid only when it explicitly reports
            # no 7d price and zero completed sales. An absent row is also valid.
            if chroma is not None and (
                not isinstance(chroma, dict)
                or chroma.get("marketplace") != "chromas.gg"
                or chroma.get("price_usd") is not None
                or str(chroma.get("price_display") or "") != ""
                or type(chroma.get("sold_7d")) is not int
                or chroma.get("sold_7d") != 0
            ):
                return empty, []
            price_value = None
        else:
            if selected != "chromas.gg" or not isinstance(chroma, dict):
                return empty, []
            if chroma.get("marketplace") != "chromas.gg":
                return empty, []
            try:
                price_value = float(price)
                market_price = float(chroma.get("price_usd"))
                display_price = float(display.removeprefix("$").replace(",", ""))
            except (TypeError, ValueError, OverflowError):
                return empty, []
            if (
                type(sales) is not int or sales <= 0
                or chroma.get("sold_7d") != sales
                or not math.isfinite(price_value) or price_value <= 0
                or not math.isfinite(market_price) or abs(price_value - market_price) > 1e-7
                or not math.isfinite(display_price) or abs(price_value - display_price) > 1e-7
                or not display.startswith("$")
            ):
                return empty, []
        if type(sales) is not int or sales < 0 or (price_value is None and sales != 0):
            return empty, []
        checked = str(row.get("last_checked_at_utc") or "")
        updated = str(row.get("price_updated_at_utc") or "")
        if checked:
            checked_times.append(checked)
        if updated and price_value is not None:
            updated_times.append(updated)
        clean.append({
            "name": str(row["name"]),
            "category": str(row.get("robase_category") or ""),
            "priceUsd": price_value,
            "sold7d": sales,
            "copies": optional_count(row.get("bloxswaps_copies")),
            "holders": optional_count(row.get("bloxswaps_holders")),
            "priceBasis": "chromas.gg Price (7d)" if price_value is not None else "No Chroma sales · 7d",
            "lastCheckedAt": checked or None,
            "priceUpdatedAt": updated or None,
            "url": str(row.get("source") or ""),
        })
    priced = sum(item["priceUsd"] is not None for item in clean)
    summary = {
        "available": True, "itemCount": len(clean), "pricedItemCount": priced,
        "totalSold7d": sum(item["sold7d"] for item in clean),
        "missingPriceCount": len(clean) - priced,
        "missingSupplyCount": sum(item["copies"] is None or item["holders"] is None for item in clean),
        "lastCheckedAt": max(checked_times) if checked_times else None,
        "latestPriceUpdateAt": max(updated_times) if updated_times else None,
        "priceBasis": "chromas_gg_7d_price",
    }
    return summary, clean

def build_dashboard_data(db_path: str | Path, robase_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(db_path)
    generated = datetime.now(UTC).isoformat()
    robase_summary, robase_items = _robase_dashboard_payload(robase_path or "data/robase_prices.json")
    empty = {
        "generatedAt": generated,
        "summary": {
            "trackedListings": 0, "opportunities7d": 0,
            "trackedItems": robase_summary["pricedItemCount"],
            "latestSnapshot": robase_summary["lastCheckedAt"],
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
        if not {"listings", "opportunities"}.issubset(tables):
            return empty

        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        summary = {
            "trackedListings": db.execute(
                "SELECT COUNT(*) FROM listings WHERE lower(source) != 'discord'"
            ).fetchone()[0],
            "opportunities7d": 0,
            "trackedItems": robase_summary["pricedItemCount"],
            "latestSnapshot": robase_summary["lastCheckedAt"],
        }

        opportunity_rows = db.execute(
            """
            SELECT listing_id, source, detected_at_utc, give_total, receive_total,
                   profit, margin_percent, listing_url, details_json
            FROM opportunities
            WHERE lower(source) != 'discord'
            ORDER BY profit DESC, detected_at_utc DESC
            """
        ).fetchall()
        opportunities = []
        cash_opportunities_7d = 0
        for row in opportunity_rows:
            details = _decode_json(row["details_json"], {})
            if not isinstance(details, dict) or details.get("valuation_mode") != "cash":
                continue
            if row["detected_at_utc"] >= since:
                cash_opportunities_7d += 1
            if len(opportunities) >= 30:
                continue
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
        summary["opportunities7d"] = cash_opportunities_7d

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

    return {
        "generatedAt": generated,
        "summary": summary,
        "opportunities": opportunities,
        "recentListings": recent_listings,
        "valueChecks": [],
        "priceHistory": [],
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
