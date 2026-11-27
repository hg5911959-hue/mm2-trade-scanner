"""RFC-compliant parsing for HTTP Retry-After headers."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(value, *, now=None):
    """Return a non-negative Retry-After delay, or None for an invalid header.

    Both delta-seconds and the HTTP-date form are supported. The optional
    reference time is injectable so date-form behavior is deterministic in tests.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        seconds = (retry_at.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)
