"""Small, explicit state mutations used by one scanner cycle."""
from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence
from typing import Any


def mark_alerted_state(
    trade_id: str,
    *,
    alerted: list[str],
    alerted_set: set[str],
    save_alerted: Callable[[list[str]], None],
) -> None:
    if trade_id not in alerted_set:
        alerted.append(trade_id)
        alerted_set.add(trade_id)
        save_alerted(alerted)


def mark_pending_state(
    listing: dict[str, Any],
    *,
    pending_ids: dict[str, float],
    pending_schedule: dict[str, dict[str, Any]],
    seen_ads: set[str],
    save_seen_ads: Callable[[set[str]], None],
    save_pending_recheck_schedule: Callable[[dict[str, dict[str, Any]]], None],
    save_pending_ids: Callable[[dict[str, float]], None],
    priority_pending_reasons: Collection[str],
    pending_recheck_backoff_seconds: Sequence[float],
    reason: str = "legacy_unclassified",
    now: float | None = None,
) -> None:
    if listing.get("source") == "discord":
        return  # Discord messages are reloaded from their own bounded source file.
    source_id = str(listing.get("source_ad_id") or "")
    if not source_id:
        return
    timestamp = time.time() if now is None else now
    pending_ids.setdefault(source_id, timestamp)
    previous = pending_schedule.get(source_id, {})
    attempts = int(previous.get("attempts", 0))
    if reason in priority_pending_reasons:
        pending_schedule[source_id] = {
            "attempts": attempts,
            "next_check_at": timestamp,
            "reason": reason,
        }
        if source_id in seen_ads:
            seen_ads.discard(source_id)
            save_seen_ads(seen_ads)
    else:
        next_check = previous.get("next_check_at")
        if previous.get("reason") != reason or not next_check:
            next_check = timestamp + pending_recheck_backoff_seconds[0]
        pending_schedule[source_id] = {
            "attempts": attempts,
            "next_check_at": float(next_check),
            "reason": reason,
        }
    save_pending_recheck_schedule(pending_schedule)
    save_pending_ids(pending_ids)


def mark_seen_ad_state(
    listing: dict[str, Any],
    *,
    seen_ads: set[str],
    pending_ids: dict[str, float],
    pending_schedule: dict[str, dict[str, Any]],
    save_seen_ads: Callable[[set[str]], None],
    save_pending_ids: Callable[[dict[str, float]], None],
    save_pending_recheck_schedule: Callable[[dict[str, dict[str, Any]]], None],
    clear_pending: bool = True,
) -> None:
    if listing.get("source") == "discord":
        return  # Never add Discord snowflakes to BloxSwaps seen/pending cursors.
    source_id = str(listing.get("source_ad_id") or "")
    if source_id and source_id not in seen_ads:
        seen_ads.add(source_id)
        save_seen_ads(seen_ads)
    if clear_pending and source_id and source_id in pending_ids:
        pending_ids.pop(source_id, None)
        pending_schedule.pop(source_id, None)
        save_pending_ids(pending_ids)
        save_pending_recheck_schedule(pending_schedule)
