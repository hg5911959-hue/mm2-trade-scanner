"""Telegram message formatting boundaries and conservative delivery transport."""
from __future__ import annotations

import html as html_lib
import json
import time
from time import monotonic as monotonic_time
import urllib.request
from urllib.error import HTTPError, URLError


class TelegramDeliveryUncertain(RuntimeError):
    """Telegram may have accepted a message but the response was not received."""


class TelegramHTML(str):
    """Trusted, escaped Telegram HTML. Plain text is never interpreted as markup."""

    def __add__(self, other):
        suffix = str(other) if isinstance(other, TelegramHTML) else html_lib.escape(str(other), quote=False)
        return TelegramHTML(str(self) + suffix)

    def __radd__(self, other):
        prefix = str(other) if isinstance(other, TelegramHTML) else html_lib.escape(str(other), quote=False)
        return TelegramHTML(prefix + str(self))


def send_telegram(
    message, *, robase_attribution, rblxvalue_attribution, max_chars,
    bot_token, chat_id, opener, wait_for_rate_limit, send_part,
):
    is_html = isinstance(message, TelegramHTML)
    message = str(message)
    is_robase_cash_message = "CASH PROFIT" in message or "ROBASE PRICE" in message or "MM2 ROBASE" in message
    required_attributions = (
        [robase_attribution] if is_robase_cash_message else [rblxvalue_attribution]
    )
    existing_lines = set(message.rstrip().splitlines())
    missing_attributions = [
        value for value in required_attributions if value not in existing_lines
    ]
    footer = "\n\n" + "\n".join(missing_attributions) if missing_attributions else ""
    if len(message) + len(footer) > max_chars:
        chunk_limit = max_chars - 96
        chunks, current = [], ""
        for line in message.splitlines():
            candidate = (current + "\n" + line) if current else line
            if len(candidate) <= chunk_limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(line) > chunk_limit and is_html:
                raise RuntimeError("[telegram] A single formatted HTML line exceeds the safe chunk size.")
            while len(line) > chunk_limit:
                chunks.append(line[:chunk_limit])
                line = line[chunk_limit:]
            current = line
        if current:
            chunks.append(current)
        total_chunks = len(chunks)
        sent_chunks = 0
        for index, chunk in enumerate(chunks, start=1):
            try:
                header = f"💎 MM2 TRADE DETAILS ({index}/{total_chunks})\n"
                part_text = header + chunk + footer
                part = TelegramHTML(part_text) if is_html else part_text
                send_part(part)
            except TelegramDeliveryUncertain:
                raise
            except Exception as exc:
                if sent_chunks:
                    raise TelegramDeliveryUncertain(
                        "[telegram] Multipart alert was only partially delivered; "
                        "resend is quarantined to avoid duplicate earlier parts."
                    ) from exc
                raise
            sent_chunks += 1
        return
    message += footer
    url = "https://api.telegram.org/bot" + bot_token + "/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if is_html:
        payload["parse_mode"] = "HTML"
    body = json.dumps(payload).encode()
    # sendMessage is non-idempotent. Never retry uncertain network/5xx outcomes.
    for attempt in range(2):
        wait_for_rate_limit()
        request_started = monotonic_time()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read(2000)
            if exc.code == 429 and attempt < 1:
                fallback_delay = 2 ** (attempt + 1)
                try:
                    result = json.loads(raw.decode("utf-8"))
                    parameters = result.get("parameters") if isinstance(result, dict) else None
                    retry_after = (
                        parameters.get("retry_after", fallback_delay)
                        if isinstance(parameters, dict) else fallback_delay
                    )
                    retry_after = int(retry_after)
                except (TypeError, ValueError, OverflowError, UnicodeDecodeError):
                    retry_after = fallback_delay
                delay = min(180, max(1, retry_after + 2))
                print(f"[retry] Telegram explicitly rejected sendMessage with 429; waiting {delay}s")
                time.sleep(delay)
                continue
            if exc.code >= 500:
                raise TelegramDeliveryUncertain(
                    f"[telegram] HTTP {exc.code}; delivery status is uncertain, not retrying."
                ) from exc
            raise RuntimeError(
                f"[telegram] API rejected message: HTTP {exc.code}: "
                f"{raw.decode('utf-8', errors='replace')[:300]}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TelegramDeliveryUncertain(
                "[telegram] Network response missing; delivery status is uncertain, not retrying."
            ) from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TelegramDeliveryUncertain(
                "[telegram] Invalid response; delivery status is uncertain, not retrying."
            ) from exc
        if not isinstance(result, dict):
            raise TelegramDeliveryUncertain(
                "[telegram] Response JSON was not an object; delivery status is uncertain, not retrying."
            )
        if "ok" not in result or not isinstance(result.get("ok"), bool):
            raise TelegramDeliveryUncertain(
                "[telegram] Response omitted a valid ok flag; delivery status is uncertain, not retrying."
            )
        if result["ok"] is True:
            message_result = result.get("result") if isinstance(result.get("result"), dict) else {}
            message_id = message_result.get("message_id")
            request_ms = max(0.0, (monotonic_time() - request_started) * 1000.0)
            if message_id is not None:
                print(f"[telegram] Bot API accepted message; message_id={message_id}")
            else:
                print("[telegram] Bot API accepted message; response omitted message_id")
            print(f"[telegram-timing] confirmed_request_ms={request_ms:.1f}")
            return
        if result.get("error_code") == 429 and attempt < 1:
            parameters = result.get("parameters")
            fallback_delay = 2 ** (attempt + 1)
            retry_after = (
                parameters.get("retry_after", fallback_delay)
                if isinstance(parameters, dict) else fallback_delay
            )
            try:
                retry_after = int(retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_after = fallback_delay
            delay = min(180, max(1, retry_after + 2))
            print(f"[retry] Telegram explicitly rejected sendMessage with 429; waiting {delay}s")
            time.sleep(delay)
            continue
        raise RuntimeError(f"[telegram] API rejected message: {result.get('description', 'unknown error')}")
    raise RuntimeError("[telegram] Rate limit persisted after explicit 429 responses")
