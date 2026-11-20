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
BLOCKED_OFFER_FLAGS = frozenset({
    "invalid_flags", "market_flooding", "no_currency_symbol",
    "owner_supplied_author", "price_far_from_median", "sell_before_drop",
    "unsupported_payment",
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
        raw_quantity = row.get("quantity")
        quantity = int(raw_quantity)
        raw_minimum = row.get("min_quantity")
        minimum = 1 if raw_minimum is None else int(raw_minimum)
        if (
            isinstance(raw_quantity, bool) or float(raw_quantity) != quantity
            or (raw_minimum is not None and (
                isinstance(raw_minimum, bool) or float(raw_minimum) != minimum
            ))
        ):
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(price) and price > 0 and 1 <= quantity <= 5000
        and 1 <= minimum <= quantity and bool(row.get("item_key"))
    )


def _unsupported_payment(row: dict[str, Any]) -> bool:
    """Only USD-denominated offers may enter a USD order book."""
    payment = row.get("payment")
    if payment is None or not str(payment).strip():
        return False
    return str(payment).strip().casefold() not in {"usd", "us dollar", "dollar"}


def _offer_flags(row: dict[str, Any]) -> set[str]:
    """Normalize flags and fail closed when their container or entries are malformed."""
    raw = row.get("flags", [])
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return {"invalid_flags"}
    flags: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            flags.add("invalid_flags")
            continue
        flags.add(value.strip().casefold())
    return flags


def _offer_is_actionable(row: dict[str, Any]) -> bool:
    """Require a verified author and exclude offers with blocking risk flags."""
    return bool(_trusted_author(row)) and not (_offer_flags(row) & BLOCKED_OFFER_FLAGS)


def _add_offer_flag(row: dict[str, Any], flag: str) -> None:
    """Append a risk flag while repairing malformed optional flag containers."""
    flags = row.get("flags")
    if not isinstance(flags, list):
        flags = sorted(_offer_flags(row))
        row["flags"] = flags
    if flag not in flags:
        flags.append(flag)


def _trusted_author(row: dict[str, Any]) -> str:
    """Only validated Discord API author IDs contribute to independence."""
    if (
        "owner_supplied_author" in _offer_flags(row)
        or row.get("author_id_source") != "discord_api"
    ):
        return ""
    author_id = str(row.get("author_id") or "").strip()
    if not author_id.isdigit() or not 15 <= len(author_id) <= 22:
        return ""
    return author_id


def build_order_book(
    offers: list[dict[str, Any]], *, now: float | None = None,
    max_age_hours: float = 48, low_price_ratio: float = 0.40,
    high_price_ratio: float = 2.50,
) -> dict[str, dict[str, Any]]:
    """Build per-item asks/bids from recent, timestamped offers only."""
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    max_age_hours = float(max_age_hours)
    low_price_ratio = float(low_price_ratio)
    high_price_ratio = float(high_price_ratio)
    if (
        not all(math.isfinite(value) for value in (current, max_age_hours, low_price_ratio, high_price_ratio))
        or current <= 0 or max_age_hours <= 0
        or not 0 < low_price_ratio < 1 or high_price_ratio <= 1
    ):
        raise ValueError("invalid order-book age or outlier thresholds")
    max_age_seconds = max_age_hours * 3600
    if not math.isfinite(max_age_seconds):
        raise ValueError("order-book maximum age is too large")
    grouped: dict[str, list[dict[str, Any]]] = {}
    stale_rows: dict[str, dict[str, Any]] = {}
    stale_counts: dict[str, int] = {}
    for raw in offers or []:
        if not _valid_offer(raw):
            continue
        item_key = str(raw["item_key"])
        stamp = _timestamp(raw.get("posted_at"))
        if stamp is None or stamp > current or current - stamp > max_age_seconds:
            stale_counts[item_key] = stale_counts.get(item_key, 0) + 1
            stale_rows[item_key] = dict(raw)
            continue
        row = dict(raw)
        row["_timestamp"] = stamp
        grouped.setdefault(item_key, []).append(row)

    result: dict[str, dict[str, Any]] = {}
    for item_key in sorted(set(grouped) | set(stale_counts)):
        rows = grouped.get(item_key, [])
        # USD cash prices must not mix with USDT or currency-ambiguous rows.
        # Mark these before computing the benchmark so they cannot distort it.
        for row in rows:
            if _unsupported_payment(row):
                _add_offer_flag(row, "unsupported_payment")

        # Build the outlier benchmark only from independently attributable,
        # currently unflagged offers. Collapse repeated posts by each author
        # and side so one account cannot dominate the reference median.
        benchmark_prices_by_author: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            author = _trusted_author(row)
            if author and not (_offer_flags(row) & BLOCKED_OFFER_FLAGS):
                key = (str(row["side"]), author)
                benchmark_prices_by_author.setdefault(key, []).append(
                    float(row["unit_price_usd"])
                )
        benchmark_prices_by_side: dict[str, list[float]] = {"sell": [], "buy": []}
        for (side, _author), prices in benchmark_prices_by_author.items():
            benchmark_prices_by_side[side].append(statistics.median(prices))
        median_by_side = {
            side: statistics.median(prices) if prices else None
            for side, prices in benchmark_prices_by_side.items()
        }
        benchmark_prices = [
            statistics.median(prices)
            for prices in benchmark_prices_by_author.values()
        ]
        median_all = statistics.median(benchmark_prices) if benchmark_prices else None
        flags: set[str] = set()
        if stale_counts.get(item_key):
            flags.add("stale_offer")
        for row in rows:
            row_flags = _offer_flags(row)
            flags.update(row_flags)
            price = float(row["unit_price_usd"])
            side_median = median_by_side.get(row["side"])
            if side_median and (
                price < side_median * low_price_ratio
                or price > side_median * high_price_ratio
            ):
                _add_offer_flag(row, "price_far_from_median")
                flags.add("price_far_from_median")
            if row["side"] == "sell" and int(row["quantity"]) >= 100 and side_median and price < side_median * low_price_ratio:
                _add_offer_flag(row, "market_flooding")
                flags.add("market_flooding")
            text = str(row.get("source_text") or "").casefold()
            if row["side"] == "buy" and "sell before it drops" in text:
                _add_offer_flag(row, "sell_before_drop")
                flags.add("sell_before_drop")

        # Unverified pasted author labels and flagged outliers remain visible as
        # risk metadata, but cannot influence actionable depth or confidence.
        actionable_rows = [row for row in rows if _offer_is_actionable(row)]
        asks = [row for row in actionable_rows if row["side"] == "sell"]
        bids = [row for row in actionable_rows if row["side"] == "buy"]
        authors = {author for row in actionable_rows if (author := _trusted_author(row))}
        ask_authors = {author for row in asks if (author := _trusted_author(row))}
        bid_authors = {author for row in bids if (author := _trusted_author(row))}
        author_count = len(authors)
        if author_count < 2:
            flags.add("single_author")
        confidence = (
            "high" if len(ask_authors) >= 2 and len(bid_authors) >= 2
            else "medium" if asks and bids and author_count >= 2
            else "low"
        )
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
    """Find the best independent ask/bid pair per item after costs and margin."""
    raw_thresholds = (
        transfer_fee_usd, safety_margin_percent, min_profit_usd, min_profit_percent
    )
    if any(isinstance(value, bool) for value in raw_thresholds):
        raise ValueError("invalid arbitrage thresholds")
    try:
        fee, margin, min_profit_usd, min_profit_percent = (
            float(value) for value in raw_thresholds
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid arbitrage thresholds") from None
    if (
        not all(math.isfinite(value) and value >= 0 for value in (
            fee, margin, min_profit_usd, min_profit_percent
        ))
        or margin >= 100
    ):
        raise ValueError("invalid arbitrage thresholds")

    opportunities = []
    for item_key, row in book.items():
        if row.get("confidence") not in {"medium", "high"}:
            continue
        asks = row.get("asks") or ([row["best_ask"]] if row.get("best_ask") else [])
        bids = row.get("bids") or ([row["best_bid"]] if row.get("best_bid") else [])
        best_candidate = None
        for ask in asks:
            if not _offer_is_actionable(ask):
                continue
            ask_price = float(ask["unit_price_usd"])
            ask_author = _trusted_author(ask)
            for bid in bids:
                if not _offer_is_actionable(bid):
                    continue
                bid_author = _trusted_author(bid)
                if not ask_author or not bid_author or ask_author == bid_author:
                    continue
                bid_price = float(bid["unit_price_usd"])
                quantity = min(int(ask["quantity"]), int(bid["quantity"]))
                minimum_quantity = max(
                    int(ask.get("min_quantity") or 1),
                    int(bid.get("min_quantity") or 1),
                )
                if quantity < minimum_quantity or ask_price >= bid_price:
                    continue
                gross = (bid_price - ask_price) * quantity
                fees = 2 * fee
                safety = gross * margin / 100
                net = gross - fees - safety
                cost = ask_price * quantity + fees
                profit_percent = net / cost * 100 if cost > 0 else 0.0
                if net < min_profit_usd or profit_percent < min_profit_percent:
                    continue
                candidate = {
                    "type": "direct_arbitrage", "item_key": item_key, "item": row["item"],
                    "buy": dict(ask), "sell": dict(bid), "quantity": quantity,
                    "gross_profit_usd": gross, "fees_usd": fees,
                    "safety_margin_usd": safety, "profit_usd": net,
                    "profit_percent": profit_percent,
                    "confidence": row["confidence"], "risk_flags": list(row["risk_flags"]),
                }
                if (
                    best_candidate is None
                    or candidate["profit_usd"] > best_candidate["profit_usd"]
                ):
                    best_candidate = candidate
        if best_candidate is not None:
            opportunities.append(best_candidate)
    return sorted(opportunities, key=lambda item: item["profit_usd"], reverse=True)


def find_swap_chains(
    book: dict[str, dict[str, Any]], game_values: dict[str, dict[str, Any]], *,
    tolerance_percent: float = 2.0, transfer_fee_usd: float = DEFAULT_TRANSFER_FEE_USD,
    min_profit_usd: float = DEFAULT_MIN_PROFIT_USD,
    min_profit_percent: float = DEFAULT_MIN_PROFIT_PERCENT,
    max_value_age_hours: float = 24,
) -> list[dict[str, Any]]:
    """Find conservative 1:1 and N:1 buy→swap→sell chains."""
    tolerance = float(tolerance_percent)
    fee = float(transfer_fee_usd)
    min_profit = float(min_profit_usd)
    min_profit_pct = float(min_profit_percent)
    max_value_age = float(max_value_age_hours)
    if (
        not all(math.isfinite(value) for value in (tolerance, fee, min_profit, min_profit_pct, max_value_age))
        or not 0 <= tolerance < 100 or fee < 0 or min_profit < 0
        or min_profit_pct < 0 or max_value_age <= 0
    ):
        raise ValueError("invalid swap-chain thresholds")
    max_value_age_seconds = max_value_age * 3600
    if not math.isfinite(max_value_age_seconds):
        raise ValueError("swap-chain maximum value age is too large")

    eligible: dict[str, dict[str, Any]] = {}
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
            or age < 0 or age > max_value_age_seconds
            or confidence not in {"medium", "high"}
            or stability in BLOCKED_STABILITY or not stability
        ):
            continue
        eligible[key] = {
            **item, "value": value, "confidence": confidence, "data_age_seconds": age,
        }

    chains: list[dict[str, Any]] = []
    fees = 2 * fee
    for item_a, row_a in book.items():
        value_a = eligible.get(item_a)
        if not value_a or row_a.get("confidence") not in {"medium", "high"}:
            continue
        for ask in row_a.get("asks", []):
            if not _offer_is_actionable(ask):
                continue
            ask_author = _trusted_author(ask)
            if not ask_author:
                continue
            ask_price = float(ask["unit_price_usd"])
            ask_qty = int(ask["quantity"])
            minimum_ask_quantity = max(1, int(ask.get("min_quantity") or 1))
            if ask_qty < minimum_ask_quantity:
                continue
            for item_b, row_b in book.items():
                value_b = eligible.get(item_b)
                if (
                    item_a == item_b or not value_b
                    or row_b.get("confidence") not in {"medium", "high"}
                ):
                    continue
                bid = row_b.get("best_bid")
                if (
                    not bid or not _offer_is_actionable(bid)
                    or int(bid.get("quantity", 0)) < 1
                ):
                    continue
                bid_author = _trusted_author(bid)
                if not bid_author or bid_author == ask_author:
                    continue
                # The sell leg is one received item. A buyer demanding 2+ units
                # cannot fill that leg, so the chain is rejected.
                if int(bid.get("min_quantity") or 1) > 1:
                    continue
                try:
                    value_limit = math.floor(
                        value_b["value"] / (value_a["value"] * (1 - tolerance / 100))
                    )
                    usd_limit = math.floor((float(bid["unit_price_usd"]) - fees - min_profit) / ask_price)
                    pct_ratio = min_profit_pct / 100
                    percent_limit = math.floor(
                        (float(bid["unit_price_usd"]) - fees * (1 + pct_ratio))
                        / (ask_price * (1 + pct_ratio))
                    )
                except (OverflowError, ZeroDivisionError, ValueError):
                    continue
                maximum_ask_quantity = min(ask_qty, value_limit, usd_limit, percent_limit)
                if maximum_ask_quantity < minimum_ask_quantity:
                    continue

                # Keep the highest-profit minimum lot and, when different, the
                # largest still-profitable lot. This preserves N:1 discovery
                # without an O(5,000) quantity loop for every item pair.
                quantities = [minimum_ask_quantity]
                if maximum_ask_quantity != minimum_ask_quantity:
                    quantities.append(maximum_ask_quantity)
                bid_price = float(bid["unit_price_usd"])
                for qty_a in quantities:
                    profit = bid_price - ask_price * qty_a - fees
                    cost = ask_price * qty_a + fees
                    profit_pct = profit / cost * 100 if cost > 0 else 0.0
                    if profit < min_profit or profit_pct < min_profit_pct:
                        continue
                    chains.append({
                        "type": "swap_chain", "buy_item": item_a, "swap_item": item_b,
                        "buy": dict(ask), "swap_value_in": value_a["value"] * qty_a,
                        "swap_value_out": value_b["value"], "sell": dict(bid),
                        "ask_to_value_ratio": ask_price / value_a["value"],
                        "buy_price_per_value": ask_price / value_a["value"],
                        "sell_price_per_value": bid_price / value_b["value"],
                        "quantity_buy": qty_a, "quantity_sell": 1,
                        "profit_usd": profit, "profit_percent": profit_pct,
                        "fees_usd": fees,
                        "source_links": [str(ask.get("url") or ""), str(bid.get("url") or "")],
                        "data_age_seconds": max(
                            float(row_a["data_age_seconds"]), float(row_b["data_age_seconds"]),
                            float(value_a["data_age_seconds"]), float(value_b["data_age_seconds"]),
                        ),
                        "confidence": "medium" if "medium" in {
                            row_a["confidence"], row_b["confidence"],
                            value_a["confidence"], value_b["confidence"],
                        } else "high",
                        "risk_flags": sorted(set(
                            list(row_a.get("risk_flags", [])) + list(row_b.get("risk_flags", []))
                        )),
                    })
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for chain in chains:
        key = (
            chain["buy_item"], chain["swap_item"], chain["quantity_buy"],
            chain["buy"].get("message_id"), chain["sell"].get("message_id"),
        )
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
    undercut = float(undercut_usd)
    if not math.isfinite(undercut) or undercut < 0:
        raise ValueError("undercut_usd must be finite and non-negative")
    advice = []
    upgrade_quantities: dict[str, int] = {}
    for row in swap_opportunities or []:
        if not isinstance(row, dict) or not row.get("buy_item"):
            continue
        try:
            required = max(1, int(row.get("quantity_buy", 1)))
        except (TypeError, ValueError):
            continue
        key = str(row["buy_item"])
        upgrade_quantities[key] = min(upgrade_quantities.get(key, required), required)
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
            suggested = max(best_bid or 0.0, float(ask["unit_price_usd"]) - undercut)
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
        bid_minimum = max(1, int(bid.get("min_quantity") or 1)) if bid else None
        below_bid_minimum = bool(bid and quantity < bid_minimum)
        if below_bid_minimum:
            risk.append("below_buyer_minimum")
        can_upgrade = item_key in upgrade_quantities and quantity >= upgrade_quantities[item_key]
        if can_upgrade:
            verdict = "UPGRADE VIA SWAP"
        elif not bid or below_bid_minimum or market["confidence"] == "low":
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
    if not math.isfinite(current) or current <= 0:
        raise ValueError("history timestamp must be finite and positive")
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
