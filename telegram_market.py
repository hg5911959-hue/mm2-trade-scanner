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
    build_order_book, find_direct_arbitrage, find_swap_chains, inventory_advice,
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


def _load_inventory() -> dict[str, Any]:
    state = _read_object(INVENTORY_FILE, {"schema_version": 1, "items": {}})
    if state.get("schema_version") != 1 or not isinstance(state.get("items"), dict):
        raise RuntimeError("private inventory has an invalid schema")
    return state["items"]


def _save_inventory(items: dict[str, Any]) -> None:
    _atomic_json(INVENTORY_FILE, {"schema_version": 1, "items": items})


def _load_market(now: float | None = None) -> dict[str, Any]:
    import scanner

    offers, _hints = _load_offers()
    current = datetime.now(UTC).timestamp() if now is None else float(now)
    book = build_order_book(
        offers, now=current,
        max_age_hours=float(os.environ.get("MARKET_OFFER_MAX_AGE_HOURS", "48")),
        low_price_ratio=float(os.environ.get("MARKET_LOW_PRICE_RATIO", "0.40")),
        high_price_ratio=float(os.environ.get("MARKET_HIGH_PRICE_RATIO", "2.50")),
    )
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
            cache_age = max(0.0, current - float(cache_document.get("fetched_at", 0)))
        except (TypeError, ValueError):
            cache_age = None
    game_values: dict[str, dict[str, Any]] = {}
    if (
        isinstance(cache_document, dict) and cache_age is not None
        and cache_age <= GAME_VALUES_MAX_AGE_SECONDS
    ):
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
        "cache_age": cache_age, "previous": previous, "offers_count": len(offers),
    }


def _format_item(item: dict[str, Any], market: dict[str, Any], game: dict[str, Any] | None) -> str:
    ask, bid = market.get("best_ask"), market.get("best_bid")
    ask_text = _format_usd(ask.get("unit_price_usd")) if ask else "غير متاح"
    bid_text = _format_usd(bid.get("unit_price_usd")) if bid else "غير متاح"
    median = _format_usd(market.get("median_market"))
    spread = market.get("spread_percent")
    spread_text = f"{spread:.1f}%" if isinstance(spread, (int, float)) else "—"
    age = market.get("data_age_seconds")
    age_text = f"{int(age // 60)} دقيقة" if isinstance(age, (int, float)) else "قديم/غير متاح"
    value_text = "قديم/غير متاح"
    ratio_text = "—"
    if game:
        value_text = f"{game['value']:g}"
        if ask:
            ratio_text = f"{float(ask['unit_price_usd']) / game['value']:.4f}"
    risk_flags = list(market.get("risk_flags", []))
    if not game:
        risk_flags.append("no_value_data")
    risks = "، ".join(sorted(set(risk_flags))) or "لا توجد"
    return (
        f"{item.get('item') or market.get('item')}\n"
        f"أفضل بيع: {ask_text} | أفضل شراء: {bid_text}\n"
        f"الوسيط: {median} | الفارق: {spread_text}\n"
        f"الكميات: بيع {market.get('ask_quantity', 0)} / شراء {market.get('bid_quantity', 0)}\n"
        f"العمر: {age_text} | قيمة اللعبة: {value_text} | $/Value: {ratio_text}\n"
        f"الثقة: {market.get('confidence')} | المخاطر: {risks}"
    )


def _resolve_command_item(raw: str) -> tuple[str | None, str]:
    return resolve_cash_item(raw, load_aliases())


def _command_reply(command: str, snapshot: dict[str, Any], *, inventory_enabled: bool) -> str:
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
            if not market:
                replies.append(f"{match}: لا توجد عروض حديثة.")
            else:
                replies.append(_format_item({"item": match}, market, snapshot["game_values"].get(key)))
            index = end_match
        return "\n\n".join(replies)
    if verb in {"/buy", "/chains"}:
        rows = snapshot["chains"] if verb == "/chains" else sorted(
            snapshot["direct"] + snapshot["chains"],
            key=lambda row: float(row.get("profit_usd", 0)), reverse=True,
        )
        if not rows:
            return "لا توجد فرصة مؤكدة تتجاوز حدود الربح والمخاطر الحالية."
        output = []
        for row in rows[:8]:
            if row["type"] == "direct_arbitrage":
                output.append(
                    f"مراجحة مباشرة: {row['item']} ×{row['quantity']}\n"
                    f"شراء { _format_usd(row['buy']['unit_price_usd']) } ← بيع { _format_usd(row['sell']['unit_price_usd']) }\n"
                    f"الربح الصافي: {_format_usd(row['profit_usd'])} ({row['profit_percent']:.1f}%)"
                )
            else:
                output.append(
                    f"شراء {row['buy_item']} ×{row['quantity_buy']} → مبادلة {row['swap_item']} → بيع ×1\n"
                    f"الربح الصافي: {_format_usd(row['profit_usd'])} ({row['profit_percent']:.1f}%)\n"
                    f"المصادر: {row['source_links'][0] or 'غير متاح'} | {row['source_links'][1] or 'غير متاح'}"
                )
        return "\n\n".join(output)
    if verb in {"/sell", "/inv", "/add", "/remove"}:
        if not inventory_enabled:
            return "إدارة المخزون معطّلة حالياً."
        items = _load_inventory()
        if verb == "/inv":
            if not items:
                return "المخزون فارغ."
            return "\n".join(
                f"{row.get('item', key)} ×{row.get('quantity', 0)}"
                + (f" | التكلفة {_format_usd(row.get('cost_usd'))}" if row.get("cost_usd") is not None else "")
                for key, row in items.items() if isinstance(row, dict)
            )
        if verb == "/add":
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
            _save_inventory(items)
            return f"تم تحديث {canonical}: ×{prior['quantity']}."
        if verb == "/remove":
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
            _save_inventory(items)
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


def _save_forwarded_post(message: dict[str, Any], chat_id: str) -> tuple[int, int, dict[str, int]]:
    raw = _message_text(message)
    link, body = _discord_link_and_body(raw)
    origin = message.get("forward_origin") if isinstance(message.get("forward_origin"), dict) else {}
    posted = origin.get("date") or message.get("date")
    try:
        posted_at = datetime.fromtimestamp(int(posted), UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        posted_at = datetime.now(UTC).isoformat()
    message_id = f"telegram:{chat_id}:{message.get('message_id', '')}"
    result = parse_cash_post(
        body, author=_forward_author(message), message_id=message_id,
        url=link, posted_at=posted_at,
    )
    offers, hints = _load_offers()
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
                "direct", row.get("item_key"), row.get("buy", {}).get("message_id"),
                row.get("sell", {}).get("message_id"), row.get("quantity"),
            ]
            message = (
                f"فرصة مراجحة نقدية: {row.get('item')} ×{row.get('quantity')}\n"
                f"شراء {_format_usd(row['buy'].get('unit_price_usd'))} ← "
                f"بيع {_format_usd(row['sell'].get('unit_price_usd'))}\n"
                f"الربح الصافي: {_format_usd(profit)} ({float(row.get('profit_percent', 0)):.1f}%)"
            )
        elif row.get("type") == "swap_chain":
            identity = [
                "chain", row.get("buy_item"), row.get("swap_item"),
                row.get("buy", {}).get("message_id"), row.get("sell", {}).get("message_id"),
                row.get("quantity_buy"), row.get("quantity_sell"),
            ]
            message = (
                f"فرصة سلسلة تبادل: {row.get('buy_item')} ×{row.get('quantity_buy')} "
                f"→ {row.get('swap_item')} → بيع ×{row.get('quantity_sell')}\n"
                f"الربح الصافي: {_format_usd(profit)} ({float(row.get('profit_percent', 0)):.1f}%)"
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
    if state.get("schema_version") != 1 or type(state.get("offset")) is not int:
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
        sender_chat_id = str(chat.get("id") or "")
        if sender_chat_id != str(chat_id):
            offset = update_id + 1
            _atomic_json(UPDATES_FILE, {"schema_version": 1, "offset": offset})
            continue
        text = _message_text(message).strip()
        if queued:
            send_message(token, chat_id, "تم وضع الطلب في قائمة الانتظار؛ جارٍ معالجته الآن.")
        if text.startswith("/"):
            snapshot = _load_market(now=current)
            reply = _command_reply(text, snapshot, inventory_enabled=inventory_enabled)
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
