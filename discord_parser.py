"""Conservative parser for manually imported or authorized MM2 Discord messages.

Only explicit two-sided trade statements are accepted. Ambiguous messages are
rejected instead of being converted into guessed inventory or prices.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

MAX_QUANTITY = 100
_SPLIT_ITEMS = re.compile(r"\s*(?:,|;|\n|\r|\s+\+\s+)\s*")
_QTY_PREFIX = re.compile(r"^(?:(\d{1,3})\s*[x×]\s+|[x×]\s*(\d{1,3})\s+)(.+)$", re.I)
_QTY_SUFFIX = re.compile(r"^(.+?)\s+(?:[x×]\s*(\d{1,3})|(\d{1,3})\s*[x×])$", re.I)
_LABELLED = re.compile(
    r"(?is)^\s*(?:trading|offering|offer|giving|i\s+have)\s*[:\-]\s*(.+?)"
    r"\s*(?:\||;|\n)\s*(?:looking\s+for|lf|want|wants|seeking|for)\s*[:\-]\s*(.+?)\s*$"
)
_FOR = re.compile(r"(?is)^\s*(?:trading|giving|offering)\s+(.+?)\s+\bfor\b\s+(.+?)\s*$")


def _item(raw: str) -> dict[str, Any] | None:
    text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t-•")
    if not text:
        return None
    quantity = 1
    source = "implicit_single"
    match = _QTY_PREFIX.fullmatch(text)
    if match:
        quantity = int(match.group(1) or match.group(2))
        text = match.group(3).strip()
        source = "explicit_text"
    else:
        match = _QTY_SUFFIX.fullmatch(text)
        if match:
            quantity = int(match.group(2) or match.group(3))
            text = match.group(1).strip()
            source = "explicit_text"
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or len(text) > 80 or quantity < 1 or quantity > MAX_QUANTITY:
        return None
    if re.search(r"(?i)\b(?:selling|buying|price|robux|robux\s*\d|dm\s*me|offer\s*up)\b", text):
        return None
    return {
        "name": text,
        "quantity": quantity,
        "quantity_source": source,
        "quantity_min": quantity,
        "quantity_max": quantity,
        "quantity_reason": "",
        "listed_price": None,
        "raw_excerpt": str(raw)[:200],
    }


def _side(raw: str) -> list[dict[str, Any]] | None:
    parts = [part.strip() for part in _SPLIT_ITEMS.split(raw) if part.strip()]
    if not parts or len(parts) > 12:
        return None
    parsed = [_item(part) for part in parts]
    if any(item is None for item in parsed):
        return None
    return parsed  # type: ignore[return-value]


def parse_trade_text(text: str, *, message_id: str, author: str = "",
                     message_url: str = "", guild_id: str = "",
                     channel_id: str = "", created_at_utc: str = "") -> dict[str, Any] | None:
    """Parse one message into the scanner's receive/give listing schema."""
    body = str(text or "").strip()
    if not body or len(body) > 8000:
        return None
    # Strip Discord markdown wrappers without interpreting arbitrary prose.
    body = re.sub(r"<@!?\d+>", " ", body)
    body = re.sub(r"(?m)^\s*>\s?", "", body)
    match = _LABELLED.fullmatch(body) or _FOR.fullmatch(body)
    if not match:
        return None
    give = _side(match.group(1))
    receive = _side(match.group(2))
    if not give or not receive:
        return None
    def combine(items: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        grouped: dict[str, dict[str, Any]] = {}
        for item in items:
            key = item["name"].casefold()
            if key not in grouped:
                grouped[key] = dict(item)
                continue
            total = int(grouped[key]["quantity"]) + int(item["quantity"])
            if total > MAX_QUANTITY:
                return None
            grouped[key]["quantity"] = total
            grouped[key]["quantity_min"] = total
            grouped[key]["quantity_max"] = total
            if item["quantity_source"] == "explicit_text":
                grouped[key]["quantity_source"] = "explicit_text"
        return list(grouped.values())

    give = combine(give)
    receive = combine(receive)
    if not give or not receive:
        return None
    stable_message_id = str(message_id or "").strip()
    if not stable_message_id:
        return None
    source_ad_id = "discord:" + stable_message_id
    url = str(message_url or "").strip()
    if not url.startswith("https://discord.com/channels/"):
        url = ""
    identity = {
        "source": "discord",
        "message_id": stable_message_id,
        "guild_id": str(guild_id or ""),
        "channel_id": str(channel_id or ""),
    }
    listing_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return {
        "id": listing_id,
        "legacy_id": listing_id,
        "source": "discord",
        # Keep Discord IDs out of BloxSwap-only pending/watch cursors.
        "source_ad_id": "",
        "discord_message_id": stable_message_id,
        "receive_items": give,
        "give_items": receive,
        "url": url,
        "owner_url": url,
        "source_text": body[:700],
        "source_author": str(author or "")[:100],
        "source_guild_id": str(guild_id or ""),
        "source_channel_id": str(channel_id or ""),
        "posted_at": str(created_at_utc or ""),
    }


def parse_message_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    texts = [str(row.get("content") or "")]
    embeds = row.get("embeds") or []
    if isinstance(embeds, list):
        texts.extend(str(value) for value in embeds if value)
    return parse_trade_text(
        "\n".join(text for text in texts if text.strip()),
        message_id=str(row.get("message_id") or ""),
        author=str(row.get("author") or ""),
        message_url=str(row.get("url") or ""),
        guild_id=str(row.get("guild_id") or ""),
        channel_id=str(row.get("channel_id") or ""),
        created_at_utc=str(row.get("created_at_utc") or ""),
    )


def load_discord_listings(path: str | Path = "data/discord_messages.json") -> tuple[list[dict[str, Any]], int]:
    """Load, parse and deduplicate Discord messages; unreadable state fails closed."""
    source = Path(path)
    if not source.exists():
        return [], 0
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Discord message state is unreadable: {source}") from exc
    rows = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Discord message state has an invalid schema; expected messages array")
    listings: dict[str, dict[str, Any]] = {}
    rejected = 0
    for row in rows:
        listing = parse_message_row(row)
        if listing is None:
            rejected += 1
            continue
        listings[listing["id"]] = listing
    return list(listings.values()), rejected
