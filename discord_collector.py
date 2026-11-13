"""Read-only Discord collector for explicitly approved MM2 servers.

The bot discovers readable text channels only inside configured guild IDs. It
never sends messages, moderates, or uses a personal account token/self-bot.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_HISTORY_PER_CHANNEL = 100
MAX_STORED_MESSAGES = 10000


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


def normalize_message(message: Any) -> dict[str, Any]:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    attachments = [
        str(getattr(item, "url", ""))[:1000]
        for item in list(getattr(message, "attachments", []) or [])[:10]
        if getattr(item, "url", None)
    ]
    embeds = []
    for embed in list(getattr(message, "embeds", []) or [])[:5]:
        parts = [getattr(embed, "title", None), getattr(embed, "description", None)]
        embeds.append(" ".join(str(part) for part in parts if part)[:2000])
    created = getattr(message, "created_at", None)
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "source": "discord",
        "message_id": str(getattr(message, "id", "")),
        "guild_id": str(getattr(guild, "id", "")) if guild else "",
        "guild_name": str(getattr(guild, "name", ""))[:100] if guild else "",
        "channel_id": str(getattr(channel, "id", "")) if channel else "",
        "channel_name": str(getattr(channel, "name", ""))[:100] if channel else "",
        "author": str(getattr(author, "display_name", getattr(author, "name", "unknown")))[:100],
        "content": str(getattr(message, "content", "") or "")[:8000],
        "embeds": embeds,
        "attachments": attachments,
        "created_at_utc": created.isoformat() if created else "",
        "url": str(getattr(message, "jump_url", "") or "")[:1000],
    }


def merge_messages(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], retention_days: int = 7) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(retention_days), 30)))
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
            if parsed < cutoff:
                continue
        except (ValueError, TypeError):
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


def atomic_save(path: Path, messages: list[dict[str, Any]], guild_ids: list[int], scanned_channels: list[str]) -> None:
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
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


async def collect(guild_ids: list[int], output: Path, limit: int, retention_days: int) -> int:
    try:
        import discord
    except ImportError:
        print("discord.py is not installed; install discord.py==2.6.4", file=sys.stderr)
        return 2

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("Discord collection skipped: DISCORD_BOT_TOKEN is not configured.")
        return 0
    if not guild_ids:
        print("Discord collection skipped: DISCORD_GUILD_IDS is empty.")
        return 0

    intents = discord.Intents.none()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True
    client = discord.Client(intents=intents)
    state = load_existing(output)
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_channels: list[str] = []

    @client.event
    async def on_ready() -> None:
        for guild_id in guild_ids:
            guild = client.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await client.fetch_guild(guild_id)
                except discord.Forbidden:
                    errors.append(f"server {guild_id}: bot is not a member or lacks access")
                    continue
                except discord.HTTPException as exc:
                    errors.append(f"server {guild_id}: Discord HTTP {exc.status}")
                    continue

            me = guild.me
            channels = list(getattr(guild, "text_channels", []) or [])
            # Include active public/private threads only when their parent is
            # in this explicitly allowlisted server and the bot can read them.
            channels.extend(list(getattr(guild, "threads", []) or []))
            unique_channels = {int(channel.id): channel for channel in channels if hasattr(channel, "history")}

            for channel_id, channel in unique_channels.items():
                parent = getattr(channel, "parent", None)
                permission_target = parent or channel
                try:
                    permissions = permission_target.permissions_for(me)
                    if not permissions.view_channel or not permissions.read_message_history:
                        continue
                    async for message in channel.history(limit=limit, oldest_first=False):
                        if getattr(getattr(message, "author", None), "bot", False):
                            continue
                        row = normalize_message(message)
                        if row["content"].strip() or row["embeds"] or row["attachments"]:
                            collected.append(row)
                    scanned_channels.append(str(channel_id))
                except discord.Forbidden:
                    errors.append(f"channel {channel_id}: missing View Channel or Read Message History")
                except discord.HTTPException as exc:
                    errors.append(f"channel {channel_id}: Discord HTTP {exc.status}")
                except Exception as exc:
                    errors.append(f"channel {channel_id}: {type(exc).__name__}")

        if not scanned_channels:
            errors.append("No readable text channels found; check bot membership and channel permissions.")
        merged = merge_messages(state, collected, retention_days)
        atomic_save(output, merged, guild_ids, scanned_channels)
        print(f"Discord collection complete: {len(collected)} fetched, {len(merged)} retained, {len(scanned_channels)} channels scanned, {len(errors)} warnings.")
        for error in errors:
            print(f"Discord warning: {error}")
        await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        print("Discord collection failed: bot token rejected; verify DISCORD_BOT_TOKEN.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Discord collection failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 1 if errors and not collected else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect recent messages from configured MM2 Discord servers.")
    parser.add_argument("--output", default="data/discord_messages.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_HISTORY_PER_CHANNEL:
        parser.error("--limit must be between 1 and 100")
    try:
        guilds = parse_guild_ids(os.environ.get("DISCORD_GUILD_IDS", ""))
    except ValueError as exc:
        parser.error(str(exc))
    return asyncio.run(collect(guilds, Path(args.output), args.limit, args.retention_days))


if __name__ == "__main__":
    raise SystemExit(main())
