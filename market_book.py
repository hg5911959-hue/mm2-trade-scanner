"""Private cash-market order book and conservative opportunity calculations.

This module is pure Python and has no network or Discord integration. Inputs
must already be normalized by the owner-supplied cash-offer parser.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BLOCKED_STABILITY = frozenset({
    "decreasing", "receding", "unsafe", "untradeable", "unstable", "volatile",
})
DEFAULT_TRANSFER_FEE_USD = 0.50
DEFAULT_SAFETY_MARGIN_PERCENT = 5.0
DEFAULT_MIN_PROFIT_USD = 0.50
DEFAULT_MIN_PROFIT_PERCENT = 5.0


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else None
    try:
        parsed_dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed_dt.tzinfo is None:
            return None
        return parsed_dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _weighted_median(rows: list[dict[str, Any]], now: float) -> float | None:
    if not rows:
        return None
    weighted = []
    for row in rows:
        age = max(0.0, now - float(row["_timestamp"]))
        weight = math.exp(-age / (24 * 3600))
        weighted.append((float(row["unit_price_usd"]), weight))
    weighted.sort(key=lambda pair: pair[0])
    total = sum(weight for _, weight in weighted)
    cursor = 0.0
    for price, weight in weighted:
        cursor += weight
        if cursor >= total / 2:
            return price
    return weighted[-1][0]


def _valid_offer(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict) or row.get("side") not in {"sell", "buy"}:
        return False
    try:
        price = float(row.get("unit_price_usd"))
        quantity = int(row.get("quantity"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 0 and 1 <= quantity <= 5000 and bool(row.get("item_key"))


def build_order_book(
    offers: list[dict[str, Any]], *, now: float | None = None,
    max_age_hours: float = 48, low_price_ratio: float = 0.40,
    high_price_ratio: float = 2.50,
) -> dict[str, dict[str, Any]]:
    """Build per-item asks/bids from recent, timestamped offers only."""
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    if not math.isfinite(current) or max_age_hours <= 0:
        raise ValueError("invalid market-book time window")
    grouped: dict[str, list[dict[str, Any]]] = {}
    stale_rows: dict[str, dict[str, Any]] = {}
    stale_counts: dict[str, int] = {}
    for raw in offers or []:
        if not _valid_offer(raw):
            continue
        item_key = str(raw["item_key"])
        stamp = _timestamp(raw.get("posted_at"))
        if stamp is None or stamp > current or current - stamp > max_age_hours * 3600:
            stale_counts[item_key] = stale_counts.get(item_key, 0) + 1
            stale_rows[item_key] = dict(raw)
            continue
        row = dict(raw)
        row["_timestamp"] = stamp
        grouped.setdefault(item_key, []).append(row)

    result: dict[str, dict[str, Any]] = {}
    for item_key in sorted(set(grouped) | set(stale_counts)):
        rows = grouped.get(item_key, [])
        asks = [row for row in rows if row["side"] == "sell"]
        bids = [row for row in rows if row["side"] == "buy"]
        ask_prices = [float(row["unit_price_usd"]) for row in asks]
        bid_prices = [float(row["unit_price_usd"]) for row in bids]
        median_all = statistics.median(ask_prices + bid_prices) if asks or bids else None
        authors = {str(row.get("author") or "").strip() for row in rows if str(row.get("author") or "").strip()}
        flags: set[str] = set()
        if stale_counts.get(item_key):
            flags.add("stale_offer")
        if len(authors) < 2:
            flags.add("single_author")
        for row in rows:
            flags.update(str(value) for value in row.get("flags", []) if value)
            price = float(row["unit_price_usd"])
            if median_all and (price < median_all * low_price_ratio or price > median_all * high_price_ratio):
                row.setdefault("flags", []).append("price_far_from_median")
                flags.add("price_far_from_median")
            if row["side"] == "sell" and int(row["quantity"]) >= 100 and median_all and price < median_all * low_price_ratio:
                row.setdefault("flags", []).append("market_flooding")
                flags.add("market_flooding")
            text = str(row.get("source_text") or "").casefold()
            if row["side"] == "buy" and "sell before it drops" in text:
                row.setdefault("flags", []).append("sell_before_drop")
                flags.add("sell_before_drop")
        ask_authors = {str(row.get("author") or "").strip() for row in asks if str(row.get("author") or "").strip()}
        bid_authors = {str(row.get("author") or "").strip() for row in bids if str(row.get("author") or "").strip()}
        author_count = len(authors)
        confidence = "high" if author_count >= 3 else "medium" if author_count >= 2 else "low"
        best_ask = min(asks, key=lambda row: (float(row["unit_price_usd"]), -row["_timestamp"])) if asks else None
        best_bid = max(bids, key=lambda row: (float(row["unit_price_usd"]), row["_timestamp"])) if bids else None
        median_ask = _weighted_median(asks, current)
        median_bid = _weighted_median(bids, current)
        spread = None
        if best_ask and best_bid:
            midpoint = (float(best_ask["unit_price_usd"]) + float(best_bid["unit_price_usd"])) / 2
            spread = ((float(best_ask["unit_price_usd"]) - float(best_bid["unit_price_usd"])) / midpoint * 100) if midpoint else None
        result[item_key] = {
            "item_key": item_key,
            "item": str((rows[0] if rows else stale_rows[item_key]).get("item") or item_key),
            "best_ask": dict(best_ask) if best_ask else None,
            "best_bid": dict(best_bid) if best_bid else None,
            "median_ask": median_ask,
            "median_bid": median_bid,
            "median_market": median_all,
            "spread_percent": spread,
            "ask_quantity": sum(int(row["quantity"]) for row in asks),
            "bid_quantity": sum(int(row["quantity"]) for row in bids),
            "ask_author_count": len(ask_authors),
            "bid_author_count": len(bid_authors),
            "author_count": author_count,
            "confidence": confidence,
            "newest_timestamp": max((row["_timestamp"] for row in rows), default=None),
            "data_age_seconds": max(0.0, current - max(row["_timestamp"] for row in rows)) if rows else None,
            "stale_offer_count": stale_counts.get(item_key, 0),
            "risk_flags": sorted(flags),
            "asks": asks,
            "bids": bids,
        }
    return result


def find_direct_arbitrage(
    book: dict[str, dict[str, Any]], *, transfer_fee_usd: float = DEFAULT_TRANSFER_FEE_USD,
    safety_margin_percent: float = DEFAULT_SAFETY_MARGIN_PERCENT,
    min_profit_usd: float = DEFAULT_MIN_PROFIT_USD,
    min_profit_percent: float = DEFAULT_MIN_PROFIT_PERCENT,
) -> list[dict[str, Any]]:
    """Find ask-to-bid crosses with overlapping quantity after costs and margin."""
    fee = float(transfer_fee_usd)
    margin = float(safety_margin_percent)
    if not all(math.isfinite(v) and v >= 0 for v in (fee, margin, min_profit_usd, min_profit_percent)) or margin >= 100:
        raise ValueError("invalid arbitrage thresholds")
    opportunities = []
    for item_key, row in book.items():
        ask, bid = row.get("best_ask"), row.get("best_bid")
        if not ask or not bid:
            continue
        ask_price, bid_price = float(ask["unit_price_usd"]), float(bid["unit_price_usd"])
        quantity = min(int(ask["quantity"]), int(bid["quantity"]))
        if quantity <= 0 or ask_price >= bid_price:
            continue
        ask_author = str(ask.get("author") or "").strip().casefold()
        bid_author = str(bid.get("author") or "").strip().casefold()
        if ask_author and bid_author and ask_author == bid_author:
            continue
        gross = (bid_price - ask_price) * quantity
        fees = 2 * fee
        safety = gross * margin / 100
        net = gross - fees - safety
        cost = ask_price * quantity + fees
        profit_percent = net / cost * 100 if cost > 0 else 0.0
        if net < float(min_profit_usd) or profit_percent < float(min_profit_percent):
            continue
        opportunities.append({
            "type": "direct_arbitrage", "item_key": item_key, "item": row["item"],
            "buy": dict(ask), "sell": dict(bid), "quantity": quantity,
            "gross_profit_usd": gross, "fees_usd": fees, "safety_margin_usd": safety,
            "profit_usd": net, "profit_percent": profit_percent,
            "confidence": row["confidence"], "risk_flags": list(row["risk_flags"]),
        })
    return sorted(opportunities, key=lambda item: item["profit_usd"], reverse=True)


def find_swap_chains(
    book: dict[str, dict[str, Any]], game_values: dict[str, dict[str, Any]], *,
    tolerance_percent: float = 2.0, transfer_fee_usd: float = DEFAULT_TRANSFER_FEE_USD,
    min_profit_usd: float = DEFAULT_MIN_PROFIT_USD,
    min_profit_percent: float = DEFAULT_MIN_PROFIT_PERCENT,
    max_value_age_hours: float = 24,
) -> list[dict[str, Any]]:
    """Find 1:1 and N:1 buy→swap→sell chains; stale or unsafe values fail closed."""
    tolerance = float(tolerance_percent)
    fee = float(transfer_fee_usd)
    if not 0 <= tolerance < 100 or fee < 0 or max_value_age_hours <= 0:
        raise ValueError("invalid swap-chain thresholds")
    eligible = {}
    for key, item in game_values.items():
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("value"))
            age = float(item.get("data_age_seconds"))
        except (TypeError, ValueError):
            continue
        confidence = str(item.get("confidence") or "low").casefold()
        stability = str(item.get("stability") or "").casefold()
        if (
            not math.isfinite(value) or value <= 0 or not math.isfinite(age)
            or age < 0 or age > max_value_age_hours * 3600
            or confidence not in {"medium", "high"}
            or stability in BLOCKED_STABILITY
        ):
            continue
        eligible[key] = {**item, "value": value, "confidence": confidence, "data_age_seconds": age}

    chains = []
    for item_a, row_a in book.items():
        value_a = eligible.get(item_a)
        if not value_a or row_a.get("confidence") not in {"medium", "high"}:
            continue
        for ask in row_a.get("asks", []):
            ask_price, ask_qty = float(ask["unit_price_usd"]), int(ask["quantity"])
            for item_b, row_b in book.items():
                value_b = eligible.get(item_b)
                if (
                    item_a == item_b or not value_b
                    or row_b.get("confidence") not in {"medium", "high"}
                    or not value_b["stability"]
                ):
                    continue
                bid = row_b.get("best_bid")
                if not bid:
                    continue
                bid_price, bid_qty = float(bid["unit_price_usd"]), int(bid["quantity"])
                if bid_qty < 1:
                    continue
                # 1→1 and N→1 only; the received item must be worth the
                # offered aggregate game value within the configured tolerance.
                for qty_a in range(1, ask_qty + 1):
                    if value_b < value_a["value"] * qty_a * (1 - tolerance / 100):
                        continue
                    qty_b = 1
                    gross = bid_price * qty_b - ask_price * qty_a
                    fees = 2 * fee
                    profit = gross - fees
                    cost = ask_price * qty_a + fees
                    profit_pct = profit / cost * 100 if cost > 0 else 0.0
                    if profit < min_profit_usd or profit_pct < min_profit_percent:
                        continue
                    chains.append({
                        "type": "swap_chain", "buy_item": item_a, "swap_item": item_b,
                        "buy": dict(ask), "swap_value_in": value_a["value"] * qty_a,
                        "swap_value_out": value_b["value"], "sell": dict(bid),
                        "buy_price_per_value": ask_price / value_a["value"],
                        "sell_price_per_value": bid_price / value_b["value"],
                        "quantity_buy": qty_a, "quantity_sell": qty_b,
                        "profit_usd": profit, "profit_percent": profit_pct,
                        "fees_usd": fees,
                        "source_links": [str(ask.get("url") or ""), str(bid.get("url") or "")],
                        "data_age_seconds": max(
                            float(row_a["data_age_seconds"]), float(row_b["data_age_seconds"]),
                            float(value_a["data_age_seconds"]), float(value_b["data_age_seconds"]),
                        ),
                        "confidence": "medium" if "medium" in {row_a["confidence"], row_b["confidence"], value_a["confidence"], value_b["confidence"]} else "high",
                    })
    unique = {}
    for chain in chains:
        key = (chain["buy_item"], chain["swap_item"], chain["quantity_buy"],
               chain["buy"].get("message_id"), chain["sell"].get("message_id"))
        previous = unique.get(key)
        if previous is None or chain["profit_usd"] > previous["profit_usd"]:
            unique[key] = chain
    return sorted(unique.values(), key=lambda item: item["profit_usd"], reverse=True)


def inventory_advice(
    inventory: dict[str, Any], book: dict[str, dict[str, Any]], *,
    previous_snapshot: dict[str, Any] | None = None, swap_opportunities: list[dict[str, Any]] | None = None,
    undercut_usd: float = 0.01,
) -> list[dict[str, Any]]:
    """Return conservative sell/list/hold guidance for a private owner inventory."""
    advice = []
    upgrade_items = {str(row.get("buy_item")) for row in (swap_opportunities or []) if isinstance(row, dict)}
    entries = inventory.items() if isinstance(inventory, dict) else []
    for key, raw in entries:
        if not isinstance(raw, dict):
            continue
        item_key = str(raw.get("item_key") or key)
        try:
            quantity = int(raw.get("quantity"))
            if quantity <= 0 or quantity > 5000:
                continue
        except (TypeError, ValueError):
            continue
        market = book.get(item_key)
        if not market:
            advice.append({"item_key": item_key, "item": raw.get("item", item_key),
                           "quantity": quantity, "verdict": "HOLD",
                           "risk_flags": ["no_recent_market"]})
            continue
        ask = market.get("best_ask")
        bid = market.get("best_bid")
        best_bid = float(bid["unit_price_usd"]) if bid else None
        median = market.get("median_market")
        suggested = None
        if ask:
            suggested = max(best_bid or 0.0, float(ask["unit_price_usd"]) - max(0.0, undercut_usd))
        previous = (previous_snapshot or {}).get(item_key, {})
        falling = bool(
            isinstance(previous, dict) and previous.get("median_market") is not None
            and median is not None and float(median) < float(previous["median_market"])
        )
        risk = list(market.get("risk_flags", []))
        if market["confidence"] == "low":
            risk.append("thin_market")
        if falling:
            risk.append("price_falling")
        if item_key in upgrade_items:
            verdict = "UPGRADE VIA SWAP"
        elif not bid or market["confidence"] == "low":
            verdict = "HOLD"
        elif falling and market["bid_quantity"] >= quantity:
            verdict = "SELL NOW"
        elif market["bid_quantity"] >= quantity:
            verdict = "LIST"
        else:
            verdict = "HOLD"
        advice.append({
            "item_key": item_key, "item": raw.get("item") or market["item"],
            "quantity": quantity, "best_bid": best_bid, "median": median,
            "suggested_listing_price": suggested, "liquidity_quantity": market["bid_quantity"],
            "verdict": verdict, "risk_flags": sorted(set(risk)),
        })
    return advice


def save_private_price_history(
    path: str | Path, snapshot: dict[str, Any], *, now: float | None = None,
    retention_days: int = 14,
) -> None:
    """Atomically save a compact rolling history; caller must keep path private."""
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    target = Path(path)
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    existing = {"schema_version": 1, "snapshots": []}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("private market history is unreadable; refusing overwrite") from exc
    if not isinstance(existing, dict) or existing.get("schema_version") != 1 or not isinstance(existing.get("snapshots"), list):
        raise RuntimeError("private market history schema is invalid; refusing overwrite")
    snapshots = [row for row in existing["snapshots"] if isinstance(row, dict)
                 and _timestamp(row.get("timestamp")) is not None
                 and 0 <= current - float(_timestamp(row["timestamp"])) <= retention_days * 86400]
    if snapshots and current - float(_timestamp(snapshots[-1]["timestamp"])) < 3600:
        return
    compact = {
        str(key): {
            "best_ask": value.get("best_ask", {}).get("unit_price_usd") if value.get("best_ask") else None,
            "best_bid": value.get("best_bid", {}).get("unit_price_usd") if value.get("best_bid") else None,
            "median_market": value.get("median_market"),
            "author_count": value.get("author_count", 0),
        }
        for key, value in snapshot.items() if isinstance(value, dict)
    }
    snapshots.append({"timestamp": current, "items": compact})
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "snapshots": snapshots}, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
