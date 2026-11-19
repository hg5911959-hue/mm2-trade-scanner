"""Owner-only Telegram interface for the private MM2 cash market.

The bot polls getUpdates only during an existing GitHub Actions scan. It never
connects to Discord, executes trades, or logs message bodies or credentials.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from cash_offer_parser import load_aliases, parse_cash_post, resolve_cash_item, normalize_key
from market_book import (
    BLOCKED_STABILITY, build_order_book, find_direct_arbitrage, find_swap_chains,
    inventory_advice,
)

DATA_DIR = Path("data")
OFFERS_FILE = DATA_DIR / "cash_market_offers.json"
INVENTORY_FILE = DATA_DIR / "inventory.json"
UPDATES_FILE = DATA_DIR / "telegram_update_state.json"
LAST_RUN_FILE = DATA_DIR / "telegram_last_run.json"
HISTORY_FILE = DATA_DIR / "market_book_history.json"
MARKET_ALERTS_FILE = DATA_DIR / "market_alert_state.json"
GAME_VALUES_MAX_AGE_SECONDS = max(3600, int(os.environ.get("RBLXVALUE_MAX_CACHE_AGE_SECONDS", "86400")))
MAX_STORED_OFFERS = 20000
MAX_TELEGRAM_TEXT = 4096


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_object(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(default)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"private Telegram state is unreadable: {path.name}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"private Telegram state has an invalid schema: {path.name}")
    return payload


def _request_json(url: str, *, data: bytes | None = None, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": "MM2MarketIntelligence/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Telegram API returned an invalid response")
    return payload


def telegram_get_updates(token: str, offset: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "offset": str(offset), "timeout": "0",
        "allowed_updates": json.dumps(["message"]),
    })
    payload = _request_json(f"https://api.telegram.org/bot{token}/getUpdates?{query}", timeout=15)
    updates = payload.get("result")
    if not isinstance(updates, list):
        raise RuntimeError("Telegram getUpdates returned an invalid update list")
    return [row for row in updates if isinstance(row, dict)]


def telegram_send(token: str, chat_id: str, text: str) -> None:
    for start in range(0, len(text), MAX_TELEGRAM_TEXT):
        chunk = text[start:start + MAX_TELEGRAM_TEXT]
        body = urllib.parse.urlencode({
            "chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true",
        }).encode("utf-8")
        _request_json(f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=20)


def _format_usd(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"${number:,.2f}" if math.isfinite(number) else "—"


def _load_offers() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state = _read_object(OFFERS_FILE, {"schema_version": 1, "offers": [], "demand_hints": []})
    if state.get("schema_version") != 1 or not isinstance(state.get("offers"), list):
        raise RuntimeError("private cash-offer store has an invalid schema")
    hints = state.get("demand_hints", [])
    if not isinstance(hints, list):
        raise RuntimeError("private demand-hint store has an invalid schema")
    return [row for row in state["offers"] if isinstance(row, dict)], [
        row for row in hints if isinstance(row, dict)
    ]


def _save_offers(offers: list[dict[str, Any]], hints: list[dict[str, Any]]) -> None:
    _atomic_json(OFFERS_FILE, {
        "schema_version": 1, "offers": offers[-MAX_STORED_OFFERS:],
        "demand_hints": hints[-MAX_STORED_OFFERS:],
    })


_MANUAL_IMPORTS_INGESTED = False


def _manual_message_body(row: dict[str, Any]) -> str:
    parts = [str(row.get("content") or "")]
    embeds = row.get("embeds") or []
    if isinstance(embeds, list):
        for embed in embeds:
            if isinstance(embed, dict):
                parts.extend(str(embed.get(key) or "") for key in ("title", "description"))
                fields = embed.get("fields") or []
                if isinstance(fields, list):
                    for field in fields:
                        if isinstance(field, dict):
                            parts.extend(str(field.get(key) or "") for key in ("name", "value"))
            elif embed:
                parts.append(str(embed))
    return "\n".join(part for part in parts if part.strip())


def ingest_manual_discord_imports(
    path: str | Path = "data/discord_messages.json",
) -> tuple[int, int]:
    """Parse authorized collector/manual-secret rows once per scan process."""
    global _MANUAL_IMPORTS_INGESTED
    if _MANUAL_IMPORTS_INGESTED:
        return 0, 0
    source = Path(path)
    if not source.exists():
        _MANUAL_IMPORTS_INGESTED = True
        return 0, 0
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("authorized Discord import file is unreadable") from exc
    rows = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("authorized Discord import file has an invalid schema")
    offers, hints = _load_offers()
    added = rejected = 0
    changed = False
    seen_message_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            rejected += 1
            continue
        body = _manual_message_body(row)
        if not body:
            continue
        created = str(row.get("created_at_utc") or "")
        try:
            stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            posted_at = stamp.astimezone(UTC).isoformat()
        except (TypeError, ValueError, OverflowError):
            posted_at = ""
        url = str(row.get("url") or "")
        if not url.startswith("https://discord.com/channels/"):
            url = ""
        message_id = str(row.get("message_id") or "")
        if not message_id:
            rejected += 1
            continue
        source_id = "discord:" + message_id
        if source_id in seen_message_ids:
            continue
        seen_message_ids.add(source_id)
        retained_offers = [
            offer for offer in offers
            if str(offer.get("message_id")) != source_id
            and not (url and str(offer.get("url") or "") == url)
        ]
        retained_hints = [
            hint for hint in hints
            if str(hint.get("message_id")) != source_id
            and not (url and str(hint.get("url") or "") == url)
        ]
        changed = changed or len(retained_offers) != len(offers) or len(retained_hints) != len(hints)
        offers, hints = retained_offers, retained_hints
        known = {
            (
                str(existing.get("message_id")), str(existing.get("side")),
                str(existing.get("item_key")), int(existing.get("quantity", 0)),
                float(existing.get("unit_price_usd", 0)),
            )
            for existing in offers
        }
        parsed = parse_cash_post(
            body, author=str(row.get("author") or ""),
            message_id="discord:" + message_id, url=url, posted_at=posted_at,
        )
        if not posted_at:
            parsed["rejected"].extend(
                {"reason": "timestamp_unknown"} for _ in parsed["offers"]
            )
            parsed["offers"] = []
        rejected += len(parsed["rejected"])
        for offer in parsed["offers"]:
            identity = (
                str(offer.get("message_id")), str(offer.get("side")),
                str(offer.get("item_key")), int(offer.get("quantity", 0)),
                float(offer.get("unit_price_usd", 0)),
            )
            if identity not in known:
                offers.append(offer)
                known.add(identity)
                added += 1
        if parsed["demand_hints"]:
            changed = True
        hints.extend(parsed["demand_hints"])
    if changed or added:
        _save_offers(offers, hints)
    _MANUAL_IMPORTS_INGESTED = True
    return added, rejected


def _load_inventory_state() -> dict[str, Any]:
    state = _read_object(
        INVENTORY_FILE,
        {"schema_version": 1, "items": {}, "processed_update_ids": []},
    )
    processed = state.get("processed_update_ids", [])
    if (
        state.get("schema_version") != 1 or not isinstance(state.get("items"), dict)
        or not isinstance(processed, list)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in processed)
    ):
        raise RuntimeError("private inventory has an invalid schema")
    state["processed_update_ids"] = processed[-1000:]
    return state


def _load_inventory() -> dict[str, Any]:
    return _load_inventory_state()["items"]


def _save_inventory(
    items: dict[str, Any], processed_update_ids: list[int] | None = None,
) -> None:
    if processed_update_ids is None:
        processed_update_ids = _load_inventory_state()["processed_update_ids"]
    _atomic_json(INVENTORY_FILE, {
        "schema_version": 1, "items": items,
        "processed_update_ids": processed_update_ids[-1000:],
    })


def _csv_dry_run_fallback(now: float) -> dict[str, dict[str, Any]]:
    """Expose the legacy CSV as a clearly labelled reference only in dry-run."""
    if os.environ.get("DRY_RUN", "").strip().casefold() not in {"1", "true", "yes", "on"}:
        return {}
    from tools.cash_market import cash_price_is_fresh, load_cash_price_catalog

    current = datetime.fromtimestamp(now, UTC)
    catalog = load_cash_price_catalog(max_age_days=14, now=current)
    fallback: dict[str, dict[str, Any]] = {}
    for row in catalog["items"].values():
        status = row.get("status")
        if status not in {"verified", "unverified"}:
            continue
        if status == "verified" and not cash_price_is_fresh(row, max_age_days=14, now=current):
            continue
        name = str(row.get("name") or "").strip()
        key = normalize_key(name)
        if not key:
            continue
        price = float(row["cash_price_usd"])
        verified_at = str(row.get("verified_at_utc") or "")
        age = None
        if status == "verified":
            try:
                verified = datetime.fromisoformat(verified_at)
                age = max(0.0, now - verified.timestamp())
            except (TypeError, ValueError, OverflowError):
                continue
        fallback[key] = {
            "item": name, "best_ask": None, "best_bid": None,
            "median_ask": None, "median_bid": None, "median_market": price,
            "ask_quantity": 0, "bid_quantity": 0, "ask_authors": 0,
            "bid_authors": 0, "author_count": 0, "confidence": "low_confidence",
            "spread_percent": None, "newest_timestamp": verified_at or None,
            "data_age_seconds": age,
            "risk_flags": ["dry_run_csv_fallback"] + (
                ["unverified_csv_estimate"] if status == "unverified"
                else ["csv_reference_only"]
            ),
            "stale_offer_count": 0,
        }
    return fallback


def _load_market(now: float | None = None) -> dict[str, Any]:
    import scanner

    offers, hints = _load_offers()
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    book = build_order_book(
        offers, now=current,
        max_age_hours=float(os.environ.get("MARKET_OFFER_MAX_AGE_HOURS", "48")),
        low_price_ratio=float(os.environ.get("MARKET_LOW_PRICE_RATIO", "0.40")),
        high_price_ratio=float(os.environ.get("MARKET_HIGH_PRICE_RATIO", "2.50")),
    )
    if not offers and os.environ.get("DRY_RUN", "").strip().casefold() in {"1", "true", "yes", "on"}:
        book = _csv_dry_run_fallback(current)
    transfer_fee = float(os.environ.get("MARKET_TRANSFER_FEE_USD", "0.50"))
    direct = find_direct_arbitrage(
        book, transfer_fee_usd=transfer_fee,
        safety_margin_percent=float(os.environ.get("MARKET_SAFETY_MARGIN_PERCENT", "5.0")),
        min_profit_usd=float(os.environ.get("MIN_PROFIT_USD", "0.50")),
        min_profit_percent=float(os.environ.get("MIN_PROFIT_PERCENT", "5.0")),
    )
    cache_document = scanner._read_values_cache_document()
    cache_age = None
    if isinstance(cache_document, dict):
        try:
            fetched_raw = cache_document.get("fetched_at")
            if isinstance(fetched_raw, bool):
                raise ValueError("boolean cache timestamp")
            fetched_at = float(fetched_raw)
            if (
                not math.isfinite(current) or not math.isfinite(fetched_at)
                or current <= 0 or fetched_at <= 0 or fetched_at > current
            ):
                raise ValueError("invalid or future cache timestamp")
            cache_age = current - fetched_at
        except (TypeError, ValueError, OverflowError):
            cache_age = None
    game_values_status = "unavailable"
    if isinstance(cache_document, dict):
        if cache_age is None:
            game_values_status = "invalid"
        elif cache_age > GAME_VALUES_MAX_AGE_SECONDS:
            game_values_status = "stale"
        else:
            game_values_status = "fresh"
    game_values: dict[str, dict[str, Any]] = {}
    if game_values_status == "fresh":
        values = cache_document.get("values", {})
        aliases = scanner.load_aliases()
        if isinstance(values, dict):
            for item_key, market in book.items():
                record, source, candidates = scanner.resolve_value_record(
                    market.get("item", ""), values, aliases
                )
                if not record or len(record.get("variants") or []) != 1:
                    continue
                variant = (record.get("variants") or [record])[0]
                try:
                    value = float(variant.get("value"))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value) or value <= 0:
                    continue
                game_values[item_key] = {
                    "value": value, "stability": str(variant.get("stability") or ""),
                    "confidence": "medium" if source in {"exact", "alias", "slug"} else "low",
                    "data_age_seconds": cache_age,
                }
    chains = find_swap_chains(
        book, game_values,
        tolerance_percent=float(os.environ.get("SWAP_VALUE_TOLERANCE_PERCENT", "2.0")),
        transfer_fee_usd=transfer_fee,
        min_profit_usd=float(os.environ.get("MIN_PROFIT_USD", "0.50")),
        min_profit_percent=float(os.environ.get("MIN_PROFIT_PERCENT", "5.0")),
        max_value_age_hours=GAME_VALUES_MAX_AGE_SECONDS / 3600,
    )
    history = _read_object(HISTORY_FILE, {"schema_version": 1, "snapshots": []})
    previous = {}
    snapshots = history.get("snapshots", [])
    if isinstance(snapshots, list) and snapshots:
        last = snapshots[-1]
        if isinstance(last, dict) and isinstance(last.get("items"), dict):
            previous = last["items"]
    return {
        "book": book, "direct": direct, "chains": chains, "game_values": game_values,
        "game_values_status": game_values_status, "demand_hints": hints,
        "cache_age": cache_age, "previous": previous, "offers_count": len(offers),
    }


def _format_item(
    item: dict[str, Any], market: dict[str, Any], game: dict[str, Any] | None,
    game_values_status: str = "unavailable", demand_quantity: int = 0,
) -> str:
    ask, bid = market.get("best_ask"), market.get("best_bid")
    ask_text = _format_usd(ask.get("unit_price_usd")) if ask else "غير متاح"
    bid_text = _format_usd(bid.get("unit_price_usd")) if bid else "غير متاح"
    median = _format_usd(market.get("median_market"))
    median_ask = _format_usd(market.get("median_ask"))
    median_bid = _format_usd(market.get("median_bid"))
    spread = market.get("spread_percent")
    spread_text = f"{spread:.1f}%" if isinstance(spread, (int, float)) else "—"
    age = market.get("data_age_seconds")
    age_text = f"{int(age // 60)} دقيقة" if isinstance(age, (int, float)) else "قديم/غير متاح"
    value_text = "قديم/غير متاح"
    game_age_text = "قديم/غير متاح"
    ratio_text = "—"
    if game:
        value_text = f"{game['value']:g}"
        game_age = game.get("data_age_seconds")
        if isinstance(game_age, (int, float)) and math.isfinite(float(game_age)):
            game_age_text = f"{int(game_age // 3600)} ساعة"
        if ask:
            ratio_text = f"{float(ask['unit_price_usd']) / game['value']:.4f}"
    elif game_values_status == "stale":
        value_text = "قديم (stale)"
        game_age_text = "قديم (stale)"
    elif game_values_status == "invalid":
        value_text = "غير موثوق"
        game_age_text = "غير موثوق"

    risk_flags = list(market.get("risk_flags", []))
    if not game:
        risk_flags.append("no_value_data")
        if game_values_status == "stale":
            risk_flags.append("stale_game_values")
        elif game_values_status == "invalid":
            risk_flags.append("invalid_game_values")
    else:
        stability = str(game.get("stability") or "").strip().casefold()
        if not stability:
            risk_flags.append("missing_stability")
        elif stability in BLOCKED_STABILITY:
            risk_flags.append("untradeable")
    risks = "، ".join(sorted(set(risk_flags))) or "لا توجد"
    demand_text = f" | طلب بلا سعر: x{demand_quantity} (معلومة فقط)" if demand_quantity > 0 else ""
    return (
        f"{item.get('item') or market.get('item')}\n"
        f"أفضل بيع: {ask_text} | أفضل شراء: {bid_text}\n"
        f"وسيط البيع: {median_ask} | وسيط الشراء: {median_bid}\n"
        f"الوسيط العام: {median} | الفارق: {spread_text}\n"
        f"الكميات: بيع {market.get('ask_quantity', 0)} / شراء {market.get('bid_quantity', 0)}{demand_text}\n"
        f"عمر عروض Discord: {age_text} | عمر RBLXValue: {game_age_text}\n"
        f"قيمة اللعبة: {value_text} | $/Value: {ratio_text}\n"
        f"الثقة: {market.get('confidence')} | المخاطر: {risks}"
    )



def _demand_hint_quantity(snapshot: dict[str, Any], item_key: str) -> int:
    """Sum distinct, bounded, unpriced demand hints for display only."""
    total = 0
    seen: set[tuple[str, str]] = set()
    hints = snapshot.get("demand_hints", [])
    if not isinstance(hints, list):
        return 0
    for index, hint in enumerate(hints):
        if not isinstance(hint, dict) or str(hint.get("item_key") or "") != item_key:
            continue
        message_id = str(hint.get("message_id") or hint.get("url") or f"row:{index}")
        identity = (message_id, item_key)
        if identity in seen:
            continue
        seen.add(identity)
        quantity = hint.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 5000:
            continue
        total += quantity
    return total


def _resolve_command_item(raw: str) -> tuple[str | None, str]:
    return resolve_cash_item(raw, load_aliases())


def _command_reply(
    command: str, snapshot: dict[str, Any], *, inventory_enabled: bool,
    update_id: int | None = None,
) -> str:
    parts = command.strip().split()
    verb = parts[0].casefold() if parts else ""
    book = snapshot["book"]
    if verb == "/help":
        return (
            "أوامر MM2 Market Intelligence\n"
            "/p <item> — سعر السوق\n/buy — أفضل فرص الربح\n/chains — سلاسل التبادل\n"
            "/sell — نصائح المخزون\n/inv — مخزوني\n/add <item> <qty> [cost]\n"
            "/remove <item> [qty]\n/status — حالة البيانات والميزانية\n/help"
        )
    if verb == "/p":
        if len(parts) < 2:
            return "الاستخدام: /p <item> [item2 ...]"
        replies = []
        tokens = parts[1:]
        index = 0
        while index < len(tokens):
            match = None
            end_match = index
            for end in range(len(tokens), index, -1):
                candidate, status = _resolve_command_item(" ".join(tokens[index:end]))
                if status == "resolved" and candidate:
                    match = candidate
                    end_match = end
                    break
            if not match:
                replies.append(f"{tokens[index]}: اسم غير محسوم.")
                index += 1
                continue
            key = normalize_key(match)
            market = book.get(key)
            demand_quantity = _demand_hint_quantity(snapshot, key)
            if not market:
                if demand_quantity:
                    replies.append(
                        f"{match}\nلا توجد عروض مسعّرة حديثة.\n"
                        f"طلب بلا سعر: x{demand_quantity} (معلومة فقط؛ لا يُحتسب كأفضل شراء)."
                    )
                else:
                    replies.append(f"{match}: لا توجد عروض حديثة.")
            else:
                replies.append(_format_item(
                    {"item": match}, market, snapshot["game_values"].get(key),
                    snapshot.get("game_values_status", "unavailable"), demand_quantity,
                ))
            index = end_match
        return "\n\n".join(replies)
    if verb in {"/buy", "/chains"}:
        rows = snapshot["chains"] if verb == "/chains" else sorted(
            snapshot["direct"] + snapshot["chains"],
            key=lambda row: float(row.get("profit_usd", 0)), reverse=True,
        )
        if not rows:
            if verb == "/chains" and snapshot.get("game_values_status") == "stale":
                return "قيم اللعبة قديمة (stale)؛ سلاسل التبادل معطّلة حتى تحديث البيانات."
            return "لا توجد فرصة مؤكدة تتجاوز حدود الربح والمخاطر الحالية."
        output = []
        for row in rows[:8]:
            if row["type"] == "direct_arbitrage":
                output.append(
                    f"مراجحة مباشرة: {row['item']} ×{row['quantity']}\n"
                    f"شراء { _format_usd(row['buy']['unit_price_usd']) } ← بيع { _format_usd(row['sell']['unit_price_usd']) }\n"
                    f"الربح الصافي: {_format_usd(row['profit_usd'])} ({row['profit_percent']:.1f}%)\n"
                    f"الثقة: {row.get('confidence', 'low')}\n"
                    f"المصادر: {row.get('buy', {}).get('url') or 'غير متاح'} | {row.get('sell', {}).get('url') or 'غير متاح'}"
                    + (f"\nالمخاطر: {', '.join(row.get('risk_flags', []))}" if row.get("risk_flags") else "")
                )
            else:
                output.append(
                    f"شراء {row['buy_item']} ×{row['quantity_buy']} → مبادلة {row['swap_item']} → بيع ×1\n"
                    f"الربح الصافي: {_format_usd(row['profit_usd'])} ({row['profit_percent']:.1f}%)\n"
                    f"الثقة: {row.get('confidence', 'low')}\n"
                    f"المصادر: {row['source_links'][0] or 'غير متاح'} | {row['source_links'][1] or 'غير متاح'}"
                    + (f"\nالمخاطر: {', '.join(row.get('risk_flags', []))}" if row.get("risk_flags") else "")
                )
        return "\n\n".join(output)
    if verb in {"/sell", "/inv", "/add", "/remove"}:
        if not inventory_enabled:
            return "إدارة المخزون معطّلة حالياً."
        inventory_state = _load_inventory_state()
        items = inventory_state["items"]
        processed_update_ids = inventory_state["processed_update_ids"]
        if verb == "/inv":
            if not items:
                return "المخزون فارغ."
            return "\n".join(
                f"{row.get('item', key)} ×{row.get('quantity', 0)}"
                + (f" | التكلفة {_format_usd(row.get('cost_usd'))}" if row.get("cost_usd") is not None else "")
                for key, row in items.items() if isinstance(row, dict)
            )
        if verb == "/add":
            if update_id is not None and update_id in processed_update_ids:
                return "تم تنفيذ هذا الطلب مسبقاً؛ لم تتم إضافة كمية ثانية."
            if len(parts) < 3:
                return "الاستخدام: /add <item> <qty> [cost]"
            cost = None
            if len(parts) >= 4 and parts[-2].isdigit() and re.fullmatch(r"\d+(?:\.\d+)?", parts[-1]):
                cost = float(parts[-1])
                qty_text = parts[-2]
                name_text = " ".join(parts[1:-2])
            else:
                qty_text = parts[-1]
                name_text = " ".join(parts[1:-1])
            if not qty_text.isdigit() or not 1 <= int(qty_text) <= 5000:
                return "الكمية غير صالحة."
            canonical, status = _resolve_command_item(name_text)
            if status != "resolved" or not canonical:
                return "اسم السلاح غير محسوم؛ لم تتم إضافته."
            key = normalize_key(canonical)
            prior = items.get(key, {"item": canonical, "quantity": 0, "cost_usd": None})
            prior["quantity"] = int(prior.get("quantity", 0)) + int(qty_text)
            if cost is not None:
                if not math.isfinite(cost) or cost <= 0:
                    return "التكلفة غير صالحة."
                prior["cost_usd"] = cost
            items[key] = prior
            if update_id is not None:
                processed_update_ids.append(update_id)
            _save_inventory(items, processed_update_ids)
            return f"تم تحديث {canonical}: ×{prior['quantity']}."
        if verb == "/remove":
            if update_id is not None and update_id in processed_update_ids:
                return "تم تنفيذ هذا الطلب مسبقاً؛ لم تتم إزالة الكمية مرة ثانية."
            if len(parts) < 2:
                return "الاستخدام: /remove <item> [qty]"
            qty = None
            name_text = " ".join(parts[1:])
            if parts[-1].isdigit():
                qty = int(parts[-1])
                name_text = " ".join(parts[1:-1])
            canonical, status = _resolve_command_item(name_text)
            if status != "resolved" or not canonical:
                return "اسم السلاح غير محسوم."
            key = normalize_key(canonical)
            prior = items.get(key)
            if not prior:
                return "العنصر غير موجود في المخزون."
            if qty is None or qty >= int(prior["quantity"]):
                items.pop(key, None)
            elif qty > 0:
                prior["quantity"] = int(prior["quantity"]) - qty
            else:
                return "الكمية غير صالحة."
            if update_id is not None:
                processed_update_ids.append(update_id)
            _save_inventory(items, processed_update_ids)
            return f"تم تحديث مخزون {canonical}."
        advice = inventory_advice(
            items, book, previous_snapshot=snapshot["previous"],
            swap_opportunities=snapshot["chains"],
            undercut_usd=float(os.environ.get("MARKET_UNDERCUT_USD", "0.01")),
        )
        if not advice:
            return "المخزون فارغ."
        return "\n\n".join(
            f"{row['item']} ×{row['quantity']} — {row['verdict']}\n"
            f"أفضل شراء: {_format_usd(row.get('best_bid'))} | الوسيط: {_format_usd(row.get('median'))}\n"
            f"سعر الإدراج: {_format_usd(row.get('suggested_listing_price'))} | سيولة: {row.get('liquidity_quantity', 0)}"
            + (f"\nمخاطر: {', '.join(row['risk_flags'])}" if row.get("risk_flags") else "")
            for row in advice
        )
    if verb == "/status":
        offers, _ = _load_offers()
        budget = _read_object(Path("data/rblxvalue_request_budget.json"), {"request_timestamps_epoch": []})
        timestamps = budget.get("request_timestamps_epoch", [])
        now = datetime.now(UTC).timestamp()
        used = sum(1 for value in timestamps if not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= now - value < 86400)
        blocked = _read_object(Path("data/api_key_blocked.json"), {})
        try:
            blocked_until = float(blocked.get("blocked_until_epoch", 0) or 0)
        except (TypeError, ValueError):
            blocked_until = now + 1
        active = bool(blocked and (blocked.get("permanent") or blocked_until > now))
        age = snapshot.get("cache_age")
        age_text = f"{int(age // 3600)} ساعة" if isinstance(age, (int, float)) else "غير متاح"
        if snapshot.get("game_values_status") == "stale":
            age_text += " — قديم (stale)"
        elif snapshot.get("game_values_status") == "invalid":
            age_text += " — غير موثوق"
        offer_stamps = []
        for offer in offers:
            try:
                parsed = datetime.fromisoformat(str(offer.get("posted_at", "")).replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    offer_stamps.append(parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                continue
        cash_age = max(0.0, datetime.now(UTC).timestamp() - max(offer_stamps)) if offer_stamps else None
        cash_age_text = f"{int(cash_age // 60)} دقيقة" if cash_age is not None else "غير متاح"
        return (
            f"العروض المحفوظة: {len(offers)}\n"
            f"عمر عروض النقد: {cash_age_text}\n"
            f"عمر قيم RBLXValue: {age_text}\n"
            f"ميزانية API: {used}/60 (المتبقي {max(0, 60-used)})\n"
            f"حالة المفتاح: {'محظور' if active else 'غير محظور'}"
        )
    return "أمر غير معروف. استخدم /help."


def _message_text(message: dict[str, Any]) -> str:
    return str(message.get("text") or message.get("caption") or "")


def _forward_author(message: dict[str, Any]) -> str:
    origin = message.get("forward_origin")
    if not isinstance(origin, dict):
        return ""
    user = origin.get("sender_user")
    if isinstance(user, dict):
        return str(user.get("username") or user.get("first_name") or "")[:100]
    chat = origin.get("sender_chat")
    if isinstance(chat, dict):
        return str(chat.get("title") or "")[:100]
    return ""


def _discord_link_and_body(text: str) -> tuple[str, str]:
    match = re.search(r"https://discord\.com/channels/\d+/\d+/\d+", text)
    link = match.group(0) if match else ""
    body = re.sub(r"https://discord\.com/channels/\d+/\d+/\d+", " ", text)
    return link, body.strip()


def _extract_pasted_author(body: str) -> tuple[str, str]:
    """Read an explicit owner-supplied Discord author line without guessing."""
    match = re.search(r"(?im)^\s*(?:discord\s+)?author\s*:\s*(.*?)\s*$", body)
    if not match:
        return "", body
    author = match.group(1).strip()[:100]
    cleaned = body[:match.start()] + body[match.end():]
    return author, cleaned.strip()


def _discord_posted_at_from_link(link: str) -> str:
    match = re.search(r"/channels/\d+/\d+/(\d+)", link)
    if not match:
        return ""
    try:
        snowflake = int(match.group(1))
        timestamp_ms = (snowflake >> 22) + 1_420_070_400_000
        return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _save_forwarded_post(message: dict[str, Any], chat_id: str) -> tuple[int, int, dict[str, int]]:
    raw = _message_text(message)
    link, body = _discord_link_and_body(raw)
    pasted_author, body = _extract_pasted_author(body)
    forwarded_author = _forward_author(message)
    author = forwarded_author or pasted_author
    posted_at = _discord_posted_at_from_link(link)
    message_id = f"telegram:{chat_id}:{message.get('message_id', '')}"
    result = parse_cash_post(
        body, author=author, message_id=message_id,
        url=link, posted_at=posted_at,
    )
    if pasted_author and not forwarded_author:
        for offer in result["offers"]:
            offer["flags"] = sorted(set(offer.get("flags", []) + ["owner_supplied_author"]))
        for hint in result["demand_hints"]:
            hint["flags"] = sorted(set(hint.get("flags", []) + ["owner_supplied_author"]))
    if not posted_at:
        result["rejected"].extend(
            {"reason": "timestamp_unknown"} for _ in result["offers"]
        )
        result["offers"] = []
    offers, hints = _load_offers()
    if link:
        offers = [row for row in offers if str(row.get("url") or "") != link]
        hints = [row for row in hints if str(row.get("url") or "") != link]
    known = {
        (str(row.get("message_id")), str(row.get("side")), str(row.get("item_key")),
         int(row.get("quantity", 0)), float(row.get("unit_price_usd", 0)))
        for row in offers
    }
    added = 0
    for offer in result["offers"]:
        identity = (
            str(offer.get("message_id")), str(offer.get("side")), str(offer.get("item_key")),
            int(offer.get("quantity", 0)), float(offer.get("unit_price_usd", 0)),
        )
        if identity not in known:
            offers.append(offer)
            known.add(identity)
            added += 1
    hints.extend(result["demand_hints"])
    _save_offers(offers, hints)
    reasons: dict[str, int] = {}
    for row in result["rejected"]:
        reason = str(row.get("reason") or "rejected")
        reasons[reason] = reasons.get(reason, 0) + 1
    return added, len(result["rejected"]), reasons


def send_market_alerts(
    *, token: str, chat_id: str, snapshot: dict[str, Any], max_alerts: int = 15,
    profit_delta_usd: float = 0.50,
    send_message: Callable[[str, str, str], None] = telegram_send,
    now: float | None = None,
) -> int:
    """Send threshold-qualified opportunities and deduplicate by stable ID."""
    if not token or not chat_id:
        return 0
    limit = max(0, min(100, int(max_alerts)))
    delta = float(profit_delta_usd)
    if not math.isfinite(delta) or delta < 0:
        raise ValueError("profit-change threshold must be finite and non-negative")
    if limit == 0:
        return 0
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    if not math.isfinite(current) or current <= 0:
        raise ValueError("market-alert timestamp must be finite and positive")
    state = _read_object(MARKET_ALERTS_FILE, {"schema_version": 1, "opportunities": {}})
    if state.get("schema_version") != 1 or not isinstance(state.get("opportunities"), dict):
        raise RuntimeError("private market-alert state has an invalid schema")
    previous = state["opportunities"]
    rows = sorted(
        snapshot.get("direct", []) + snapshot.get("chains", []),
        key=lambda row: float(row.get("profit_usd", 0)), reverse=True,
    )
    sent = 0
    for row in rows:
        try:
            profit = float(row.get("profit_usd"))
            if not math.isfinite(profit) or profit <= 0:
                continue
        except (TypeError, ValueError):
            continue
        if row.get("type") == "direct_arbitrage":
            identity = [
                "direct", row.get("item_key"),
                row.get("buy", {}).get("url") or row.get("buy", {}).get("message_id"),
                row.get("sell", {}).get("url") or row.get("sell", {}).get("message_id"),
                row.get("quantity"),
            ]
            message = (
                f"فرصة مراجحة نقدية: {row.get('item')} ×{row.get('quantity')}\n"
                f"شراء {_format_usd(row['buy'].get('unit_price_usd'))} ← "
                f"بيع {_format_usd(row['sell'].get('unit_price_usd'))}\n"
                f"الربح الصافي: {_format_usd(profit)} ({float(row.get('profit_percent', 0)):.1f}%) | "
                f"الثقة: {row.get('confidence', 'low')}"
                + (f"\nالمخاطر: {', '.join(row.get('risk_flags', []))}" if row.get("risk_flags") else "")
            )
        elif row.get("type") == "swap_chain":
            identity = [
                "chain", row.get("buy_item"), row.get("swap_item"),
                row.get("buy", {}).get("url") or row.get("buy", {}).get("message_id"),
                row.get("sell", {}).get("url") or row.get("sell", {}).get("message_id"),
                row.get("quantity_buy"), row.get("quantity_sell"),
            ]
            message = (
                f"فرصة سلسلة تبادل: {row.get('buy_item')} ×{row.get('quantity_buy')} "
                f"→ {row.get('swap_item')} → بيع ×{row.get('quantity_sell')}\n"
                f"الربح الصافي: {_format_usd(profit)} ({float(row.get('profit_percent', 0)):.1f}%) | "
                f"الثقة: {row.get('confidence', 'low')}"
                + (f"\nالمخاطر: {', '.join(row.get('risk_flags', []))}" if row.get("risk_flags") else "")
            )
        else:
            continue
        identifier = hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        old = previous.get(identifier)
        try:
            old_profit = float(old.get("profit_usd")) if isinstance(old, dict) else None
        except (TypeError, ValueError):
            old_profit = None
        if old_profit is not None and abs(profit - old_profit) <= delta:
            continue
        sources = row.get("source_links", []) if row.get("type") == "swap_chain" else [
            row.get("buy", {}).get("url", ""), row.get("sell", {}).get("url", ""),
        ]
        source_text = "\nالمصادر: " + " | ".join(str(value) for value in sources if value) if any(sources) else ""
        send_message(token, chat_id, message + source_text)
        previous[identifier] = {"profit_usd": profit, "last_sent_at_epoch": current}
        previous = {
            key: value for key, value in previous.items()
            if isinstance(value, dict) and isinstance(value.get("last_sent_at_epoch"), (int, float))
            and 0 <= current - float(value["last_sent_at_epoch"]) <= 14 * 86400
        }
        state["opportunities"] = previous
        _atomic_json(MARKET_ALERTS_FILE, state)
        sent += 1
        if sent >= limit:
            break
    return sent


def process_telegram_updates(
    *, token: str, chat_id: str, market_enabled: bool, inventory_enabled: bool,
    private_state_confirmed: bool = False,
    get_updates: Callable[[str, int], list[dict[str, Any]]] = telegram_get_updates,
    send_message: Callable[[str, str, str], None] = telegram_send,
    now: float | None = None,
) -> int:
    """Poll and process owner messages. Offset advances only after safe handling."""
    # The repository is public. Never persist offers, inventory, chat IDs, or
    # Telegram offsets unless the owner explicitly confirms private storage.
    if not token or not chat_id or not market_enabled or not private_state_confirmed:
        return 0
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    state = _read_object(UPDATES_FILE, {"schema_version": 1, "offset": 0})
    if (
        state.get("schema_version") != 1
        or not isinstance(state.get("offset"), int)
        or isinstance(state.get("offset"), bool)
    ):
        raise RuntimeError("Telegram update offset state is invalid")
    offset = state["offset"]
    updates = get_updates(token, offset)
    last_run = _read_object(LAST_RUN_FILE, {})
    try:
        last_run_at = float(last_run.get("started_at_epoch", 0))
    except (TypeError, ValueError):
        last_run_at = 0
    queued = last_run_at <= 0 or current - last_run_at > 12 * 60
    processed = 0
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < offset:
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            offset = update_id + 1
            _atomic_json(UPDATES_FILE, {"schema_version": 1, "offset": offset})
            continue
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        sender_chat_id = str(chat.get("id") or "")
        sender_user_id = str(sender.get("id") or "")
        # Owner authorization is deliberately restricted to the owner's private
        # Telegram chat. A group chat can contain other members even when the
        # configured chat ID is accidentally set to that group's ID.
        if (
            str(chat.get("type") or "").casefold() != "private"
            or sender_chat_id != str(chat_id)
            or sender_user_id != str(chat_id)
        ):
            offset = update_id + 1
            _atomic_json(UPDATES_FILE, {"schema_version": 1, "offset": offset})
            continue
        text = _message_text(message).strip()
        if queued:
            send_message(token, chat_id, "تم وضع الطلب في قائمة الانتظار؛ جارٍ معالجته الآن.")
        if text.startswith("/"):
            snapshot = _load_market(now=current)
            reply = _command_reply(
                text, snapshot, inventory_enabled=inventory_enabled, update_id=update_id
            )
        elif text:
            added, rejected, reasons = _save_forwarded_post(message, chat_id)
            reason_text = "، ".join(f"{key}: {value}" for key, value in sorted(reasons.items())) or "لا يوجد"
            reply = f"تم تحليل {added} عرضاً وحفظه؛ رُفض {rejected}.\nأسباب الرفض: {reason_text}"
        else:
            reply = "أرسل أمراً أو الصق منشور Discord."
        send_message(token, chat_id, reply)
        offset = update_id + 1
        _atomic_json(UPDATES_FILE, {"schema_version": 1, "offset": offset})
        processed += 1
    _atomic_json(LAST_RUN_FILE, {"started_at_epoch": current})
    return processed
