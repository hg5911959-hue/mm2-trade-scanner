"""Read-only Discord collector using the official REST API and Python stdlib.

Only GET requests are made, only configured guild IDs are visited, and the
existing Discord bot token is used (never a personal user token/self-bot).
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_BASE = "https://discord.com/api/v10"
MAX_HISTORY_PER_CHANNEL = 100
MAX_HISTORY_PAGES_PER_CHANNEL = 6
MAX_CHANNELS_PER_GUILD = 100
MAX_THREADS_PER_GUILD = 100
MAX_STORED_MESSAGES = 10000
MAX_RESPONSE_BYTES = 2_000_000
MAX_RATE_LIMIT_WAIT_SECONDS = 10.0


def parse_guild_ids(raw: str) -> list[int]:
    """Parse a comma/semicolon-separated allowlist of Discord server IDs."""
    ids: list[int] = []
    for token in str(raw or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit() or not 15 <= len(token) <= 22:
            raise ValueError("Each Discord server ID must be a 15–22 digit snowflake")
        value = int(token)
        if value not in ids:
            ids.append(value)
    return ids


def parse_channel_ids(raw: str) -> list[str]:
    """Parse a bounded allowlist of exact Discord channel IDs."""
    ids: list[str] = []
    for token in str(raw or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit() or not 15 <= len(token) <= 22:
            raise ValueError("Each Discord channel ID must be a 15–22 digit snowflake")
        if token not in ids:
            ids.append(token)
    if len(ids) > 10:
        raise ValueError("At most 10 Discord channel IDs may be configured")
    return ids


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _iso_timestamp(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


class _RejectDiscordRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the bot Authorization header to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "Discord API redirect refused", headers, fp
        )


def _discord_get_json(
    path: str, token: str, params: dict[str, Any] | None = None, timeout: int = 20,
) -> Any:
    """Make a bounded, read-only Discord REST request with limited 429 retries."""
    if not path.startswith("/") or ".." in path:
        raise ValueError("Invalid Discord API path")
    query = urllib.parse.urlencode(params or {})
    url = API_BASE + path + (("?" + query) if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bot " + token,
            "Accept": "application/json",
            "User-Agent": "MM2MarketIntelligence/1.0 (read-only bot API client)",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_RejectDiscordRedirects())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=max(3, min(int(timeout), 30))) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Discord API response exceeded the safety limit")
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RuntimeError("Discord API returned invalid JSON") from None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                try:
                    payload = json.loads(exc.read(65536).decode("utf-8"))
                    delay = float(payload.get("retry_after", 1.0))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    try:
                        delay = float(exc.headers.get("Retry-After", "1"))
                    except (AttributeError, TypeError, ValueError):
                        delay = 1.0
                if not 0 <= delay <= MAX_RATE_LIMIT_WAIT_SECONDS:
                    raise RuntimeError("Discord rate limit wait exceeds the bounded retry window") from None
                time.sleep(max(0.25, delay))
                continue
            if exc.code == 403:
                raise PermissionError("Discord bot lacks permission to read this resource") from None
            if exc.code == 404:
                return None
            if exc.code == 401:
                raise RuntimeError("Discord bot token was rejected") from None
            raise RuntimeError(f"Discord API returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Discord API network error: {type(exc.reason).__name__}"
            ) from None
    raise RuntimeError("Discord API rate limit retry budget exhausted")


def _fetch_channel_history(channel_id: str, token: str, limit: int):
    """Fetch a bounded sequence of newest messages, paging with Discord's before cursor."""
    page_limit = max(1, min(int(limit), MAX_HISTORY_PER_CHANNEL))
    collected: list[dict[str, Any]] = []
    before = None
    for _ in range(MAX_HISTORY_PAGES_PER_CHANNEL):
        params: dict[str, Any] = {"limit": page_limit}
        if before is not None:
            params["before"] = before
        page = _discord_get_json(f"/channels/{channel_id}/messages", token, params=params)
        if not isinstance(page, list):
            raise RuntimeError("Discord API returned an invalid channel history page")
        if not page:
            return collected, False
        collected.extend(row for row in page if isinstance(row, dict))
        if len(page) < page_limit:
            return collected, False
        next_before = str(page[-1].get("id") or "") if isinstance(page[-1], dict) else ""
        if not next_before or next_before == before:
            raise RuntimeError("Discord message history pagination did not advance")
        before = next_before
    return collected, True


def normalize_message(
    message: Any, guild_id: int | str | None = None, channel: Any = None,
) -> dict[str, Any]:
    guild = _get(message, "guild")
    channel_obj = _get(message, "channel") or channel
    author = _get(message, "author", {}) or {}
    message_id = str(_get(message, "id", "") or "")
    actual_guild_id = str(
        _get(guild, "id", "") or _get(message, "guild_id", "") or guild_id or ""
    )
    channel_id = str(
        _get(channel_obj, "id", "") or _get(message, "channel_id", "") or ""
    )
    attachments = [
        str(_get(item, "url", ""))[:1000]
        for item in list(_get(message, "attachments", []) or [])[:10]
        if _get(item, "url", None)
    ]
    embeds = []
    for embed in list(_get(message, "embeds", []) or [])[:5]:
        parts = [_get(embed, "title"), _get(embed, "description")]
        for field in list(_get(embed, "fields", []) or [])[:5]:
            parts.extend([_get(field, "name"), _get(field, "value")])
        embeds.append(" ".join(str(part) for part in parts if part)[:2000])
    author_name = (
        _get(author, "global_name") or _get(author, "display_name")
        or _get(author, "username") or _get(author, "name") or "unknown"
    )
    created = _get(message, "timestamp") or _get(message, "created_at")
    url = _get(message, "jump_url") or (
        f"https://discord.com/channels/{actual_guild_id}/{channel_id}/{message_id}"
        if actual_guild_id and channel_id and message_id else ""
    )
    return {
        "source": "discord",
        "message_id": message_id,
        "guild_id": actual_guild_id,
        "guild_name": str(_get(guild, "name", "") or guild_id or "")[:100],
        "channel_id": channel_id,
        "channel_name": str(_get(channel_obj, "name", "") or "")[:100],
        "author": str(author_name)[:100],
        "content": str(_get(message, "content", "") or "")[:8000],
        "embeds": embeds,
        "attachments": attachments,
        "created_at_utc": _iso_timestamp(created),
        "url": str(url or "")[:1000],
    }


def merge_messages(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]],
    retention_days: int = 7,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, min(int(retention_days), 30)) * 86400
    by_id: dict[str, dict[str, Any]] = {}
    for row in list(existing) + list(incoming):
        message_id = str(row.get("message_id", "")).strip()
        if not message_id:
            continue
        timestamp = str(row.get("created_at_utc", ""))
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed.timestamp() < cutoff:
                continue
        except (ValueError, TypeError, OverflowError):
            pass
        by_id[message_id] = row
    return sorted(
        by_id.values(),
        key=lambda row: str(row.get("created_at_utc", "")),
        reverse=True,
    )[:MAX_STORED_MESSAGES]


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("messages", []) if isinstance(payload, dict) else []
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        raise RuntimeError(f"Existing Discord state is unreadable: {path}") from None


def atomic_save(
    path: Path, messages: list[dict[str, Any]], guild_ids: list[int],
    scanned_channels: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "source": "discord",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "configured_guild_ids": [str(value) for value in guild_ids],
        "scanned_channel_ids": sorted(set(scanned_channels)),
        "message_count": len(messages),
        "messages": messages,
    }
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collect(
    guild_ids: list[int], output: Path, limit: int, retention_days: int,
    channel_ids: list[str] | None = None,
) -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("Discord collection skipped: DISCORD_BOT_TOKEN is not configured.")
        return 0
    if not guild_ids:
        print("Discord collection skipped: DISCORD_GUILD_IDS is empty.")
        return 0
    if not 1 <= limit <= MAX_HISTORY_PER_CHANNEL:
        raise ValueError("message limit must be between 1 and 100")

    existing = load_existing(output)
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_channels: list[str] = []
    found_channel_ids: set[str] = set()
    for guild_id in guild_ids:
        try:
            channels = _discord_get_json(f"/guilds/{guild_id}/channels", token)
        except (PermissionError, RuntimeError, ValueError) as exc:
            errors.append(f"server {guild_id}: {str(exc)[:160]}")
            continue
        if not isinstance(channels, list):
            errors.append(f"server {guild_id}: invalid channel-list response")
            continue
        supported_channels = [
            row for row in channels
            if isinstance(row, dict) and row.get("type") in {0, 5}
        ]
        if len(supported_channels) > MAX_CHANNELS_PER_GUILD:
            errors.append(f"server {guild_id}: readable channel limit exceeded")
        guild_channels = supported_channels[:MAX_CHANNELS_PER_GUILD]
        try:
            thread_payload = _discord_get_json(f"/guilds/{guild_id}/threads/active", token)
            threads = thread_payload.get("threads", []) if isinstance(thread_payload, dict) else []
        except (PermissionError, RuntimeError, ValueError) as exc:
            errors.append(f"server {guild_id} active threads: {str(exc)[:160]}")
            threads = []
        supported_threads = [
            row for row in threads
            if isinstance(row, dict) and row.get("type") in {10, 11, 12}
        ]
        if len(supported_threads) >= MAX_THREADS_PER_GUILD:
            errors.append(f"server {guild_id}: active thread limit reached; coverage may be incomplete")
        thread_channels = supported_threads[:MAX_THREADS_PER_GUILD]
        available_channels = guild_channels + thread_channels
        if channel_ids:
            available_ids = {
                str(row.get("id") or "") for row in available_channels
                if isinstance(row, dict)
            }
            found_channel_ids.update(available_ids.intersection(channel_ids))
            selected_channels = [
                row for row in available_channels
                if str(row.get("id") or "") in set(channel_ids)
            ]
        else:
            selected_channels = available_channels
        seen_channel_ids: set[str] = set()
        for channel in selected_channels:
            channel_id = str(channel.get("id") or "")
            if not channel_id or channel_id in seen_channel_ids:
                continue
            seen_channel_ids.add(channel_id)
            try:
                messages, history_truncated = _fetch_channel_history(channel_id, token, limit)
                if history_truncated:
                    errors.append(f"channel {channel_id}: bounded history page limit reached")
            except (PermissionError, RuntimeError, ValueError) as exc:
                errors.append(f"channel {channel_id}: {str(exc)[:160]}")
                continue
            if not isinstance(messages, list):
                errors.append(f"channel {channel_id}: invalid message-list response")
                continue
            scanned_channels.append(channel_id)
            human_message_count = 0
            readable_message_count = 0
            for message in messages:
                if not isinstance(message, dict):
                    continue
                author = message.get("author") or {}
                if isinstance(author, dict) and author.get("bot", False):
                    continue
                human_message_count += 1
                row = normalize_message(message, guild_id, channel)
                if row["content"].strip() or row["embeds"] or row["attachments"]:
                    collected.append(row)
                    readable_message_count += 1
            if human_message_count and not readable_message_count:
                errors.append(
                    f"channel {channel_id}: messages returned without readable content; "
                    "enable the MESSAGE_CONTENT privileged intent for the bot"
                )

    for missing_id in sorted(set(channel_ids or []) - found_channel_ids):
        errors.append(
            f"configured channel {missing_id}: not visible in the configured servers; "
            "check channel ID, server membership, and VIEW_CHANNEL permission"
        )
    if not scanned_channels:
        errors.append("No readable text channels found; check bot membership and permissions.")
    merged = merge_messages(existing, collected, retention_days)
    atomic_save(output, merged, guild_ids, scanned_channels)
    print(
        f"Discord collection complete: {len(collected)} fetched, {len(merged)} retained, "
        f"{len(scanned_channels)} channels scanned, {len(errors)} warnings."
    )
    for error in errors:
        print(f"Discord warning: {error}")
    # Partial collection must never report healthy: missing channel permissions,
    # rate limits, or API errors can hide offers even when some messages arrived.
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect recent messages from configured MM2 Discord servers using read-only REST GETs."
    )
    parser.add_argument("--output", default="data/discord_messages.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_HISTORY_PER_CHANNEL:
        parser.error("--limit must be between 1 and 100")
    try:
        guilds = parse_guild_ids(os.environ.get("DISCORD_GUILD_IDS", ""))
        channels = parse_channel_ids(os.environ.get("DISCORD_CHANNEL_IDS", ""))
    except ValueError as exc:
        parser.error(str(exc))
    return collect(
        guilds, Path(args.output), args.limit, args.retention_days,
        channel_ids=channels or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
