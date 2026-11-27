"""Secret-safe logging helpers used by scanner diagnostics."""
from __future__ import annotations

import json
import os
import re
import sys


def redact(text, *, secrets=(), cash_filter=False, environ=None):
    """Remove configured credentials and known credential formats from text."""
    env = os.environ if environ is None else environ
    safe = str(text)
    configured = {
        str(env.get(name, "")).strip()
        for name in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "RBLXVALUE_API_KEY",
            "DISCORD_BOT_TOKEN",
            "DISCORD_MANUAL_MESSAGES_JSON",
            "DISCORD_MANUAL_MESSAGES_JSON_2",
        )
    }
    configured.update(str(value).strip() for value in secrets if value)
    if cash_filter:
        configured.add(str(env.get("CASH_PRICES_CSV", "")).strip())
    # CASH_PRICES_CSV is intentionally not read while value mode is active.
    configured.discard("")
    # Also redact individual text fields from manual-import JSON in case a
    # diagnostic accidentally emits a parsed field rather than the full secret.
    for env_name in ("DISCORD_MANUAL_MESSAGES_JSON", "DISCORD_MANUAL_MESSAGES_JSON_2"):
        try:
            payload = json.loads(env.get(env_name, "") or "{}")
            stack = [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, str) and len(item) >= 6:
                    configured.add(item)
        except (ValueError, TypeError):
            pass
    for secret in sorted((value for value in configured if value and len(value) >= 6), key=len, reverse=True):
        safe = safe.replace(secret, "[REDACTED]")
    safe = re.sub(
        r"https://api\.telegram\.org/bot[^/\s]+",
        "https://api.telegram.org/bot[REDACTED]",
        safe,
    )
    safe = re.sub(r"rbx_[A-Za-z0-9_-]+", "rbx_[REDACTED]", safe)
    safe = re.sub(
        r"(?i)(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})",
        "[REDACTED_DISCORD_TOKEN]",
        safe,
    )
    safe = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1[REDACTED]", safe)
    return safe


class RedactingStream:
    """Line-buffered stream that redacts secrets before writing logs."""

    def __init__(self, stream, redactor):
        self._stream = stream
        self._redactor = redactor
        self._buffer = ""

    def write(self, value):
        value = str(value)
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._stream.write(self._redactor(line) + "\n")
        return len(value)

    def flush(self):
        if self._buffer:
            self._stream.write(self._redactor(self._buffer))
            self._buffer = ""
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_redacting_streams(stream_factory):
    """Install secret-safe stdout/stderr filters once, before scanner output."""
    if not isinstance(sys.stdout, stream_factory):
        sys.stdout = stream_factory(sys.stdout)
    if not isinstance(sys.stderr, stream_factory):
        sys.stderr = stream_factory(sys.stderr)
