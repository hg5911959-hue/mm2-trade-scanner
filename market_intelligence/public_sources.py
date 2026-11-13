"""Public-feed ingestion without Discord bots or user-account tokens."""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html import unescape
from typing import Any

_ALLOWED_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com"}
_MAX_BYTES = 1_500_000
_MAX_ITEMS = 100
_TAG = re.compile(r"<[^>]+>")


def validate_reddit_feed_url(url: str) -> str:
    """Allow only HTTPS Reddit RSS feeds; reject Discord and arbitrary hosts."""
    parsed = urllib.parse.urlsplit(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError("Only HTTPS public Reddit RSS feeds are supported")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Credentials and non-standard ports are not allowed")
    if not parsed.path.lower().endswith(".rss"):
        raise ValueError("URL does not look like a Reddit RSS feed")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, wanted: str) -> str:
    for child in element.iter():
        if _name(child.tag) == wanted:
            href = child.attrib.get("href")
            if wanted == "link" and href:
                return href.strip()
            if child.text:
                return child.text.strip()
    return ""


def _clean(value: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", value or "")).split())[:2000]


def parse_rss(xml_text: str, source_url: str, limit: int = _MAX_ITEMS) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom into normalized public listing candidates."""
    root = ET.fromstring(xml_text)
    entries = [node for node in root.iter() if _name(node.tag) in {"item", "entry"}]
    records = []
    for entry in entries[:max(0, min(int(limit), _MAX_ITEMS))]:
        title = _child_text(entry, "title")
        link = _child_text(entry, "link")
        summary = _child_text(entry, "description") or _child_text(entry, "summary") or _child_text(entry, "content")
        published = _child_text(entry, "pubdate") or _child_text(entry, "published") or _child_text(entry, "updated")
        if not title or not link:
            continue
        parsed = urllib.parse.urlsplit(link)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            continue
        records.append({
            "source": "reddit_rss",
            "source_feed": source_url,
            "title": _clean(title),
            "text": _clean(summary),
            "url": link,
            "published_at": published,
        })
    return records


def fetch_reddit_feed(url: str, timeout: int = 12) -> list[dict[str, Any]]:
    """Fetch one public Reddit RSS feed with bounded size and timeout."""
    safe_url = validate_reddit_feed_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "MM2MarketIntelligence/1.0 (public RSS reader)",
                 "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(3, min(int(timeout), 20))) as response:
            final_url = validate_reddit_feed_url(response.geturl())
            body = response.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Reddit RSS returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Reddit RSS network error: {type(exc.reason).__name__}") from None
    if len(body) > _MAX_BYTES:
        raise RuntimeError("Reddit RSS response exceeded the 1.5 MB safety limit")
    try:
        xml_text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RuntimeError("Reddit RSS response was not valid UTF-8") from None
    return parse_rss(xml_text, final_url)


def collect_public_feeds(urls: list[str]) -> dict[str, Any]:
    """Collect up to ten feeds; partial failures are never reported as empty success."""
    items = []
    errors = []
    for url in urls[:10]:
        try:
            items.extend(fetch_reddit_feed(url))
        except (ValueError, RuntimeError, OSError, ET.ParseError, UnicodeDecodeError) as exc:
            errors.append({"feed": str(url)[:300], "error": str(exc)[:300]})
    unique = {item["url"]: item for item in items}
    ordered = sorted(unique.values(), key=lambda row: row.get("published_at", ""), reverse=True)
    return {
        "collected_at_utc": datetime.now(UTC).isoformat(),
        "source_count": min(len(urls), 10),
        "item_count": len(ordered),
        "items": ordered,
        "errors": errors,
        "complete": not errors,
    }
