"""Pure timestamp parsing and recovery policy for feed checkpoints."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone


def parse_feed_timestamp(raw):
    """Parse an ISO-8601 feed timestamp into a UTC Unix timestamp.

    Naive timestamps are treated as UTC because the upstream feed's timestamp
    contract is UTC. Invalid or missing values return None rather than being
    guessed or used as pagination boundaries.
    """
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def scan_checkpoint_requires_cold_start(
    last_success_ts, now=None, *, max_success_gap_seconds=24 * 60 * 60
):
    """Require bounded cold-start discovery when a checkpoint is unsafe."""
    if last_success_ts is None:
        return True
    current = time.time() if now is None else float(now)
    try:
        last_success = float(last_success_ts)
    except (TypeError, ValueError, OverflowError):
        return True
    if not math.isfinite(last_success) or not math.isfinite(current) or last_success <= 0:
        return True
    if last_success > current + 300:
        return True
    return current - last_success > float(max_success_gap_seconds)
