"""Bounded HTTP retry policy for BloxSwaps and credentialed RBLXValue requests."""
from __future__ import annotations

import time
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from tools.retry_after import retry_after_seconds


def request_with_retry(
    req, timeout, label, attempts=4, return_headers=False,
    allow_not_modified=False, *, rblxvalue_guard, rblxvalue_api_key,
    rblxvalue_max_response_bytes, open_rblxvalue_url, redact,
    max_retry_after_seconds, rate_limit_error,
):
    """Retry transient failures with exponential backoff and respect Retry-After."""
    request_url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    request_headers = {}
    request_headers.update(getattr(req, "headers", {}) or {})
    request_headers.update(getattr(req, "unredirected_hdrs", {}) or {})
    carries_api_key = any(str(name).casefold() == "x-api-key" for name in request_headers)
    is_rblxvalue = label == "RBLXValue API" or carries_api_key
    if is_rblxvalue:
        target = urlsplit(request_url)
        if (
            target.scheme != "https"
            or target.hostname != "api.rblxvalue.com"
            or target.port not in (None, 443)
        ):
            raise RuntimeError(
                "[rblxvalue] Refusing credential-bearing request to a non-allowlisted HTTPS origin"
            )
    for attempt in range(attempts):
        if is_rblxvalue:
            rblxvalue_guard.reserve_request(key=rblxvalue_api_key)
        try:
            response_context = (
                open_rblxvalue_url(req, timeout)
                if is_rblxvalue else urllib.request.urlopen(req, timeout=timeout)
            )
            with response_context as response:
                if is_rblxvalue:
                    body = response.read(rblxvalue_max_response_bytes + 1)
                    if len(body) > rblxvalue_max_response_bytes:
                        raise RuntimeError(
                            "[rblxvalue] Response exceeded the 8 MiB safety limit; refusing payload"
                        )
                else:
                    body = response.read()
                print(f"[http] {label}: HTTP {response.status} ({len(body)} bytes)")
                return (body, response.headers) if return_headers else body
        except HTTPError as exc:
            if exc.code == 304 and allow_not_modified:
                print(f"[http] {label}: HTTP 304 (ETag unchanged)")
                return None, exc.headers
            detail = exc.read(400).decode("utf-8", errors="replace")
            # Detect edge/CDN challenges before interpreting HTTP 403 as an API-key
            # suspension. Cloudflare can return 403 even when the configured key is valid.
            challenge_markers = ("cloudflare", "captcha", "just a moment", "challenge-platform", "cf-chl-")
            cf_mitigated = str(exc.headers.get("cf-mitigated", "")).casefold()
            if exc.code in (403, 503) and (
                any(marker in detail.casefold() for marker in challenge_markers)
                or cf_mitigated == "challenge"
            ):
                raise RuntimeError(f"[cloudflare] {label} blocked by Cloudflare/CAPTCHA challenge (HTTP {exc.code}).") from exc
            if is_rblxvalue:
                rblxvalue_guard.raise_for_key_error(exc.code, detail, key=rblxvalue_api_key)
            if exc.code == 403:
                safe_detail = redact(detail).strip()
                if safe_detail.startswith("<"):
                    safe_detail = "[HTML response omitted; inspect response headers in diagnostics]"
                if not safe_detail:
                    safe_detail = "empty response body"
                raise RuntimeError(
                    f"[http] {label}: HTTP 403 Forbidden; upstream response: {safe_detail[:240]}"
                ) from exc
            if exc.code == 429 and not is_rblxvalue:
                retry_after = (exc.headers or {}).get("Retry-After", "")
                delay = retry_after_seconds(retry_after)
                if delay is not None and delay > max_retry_after_seconds:
                    raise rate_limit_error(
                        f"{label}: HTTP 429; Retry-After exceeds the safe wait "
                        f"({int(delay)}s > {max_retry_after_seconds}s); aborting this scan without retry"
                    ) from exc
                if not retry_after:
                    suffix = "; no Retry-After header"
                elif delay is None:
                    suffix = "; invalid Retry-After header"
                else:
                    suffix = f"; Retry-After={int(delay)}s"
                headers = exc.headers or {}
                diagnostic_headers = []
                for header in (
                    "Server", "CF-Ray", "Content-Type", "X-RateLimit-Limit",
                    "X-RateLimit-Remaining", "X-RateLimit-Reset",
                ):
                    value = headers.get(header)
                    if value:
                        diagnostic_headers.append(f"{header}={str(value)[:100]}")
                header_summary = (
                    "; response=" + ", ".join(diagnostic_headers)
                    if diagnostic_headers else ""
                )
                raise rate_limit_error(
                    f"{label}: HTTP 429 rate limited{suffix}{header_summary}; "
                    "aborting this scan without retry"
                ) from exc
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == attempts - 1:
                safe_detail = redact(detail).strip()
                if safe_detail.startswith("<"):
                    safe_detail = "[HTML response omitted]"
                raise RuntimeError(f"{label}: HTTP {exc.code}: {safe_detail[:200]}") from exc
            delay = min(30, 2 ** (attempt + 1))
            retry_after = (exc.headers or {}).get("Retry-After", "")
            parsed_delay = retry_after_seconds(retry_after)
            if parsed_delay is not None:
                delay = parsed_delay
            if delay > max_retry_after_seconds:
                raise RuntimeError(
                    f"{label}: Retry-After exceeds the safe per-attempt wait "
                    f"({int(delay)}s > {max_retry_after_seconds}s); aborting this scan without retry"
                ) from exc
            delay = rblxvalue_guard.jittered_delay(delay)
            print(f"[retry] {label}: HTTP {exc.code}; waiting {delay:.2f}s")
            time.sleep(delay)
        except (URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"{label}: network failure after {attempts} attempts: {exc}") from exc
            delay = rblxvalue_guard.jittered_delay(min(30, 2 ** (attempt + 1)))
            print(f"[retry] {label}: network error; waiting {delay:.2f}s")
            time.sleep(delay)
