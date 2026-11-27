"""Collect every public ROBASE MM2 item price with conditional, bounded sync.

The live cash price is exclusively chromas.gg Price (7d), as reported in
ROBASE's marketplace breakdown.
No extra resale haircut is applied by the scanner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from tools.retry_after import retry_after_seconds

URL = "https://robase.gg/"
DEFAULT_OUTPUT = Path("data/robase_prices.json")

# Exact ROBASE page-slug -> BloxSwaps item-slug bridges for 2026 variants
# whose ROBASE detail pages omit the BloxSwaps OG image. Keys are page slugs,
# not display names, because several names also exist as Vintage weapons.
ROBASE_BLOXSWAPS_SLUG_OVERRIDES = {
    "cowboy-1150": "cowboy-k-2026",   # Rare knife
    "ghost-1149": "ghost-g-2026",     # Uncommon gun
    "golden-1146": "golden-k-2026",   # Legendary knife
    "laser-1148": "laser-k-2026",     # Rare knife
    "phaser-1151": "phaser-k-2026",   # Rare knife
    "prince-1152": "prince-g-2026",   # Rare gun
    "shadow-1147": "shadow-g-2026",   # Legendary gun
}
CATEGORIES = {
    "Ancient", "Godly", "Chroma", "Legendary", "Rare",
    "Uncommon", "Common", "Unique", "Classic",
}
MARKETPLACE_NAMES = {
    "chromas.gg": "chromas.gg",
    "chroma.gg": "chromas.gg",
}
PERCENT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)%$")
PRICE_RE = re.compile(r"^\$\s*([0-9]+(?:\.[0-9]+)?)$")
SOLD_RE = re.compile(r"([0-9,]+)\s+sold", re.I)
DETAIL_PRICE_RE = re.compile(
    r"Over the last (?P<days>7|30) days it sold for \$(?P<price>[0-9,]+(?:\.[0-9]+)?) "
    r"on average after the marketplace fee",
    re.I,
)
DETAIL_ABOUT_HEADER_RE = re.compile(
    r"\bAbout\s+(?P<name>.+?)\s+(?P=name)\s+is\s+(?:a|an)\s+"
    r"(?P<category>.+?)\s+item in Roblox Murder Mystery 2\s+\(MM2\)",
    re.I,
)
DETAIL_ABOUT_PLAIN_RE = re.compile(
    r"^\s*(?P<name>[^.]+?)\s+is\s+(?:a|an)\s+"
    r"(?P<category>.+?)\s+item in Roblox Murder Mystery 2\s+\(MM2\)",
    re.I,
)
DETAIL_SOLD_7D_RE = re.compile(
    r"([0-9,]+)\s+cop(?:y|ies)\s+(?:changed hands|sold) in the last 7 days", re.I
)
DETAIL_SOLD_30D_RE = re.compile(
    r"(?:\(([0-9,]+)\s+in 30 days\)|([0-9,]+)\s+cop(?:y|ies) sold in the last 30 days)",
    re.I,
)
DETAIL_SOLD_7D_STAT_RE = re.compile(r"\bSold\s*\(7d\)\s*([0-9,]+)", re.I)
DETAIL_SOLD_30D_STAT_RE = re.compile(r"\bSold\s*\(30d\)\s*([0-9,]+)", re.I)
DETAIL_VALUE_RE = re.compile(r"Its value is\s+([0-9,]+)", re.I)
DETAIL_SUPPLY_RE = re.compile(
    r"BloxSwaps counts\s+([0-9,]+)\s+copies in the game,\s+held by\s+([0-9,]+)\s+players",
    re.I,
)
MAX_ITEM_AGE_SECONDS = 15 * 60
DETAIL_WORKERS = 3
REQUEST_TIMEOUT_SECONDS = 25


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.item_links = set()
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "a":
            href = str(attributes.get("href") or "")
            parsed = urlsplit(urljoin(URL, href))
            if parsed.netloc == "robase.gg" and parsed.path.startswith("/item/"):
                self.item_links.add(urljoin(URL, parsed.path))
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data):
        if not self._ignored and data.strip():
            self.parts.append(" ".join(data.split()))


class _MetaParser(HTMLParser):
    """Extract only public Open Graph image metadata used for exact catalogue matching."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og_image = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        attributes = {str(key or "").lower(): str(value or "") for key, value in attrs}
        if attributes.get("property", "").lower() == "og:image":
            self.og_image = attributes.get("content", "").strip()


def _bloxswaps_image_slug(image_url: str) -> str:
    """Return a BloxSwaps-style image asset slug when ROBASE exposes one."""
    parsed = urlsplit(str(image_url or ""))
    if parsed.hostname != "cdn.bloxluck.com" or not parsed.path.startswith("/item-images/"):
        return ""
    filename = parsed.path.rsplit("/", 1)[-1]
    slug = filename.rsplit(".", 1)[0].casefold()
    return slug if re.fullmatch(r"[a-z0-9-]{1,100}", slug) else ""


def _parse_item_name(raw: str) -> str:
    name = " ".join(str(raw or "").split())
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[1] in CATEGORIES:
        name = parts[0]
    else:
        for category in sorted(CATEGORIES, key=len, reverse=True):
            if len(name) > len(category) and name.endswith(category):
                name = name[:-len(category)].rstrip()
                break
    if not name:
        raise ValueError("empty ROBASE item name")
    return name


def normalize_robase_item_name(raw: str) -> str:
    """Normalize a ROBASE table label; never used as a fuzzy identity fallback."""
    return _parse_item_name(raw)


def parse_robase_html(document: str, fetched_at_utc: str | None = None) -> dict:
    parser = _TableParser()
    parser.feed(document)
    items = {}
    for raw_cells in parser.rows:
        cells = list(raw_cells)
        while cells and not cells[0].strip():
            cells.pop(0)
        while cells and not cells[-1].strip():
            cells.pop()
        if len(cells) < 9 or not cells[0].strip().isdigit():
            continue
        name = _parse_item_name(cells[1])
        price_text = cells[2].strip()
        price_match = PRICE_RE.fullmatch(price_text)
        if not price_match:
            continue
        price = float(price_match.group(1))
        sold_match = SOLD_RE.search(cells[7])
        sold_7d = int(sold_match.group(1).replace(",", "")) if sold_match else 0
        if price <= 0:
            raise ValueError(f"non-positive ROBASE price for {name}")
        key = name.casefold()
        if key in items:
            raise ValueError(f"duplicate ROBASE item name: {name}")
        items[key] = {
            "name": name,
            "cash_price_usd": price,
            "price_display": price_text,
            "sold_7d": sold_7d,
            "status": "verified" if sold_7d >= 3 else "unverified",
            "source": URL,
            "notes": "ROBASE completed-sales average; seller proceeds after fees",
        }
    stamp = fetched_at_utc or _utc_now()
    return {
        "schema_version": 1,
        "source": URL,
        "fetched_at_utc": stamp,
        "price_basis": "seller_proceeds_after_marketplace_fees",
        "items": sorted(items.values(), key=lambda item: item["name"].casefold()),
    }


def _parse_item_detail(url: str, document: str, fetched_at_utc: str, etag="", last_modified="") -> dict:
    parser = _VisibleTextParser()
    parser.feed(document)
    text = " ".join(parser.parts)
    table_parser = _TableParser()
    table_parser.feed(document)
    meta = _MetaParser()
    meta.feed(document)
    og_image_url = meta.og_image
    image_slug = _bloxswaps_image_slug(og_image_url)
    about = DETAIL_ABOUT_HEADER_RE.search(text)
    if about:
        name = " ".join(about.group("name").split())
    else:
        about = DETAIL_ABOUT_PLAIN_RE.search(text)
        name = " ".join(about.group("name").split()) if about else ""
    price_match = DETAIL_PRICE_RE.search(text)
    if not about:
        raise ValueError(f"ROBASE item detail did not expose a canonical item identity: {url}")
    category = " ".join(about.group("category").split())
    price_display = "$" + price_match.group("price") if price_match else ""
    price = float(price_match.group("price").replace(",", "")) if price_match else None
    if not name or not category or (price is not None and price <= 0):
        raise ValueError(f"invalid ROBASE item detail: {url}")
    # Cash valuation is intentionally sourced from one market only:
    # the chromas.gg row's Price (7d). Never fall back to StarPets or blended prices.
    chromas_market = None
    for cells in table_parser.rows:
        if len(cells) < 4 or not str(cells[0]).strip().isdigit():
            continue
        marketplace_key = MARKETPLACE_NAMES.get(str(cells[1]).strip().casefold())
        if marketplace_key != "chromas.gg":
            continue
        market_price_match = PRICE_RE.fullmatch(str(cells[2]).strip())
        sold_text = str(cells[3]).strip().replace(",", "")
        if not sold_text.isdigit():
            raise ValueError(f"invalid ROBASE chromas.gg Sold (7d) value for {name}")
        market_sold_7d = int(sold_text)
        if chromas_market is not None:
            raise ValueError(f"duplicate ROBASE chromas.gg marketplace row for {name}")
        if market_sold_7d == 0:
            # ROBASE displays an em dash for Price (7d) when there were no
            # completed Chroma sales in the window. Preserve the zero sample,
            # but never turn an old 30d value or another marketplace into a price.
            chromas_market = {
                "marketplace": "chromas.gg",
                "price_usd": None,
                "price_display": "",
                "sold_7d": 0,
            }
            continue
        if not market_price_match:
            raise ValueError(f"ROBASE chromas.gg Price (7d) is missing despite sales for {name}")
        market_price = float(market_price_match.group(1))
        if market_price <= 0:
            raise ValueError(f"invalid ROBASE chromas.gg 7d market price for {name}")
        chromas_market = {
            "marketplace": "chromas.gg",
            "price_usd": market_price,
            "price_display": "$" + market_price_match.group(1),
            "sold_7d": market_sold_7d,
        }
    marketplace_prices = {"chromas.gg": chromas_market} if chromas_market else {}
    selected_marketplace = "chromas.gg" if chromas_market and chromas_market["sold_7d"] > 0 else None
    selected_market = chromas_market if selected_marketplace else None
    sold_7d_stat = DETAIL_SOLD_7D_STAT_RE.search(text)
    sold_30d_stat = DETAIL_SOLD_30D_STAT_RE.search(text)
    sold_7d_match = DETAIL_SOLD_7D_RE.search(text)
    sold_30d_match = DETAIL_SOLD_30D_RE.search(text)
    sold_7d = (
        int(sold_7d_stat.group(1).replace(",", ""))
        if sold_7d_stat else
        int(sold_7d_match.group(1).replace(",", "")) if sold_7d_match else 0
    )
    sold_30d = (
        int(sold_30d_stat.group(1).replace(",", ""))
        if sold_30d_stat else
        int(next(value for value in sold_30d_match.groups() if value).replace(",", ""))
        if sold_30d_match else 0
    )
    price_window_days = int(price_match.group("days")) if price_match else None
    if price_window_days == 7 and sold_7d == 0 and sold_30d > 0:
        # ROBASE's explicit period counts outrank contradictory descriptive copy.
        # A 7-day average cannot be supported when the page reports zero 7-day sales.
        price_window_days = 30
    value_match = DETAIL_VALUE_RE.search(text)
    supply_match = DETAIL_SUPPLY_RE.search(text)
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return {
        "name": name,
        "robase_slug": slug,
        "robase_category": category,
        "og_image_url": og_image_url,
        "og_image_slug": image_slug,
        "bloxswaps_slug": image_slug or ROBASE_BLOXSWAPS_SLUG_OVERRIDES.get(slug.casefold(), ""),
        # The live cash price is exclusively chromas.gg Price (7d).
        "cash_price_usd": selected_market["price_usd"] if selected_market else None,
        "price_display": selected_market["price_display"] if selected_market else "",
        "all_marketplaces_price_usd": price,
        "all_marketplaces_price_display": price_display,
        "marketplace_prices": marketplace_prices,
        "selected_marketplace": selected_marketplace,
        "selected_marketplace_sold_7d": selected_market["sold_7d"] if selected_market else 0,
        "price_window_days": 7 if selected_market else None,
        "all_marketplaces_price_window_days": price_window_days,
        "sold_7d": sold_7d,
        "sold_30d": sold_30d,
        "bloxswaps_value": int(value_match.group(1).replace(",", "")) if value_match else None,
        "bloxswaps_copies": int(supply_match.group(1).replace(",", "")) if supply_match else None,
        "bloxswaps_holders": int(supply_match.group(2).replace(",", "")) if supply_match else None,
        "status": "verified",
        "source": url,
        "price_basis": "chromas_gg_7d_price",
        "last_checked_at_utc": fetched_at_utc,
        "price_updated_at_utc": fetched_at_utc,
        "etag": etag or "",
        "last_modified": last_modified or "",
    }


def _fetch_item_detail(url: str, cached: dict | None):
    headers = {
        "User-Agent": "MM2-Trade-Scanner/1.0 (public ROBASE price sync; contact repository owner)",
        "Accept": "text/html",
    }
    cached = cached if isinstance(cached, dict) else {}
    if cached.get("etag"):
        headers["If-None-Match"] = str(cached["etag"])
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = str(cached["last_modified"])
    request = Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"ROBASE returned HTTP {response.status} for {url}")
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise RuntimeError(f"ROBASE item page exceeded 1 MB: {url}")
                document = raw.decode("utf-8", errors="strict")
                etag = response.headers.get("ETag", "")
                last_modified = response.headers.get("Last-Modified", "")
            fresh = _parse_item_detail(url, document, _utc_now(), etag, last_modified)
            old_signature = (
                cached.get("selected_marketplace"),
                cached.get("price_display"),
                cached.get("marketplace_prices"),
            )
            new_signature = (
                fresh.get("selected_marketplace"),
                fresh.get("price_display"),
                fresh.get("marketplace_prices"),
            )
            if old_signature == new_signature:
                fresh["price_updated_at_utc"] = cached.get("price_updated_at_utc") or fresh["last_checked_at_utc"]
            return fresh, "unchanged" if old_signature == new_signature else "updated"
        except HTTPError as exc:
            if exc.code == 304 and cached.get("name") and "marketplace_prices" in cached:
                retained = dict(cached)
                retained["last_checked_at_utc"] = _utc_now()
                retained["etag"] = retained.get("etag") or headers.get("If-None-Match", "")
                retained["last_modified"] = retained.get("last_modified") or headers.get("If-Modified-Since", "")
                return retained, "not_modified"
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = retry_after_seconds(
                (exc.headers or {}).get("Retry-After", "")
            ) or 0.0
            time.sleep(min(8.0, max(retry_after, 0.5 * (2 ** attempt))))
        except (TimeoutError, URLError, UnicodeDecodeError, ValueError) as exc:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))
            if isinstance(exc, UnicodeDecodeError):
                continue
    raise RuntimeError(f"ROBASE item page failed after retries: {url}")

def _load_previous_snapshot(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 6
        or data.get("price_basis") != "chromas_gg_7d_price"
        or data.get("catalog_complete") is not True
        or not isinstance(data.get("items"), list)
    ):
        return {}
    return data


def fetch_snapshot(output: Path = DEFAULT_OUTPUT, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict:
    previous = _load_previous_snapshot(output)
    cached_by_slug = {
        str(row.get("robase_slug")): row
        for row in previous.get("items", [])
        if isinstance(row, dict) and row.get("robase_slug")
    }
    request = Request(URL, headers={
        "User-Agent": "MM2-Trade-Scanner/1.0 (public ROBASE price sync; contact repository owner)",
        "Accept": "text/html",
    })
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"ROBASE returned HTTP {response.status}")
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise RuntimeError("ROBASE response exceeded 5 MB safety limit")
    document = raw.decode("utf-8", errors="strict")
    link_parser = _TableParser()
    link_parser.feed(document)
    item_urls = sorted(link_parser.item_links)
    visible = _VisibleTextParser()
    visible.feed(document)
    visible_text = " ".join(visible.parts)
    count_match = re.search(r"All MM2 items\s+([0-9,]+)", visible_text, re.I)
    expected_count = int(count_match.group(1).replace(",", "")) if count_match else 0
    if expected_count < 200 or len(item_urls) != expected_count:
        raise ValueError(
            f"ROBASE item index incomplete: expected={expected_count or 'unknown'} links={len(item_urls)}"
        )

    results = {}
    failures = []
    updated = unchanged = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {
            pool.submit(
                _fetch_item_detail,
                url,
                cached_by_slug.get(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]),
            ): url
            for url in item_urls
        }
        for future in as_completed(futures):
            url = futures[future]
            slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            try:
                row, status = future.result()
                key = row["robase_slug"].casefold()
                if key in results:
                    raise ValueError(f"duplicate ROBASE source slug: {key}")
                results[key] = row
                if status == "updated":
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                reason = " ".join(str(exc).split())[:160]
                failures.append(f"{slug}:{type(exc).__name__}:{reason}")
                cached = cached_by_slug.get(slug)
                if cached and cached.get("name") and cached.get("robase_slug") == slug:
                    results[slug] = dict(cached)

    if failures:
        print("[robase] item-page failure samples: " + " | ".join(failures[:20]))
    rows = sorted(results.values(), key=lambda item: (item["name"].casefold(), item["robase_slug"]))
    now = _utc_now()
    complete = (
        len(rows) == expected_count
        and len({row.get("robase_slug") for row in rows}) == expected_count
        and all(
            row.get("name")
            and len(str(row.get("name"))) <= 100
            and row.get("status") == "verified"
            and _parse_time(row.get("last_checked_at_utc")) > 0
            and time.time() - _parse_time(row.get("last_checked_at_utc")) <= MAX_ITEM_AGE_SECONDS
            for row in rows
        )
    )
    snapshot = {
        "schema_version": 6,
        "source": URL,
        "fetched_at_utc": now,
        "price_basis": "chromas_gg_7d_price",
        "catalog_expected_count": expected_count,
        "catalog_complete": complete,
        "item_pages_updated": updated,
        "item_pages_unchanged": unchanged,
        "item_page_failures": failures[:30],
        "items": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if not complete:
        raise RuntimeError(
            f"ROBASE full catalogue is not fresh and complete: rows={len(rows)}/{expected_count}, "
            f"failures={len(failures)}"
        )
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="Refresh all public ROBASE MM2 item prices")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    snapshot = fetch_snapshot(args.output)
    print(
        f"[robase] fetched={len(snapshot['items'])}/{snapshot['catalog_expected_count']} "
        f"updated={snapshot['item_pages_updated']} unchanged={snapshot['item_pages_unchanged']} "
        f"failures={len(snapshot['item_page_failures'])} complete={snapshot['catalog_complete']} "
        f"at={snapshot['fetched_at_utc']}"
    )


if __name__ == "__main__":
    main()
