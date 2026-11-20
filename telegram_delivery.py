"""Telegram message delivery with explicit uncertain-delivery semantics.

This module owns transport mechanics only. The scanner supplies its current
configuration and clock/network callables so existing monkeypatch-based tests
and runtime settings remain compatible during the extraction.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from typing import Any, Callable


class TelegramDeliveryUncertain(RuntimeError):
    """Telegram may have accepted a message but the response was not received."""


def wait_for_rate_limit(
    last_send_at: float,
    delay_seconds: float,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], Any],
) -> float:
    """Wait for the configured minimum spacing and return the new send time."""
    now = monotonic()
    wait = delay_seconds - (now - last_send_at)
    if wait > 0:
        sleep(wait)
    return monotonic()


def send_telegram(
    message: str,
    *,
    bot_token: str,
    chat_id: str,
    max_chars: int,
    attribution: str,
    wait_for_rate_limit: Callable[[], Any],
    request_factory: Callable[..., Any],
    urlopen: Callable[..., Any],
    sleep: Callable[[float], Any],
) -> None:
    """Send one message or safe multipart details; never retry uncertain sends."""
    def send_one(part: str) -> None:
        part = str(part)
        needs_attribution = not part.rstrip().endswith(attribution)
        footer = "\n\n" + attribution if needs_attribution else ""
        if len(part) + len(footer) > max_chars:
            # Reserve room for the multipart header and mandatory source credit.
            chunk_limit = max_chars - 96
            chunks: list[str] = []
            current = ""
            for line in part.splitlines():
                candidate = (current + "\n" + line) if current else line
                if len(candidate) <= chunk_limit:
                    current = candidate
                    continue
                if current:
                    chunks.append(current)
                    current = ""
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
                    send_one(f"💎 MM2 TRADE DETAILS ({index}/{total_chunks})\n{chunk}")
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

        part += footer
        url = "https://api.telegram.org/bot" + bot_token + "/sendMessage"
        body = json.dumps({
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        }).encode()

        # sendMessage is non-idempotent. Retry only an explicit HTTP/API 429.
        for attempt in range(2):
            wait_for_rate_limit()
            request = request_factory(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urlopen(request, timeout=20) as response:
                    raw = response.read()
            except HTTPError as exc:
                raw = exc.read(2000)
                if exc.code == 429 and attempt < 1:
                    try:
                        result = json.loads(raw.decode("utf-8"))
                        retry_after = result.get("parameters", {}).get(
                            "retry_after", 2 ** (attempt + 1)
                        )
                    except (ValueError, UnicodeDecodeError):
                        retry_after = 2 ** (attempt + 1)
                    delay = min(60, max(1, int(retry_after)))
                    print(
                        f"[retry] Telegram explicitly rejected sendMessage with 429; "
                        f"waiting {delay}s"
                    )
                    sleep(delay)
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
            if result.get("ok"):
                return
            if int(result.get("error_code", 0) or 0) == 429 and attempt < 1:
                retry_after = result.get("parameters", {}).get(
                    "retry_after", 2 ** (attempt + 1)
                )
                delay = min(60, max(1, int(retry_after)))
                print(
                    f"[retry] Telegram explicitly rejected sendMessage with 429; "
                    f"waiting {delay}s"
                )
                sleep(delay)
                continue
            raise RuntimeError(
                f"[telegram] API rejected message: {result.get('description', 'unknown error')}"
            )
        raise RuntimeError("[telegram] Rate limit persisted after explicit 429 responses")

    send_one(str(message))
