#!/usr/bin/env python3
"""Scan a configured MM2 trade feed and send value-positive alerts to Telegram.

The scanner intentionally requires a user-configured trade-feed URL. It does not
pretend that an official public live-trade API exists.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

VALUES_URL = os.environ.get("MM2_VALUES_URL", "").strip()
TRADES_URL = os.environ.get("MM2_TRADES_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MIN_DIFF = float(os.environ.get("MIN_VALUE_DIFFERENCE", "0"))
SEEN_FILE = Path("data/seen.json")


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MM2-Trade-Scanner/1.0"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def unwrap_list(payload, keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("Feed JSON must be a list or contain one of: " + ", ".join(keys))


def normalize_values(payload):
    if isinstance(payload, dict):
        for key in ("values", "items", "data"):
            if isinstance(payload.get(key), (dict, list)):
                payload = payload[key]
                break
    result = {}
    if isinstance(payload, dict):
        for name, value in payload.items():
            if isinstance(value, (int, float)):
                result[str(name).strip().casefold()] = float(value)
            elif isinstance(value, dict):
                amount = value.get("value", value.get("price"))
                if isinstance(amount, (int, float)):
                    result[str(name).strip().casefold()] = float(amount)
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = item.get("name", item.get("item"))
            amount = item.get("value", item.get("price"))
            if name is not None and isinstance(amount, (int, float)):
                result[str(name).strip().casefold()] = float(amount)
    if not result:
        raise ValueError("Could not read item values. Expected {item_name: number} or [{name, value}].")
    return result


def item_names(side):
    if isinstance(side, str):
        return [side]
    if isinstance(side, dict):
        return [str(k) for k, v in side.items() if v]
    if isinstance(side, list):
        names = []
        for item in side:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name", item.get("item"))
                if name:
                    names.append(str(name))
        return names
    return []


def side_value(side, values):
    names = item_names(side)
    missing = [name for name in names if name.strip().casefold() not in values]
    if missing:
        raise ValueError("Missing value(s): " + ", ".join(missing))
    return sum(values[name.strip().casefold()] for name in names), names


def trade_sides(trade):
    give = next((trade[k] for k in ("give", "offering", "offer", "you_give") if k in trade), None)
    receive = next((trade[k] for k in ("receive", "want", "requested", "you_receive") if k in trade), None)
    if give is None or receive is None:
        return None, None
    return give, receive


def send_telegram(message):
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def load_seen():
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text()).get("seen", []))
    except (json.JSONDecodeError, AttributeError):
        return set()


def main():
    missing = [name for name, value in (
        ("MM2_VALUES_URL", VALUES_URL), ("MM2_TRADES_URL", TRADES_URL),
        ("TELEGRAM_BOT_TOKEN", BOT_TOKEN), ("TELEGRAM_CHAT_ID", CHAT_ID)
    ) if not value]
    if missing:
        raise RuntimeError("Configure these GitHub variables/secrets first: " + ", ".join(missing))

    values = normalize_values(fetch_json(VALUES_URL))
    trades = unwrap_list(fetch_json(TRADES_URL), ("trades", "offers", "data", "results"))
    seen = load_seen()
    new_seen = set(seen)
    sent = 0
    skipped = 0

    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            skipped += 1
            continue
        trade_id = str(trade.get("id", trade.get("trade_id", trade.get("url", index))))
        if trade_id in seen:
            continue
        give, receive = trade_sides(trade)
        if give is None:
            skipped += 1
            continue
        try:
            give_total, give_names = side_value(give, values)
            receive_total, receive_names = side_value(receive, values)
        except ValueError:
            skipped += 1
            continue

        difference = receive_total - give_total
        new_seen.add(trade_id)
        if difference >= MIN_DIFF:
            link = trade.get("url", "")
            message = (
                "MM2 VALUE ALERT\n"
                f"Trade ID: {trade_id}\n"
                f"You give: {', '.join(give_names)} ({give_total:g})\n"
                f"You receive: {', '.join(receive_names)} ({receive_total:g})\n"
                f"Difference: +{difference:g}\n"
                + (f"Link: {link}" if link else "")
            )
            send_telegram(message.strip())
            sent += 1

    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"seen": sorted(new_seen)}, indent=2) + "\n")
    print(f"Trades checked: {len(trades)} | alerts sent: {sent} | skipped: {skipped}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
