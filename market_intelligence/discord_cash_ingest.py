"""Fail-closed adapter from normalized Discord messages to cash-market offers.

This module performs no network access and never treats demand hints as bids.
Only collector-shaped Discord records with matching source IDs, timestamps,
and permalinks are accepted. Unverified author labels remain non-actionable.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from cash_offer_parser import parse_cash_post

MAX_MESSAGES = 10_000
MAX_BODY_LENGTH = 20_000
SNOWFLAKE_RE = re.compile(r"^\d{15,22}$")


def _snowflake(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if SNOWFLAKE_RE.fullmatch(candidate) else ""


def _allowlist(value: Any, label: str) -> set[str]:
    if not isinstance(value, list):
        raise ValueError(f"Discord collector {label} allowlist has an invalid schema")
    values = [_snowflake(item) for item in value]
    if not values or any(not item for item in values) or len(values) != len(set(values)):
        raise ValueError(f"Discord collector {label} allowlist is empty, duplicated, or invalid")
    return set(values)


def _posted_at(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(UTC).isoformat()


def _valid_permalink(value: Any, guild_id: str, channel_id: str, message_id: str) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 1000:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    expected_path = f"/channels/{guild_id}/{channel_id}/{message_id}"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "discord.com"
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    return raw


def _message_body(row: dict[str, Any]) -> str:
    parts = [str(row.get("content") or "")]
    embeds = row.get("embeds") or []
    if not isinstance(embeds, list):
        return ""
    parts.extend(str(value) for value in embeds[:5] if value)
    body = "\n".join(parts).strip()
    return body if len(body) <= MAX_BODY_LENGTH else ""


def _add_flag(row: dict[str, Any], flag: str) -> None:
    flags = row.get("flags")
    if not isinstance(flags, list):
        flags = []
        row["flags"] = flags
    if flag not in flags:
        flags.append(flag)


def parse_discord_messages(payload: Any) -> dict[str, Any]:
    """Parse a collector JSON payload into offers, demand hints, and safe stats."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("Discord collector payload has an invalid schema")
    messages = payload["messages"]
    if len(messages) > MAX_MESSAGES:
        raise ValueError("Discord collector payload exceeds the message limit")
    allowed_guilds = _allowlist(payload.get("configured_guild_ids"), "guild")
    allowed_channels = _allowlist(payload.get("configured_channel_ids"), "channel")
    scanned_channels = payload.get("scanned_channel_ids")
    if not isinstance(scanned_channels, list):
        raise ValueError("Discord collector scanned-channel metadata has an invalid schema")
    scanned = {_snowflake(value) for value in scanned_channels}
    if any(not value for value in scanned) or not scanned.issubset(allowed_channels):
        raise ValueError("Discord collector scanned channels exceed the configured allowlist")

    offers: list[dict[str, Any]] = []
    demand_hints: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    duplicates = 0

    for index, row in enumerate(messages):
        if not isinstance(row, dict):
            rejected.append({"message": str(index), "reason": "invalid_message_record"})
            continue
        message_id = _snowflake(row.get("message_id"))
        guild_id = _snowflake(row.get("guild_id"))
        channel_id = _snowflake(row.get("channel_id"))
        if (
            row.get("source") != "discord" or not message_id or not guild_id or not channel_id
            or guild_id not in allowed_guilds
            or channel_id not in allowed_channels
            or channel_id not in scanned
        ):
            rejected.append({"message": message_id or str(index), "reason": "invalid_discord_source_identity"})
            continue
        if message_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(message_id)

        posted_at = _posted_at(row.get("created_at_utc"))
        if not posted_at:
            rejected.append({"message": message_id, "reason": "invalid_or_missing_timestamp"})
            continue
        url = _valid_permalink(row.get("url"), guild_id, channel_id, message_id)
        if not url:
            rejected.append({"message": message_id, "reason": "discord_permalink_mismatch"})
            continue
        body = _message_body(row)
        if not body:
            rejected.append({"message": message_id, "reason": "empty_or_oversized_message"})
            continue

        author = str(row.get("author") or "")[:100]
        author_id = _snowflake(row.get("author_id"))
        author_id_source = (
            "discord_api"
            if row.get("author_id_source") == "discord_api" and author_id
            else ""
        )
        parsed = parse_cash_post(
            body,
            author=author,
            author_id=author_id,
            author_id_source=author_id_source,
            message_id=f"discord:{message_id}",
            url=url,
            posted_at=posted_at,
        )
        rejected.extend(
            {"message": message_id, "reason": str(item.get("reason") or "parse_rejected")}
            for item in parsed.get("rejected", [])
        )
        source_fields = {
            "source_message_id": message_id,
            "source_channel_id": channel_id,
            "source_guild_id": guild_id,
            "source_channel_name": str(row.get("channel_name") or "")[:100],
            "source_type": "discord_channel",
        }
        for offer in parsed.get("offers", []):
            offer.update(source_fields)
            if not author_id_source:
                _add_flag(offer, "owner_supplied_author" if author else "missing_author_identity")
            offers.append(offer)
        for hint in parsed.get("demand_hints", []):
            hint.update(source_fields)
            if not author_id_source:
                _add_flag(hint, "owner_supplied_author" if author else "missing_author_identity")
            demand_hints.append(hint)

    return {
        "offers": offers,
        "demand_hints": demand_hints,
        "rejected": rejected,
        "stats": {
            "messages_received": len(messages),
            "messages_seen": len(seen_ids),
            "duplicate_messages": duplicates,
            "offers_parsed": len(offers),
            "demand_hints": len(demand_hints),
            "rejected_records": len(rejected),
        },
    }
