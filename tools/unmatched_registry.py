"""Retention policy for the diagnostic unmatched-item registry."""

from datetime import datetime, timedelta


def prune_stale_unmatched_names(
    state: dict[str, object],
    *,
    now: datetime,
    retention_days: int = 14,
) -> tuple[dict[str, object], list[str]]:
    """Drop only entries with a valid last-seen timestamp older than retention.

    Malformed, missing, or timezone-naive timestamps are retained conservatively.
    This registry is diagnostic only; pruning it never changes offer eligibility.
    """
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    cutoff = now - timedelta(days=retention_days)
    retained = dict(state)
    removed: list[str] = []
    for key, record in state.items():
        if not isinstance(record, dict):
            continue
        raw_last_seen = record.get("last_seen_at")
        if not isinstance(raw_last_seen, str) or not raw_last_seen.strip():
            continue
        try:
            last_seen = datetime.fromisoformat(raw_last_seen.replace("Z", "+00:00"))
        except ValueError:
            continue
        if last_seen.tzinfo is None or last_seen.utcoffset() is None:
            continue
        if last_seen < cutoff:
            retained.pop(key, None)
            removed.append(key)
    return retained, sorted(removed)
