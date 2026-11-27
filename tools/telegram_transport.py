"""Shared Telegram transport that never forwards bot tokens across redirects."""
from __future__ import annotations

import urllib.request


class RejectTelegramRedirects(urllib.request.HTTPRedirectHandler):
    """Reject redirects because Telegram bot tokens are embedded in URL paths."""

    handler_order = 100

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


TELEGRAM_OPENER = urllib.request.build_opener(RejectTelegramRedirects())



def telegram_alert_delay(chat_type, configured_delay=3.2):
    """Apply Telegram's conservative per-chat pacing limits."""
    configured = max(0.0, float(configured_delay))
    if str(chat_type or "").strip().casefold() == "private":
        # Private chats allow roughly one message per second; retain a safety margin.
        return 1.05
    # Groups are limited to 20 messages per minute; retain the existing margin.
    return max(3.2, configured)
