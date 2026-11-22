"""Shared Telegram transport that never forwards bot tokens across redirects."""
from __future__ import annotations

import urllib.request


class RejectTelegramRedirects(urllib.request.HTTPRedirectHandler):
    """Reject redirects because Telegram bot tokens are embedded in URL paths."""

    handler_order = 100

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


TELEGRAM_OPENER = urllib.request.build_opener(RejectTelegramRedirects())
