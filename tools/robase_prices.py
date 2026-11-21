"""Fetch the public ROBASE MM2 price table into a validated local snapshot.

ROBASE prices are seller proceeds after marketplace fees, derived from completed
sales. This collector requests the public page once per scan and does not access
accounts, private APIs, or marketplace sessions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://robase.gg/"
DEFAULT_OUTPUT = Path("data/robase_prices.json")
CATEGORIES = {
    "Ancient", "Godly", "Chroma", "Legendary", "Rare",
    "Uncommon", "Common", "Unique", "Classic",
}
PRICE_RE = re.compile(r"^\$\s*([0-9]+(?:\.[0-9]+)?)$")
SOLD_RE = re.compile(r"([0-9,]+)\s+sold", re.I)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
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


def _parse_item_name(raw: str) -> str:
    name = " ".join(str(raw or "").split())
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[1] in CATEGORIES:
        name = parts[0]
    if not name:
        raise ValueError("empty ROBASE item name")
    return name


def parse_robase_html(document: str, fetched_at_utc: str | None = None) -> dict:
    parser = _TableParser()
    parser.feed(document)
    items = {}
    for cells in parser.rows:
        if len(cells) < 9 or not cells[0].strip().isdigit():
            continue
        name = _parse_item_name(cells[1])
        price_match = PRICE_RE.fullmatch(cells[2].strip())
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
            "sold_7d": sold_7d,
            "status": "verified" if sold_7d >= 3 else "unverified",
            "source": "https://robase.gg/",
            "notes": "ROBASE completed-sales average; seller proceeds after fees",
        }
    stamp = fetched_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "source": URL,
        "fetched_at_utc": stamp,
        "price_basis": "seller_proceeds_after_marketplace_fees",
        "items": sorted(items.values(), key=lambda item: item["name"].casefold()),
    }


def fetch_snapshot(output: Path = DEFAULT_OUTPUT, timeout: int = 25) -> dict:
    request = Request(URL, headers={
        "User-Agent": "MM2-Trade-Scanner/1.0 (public price snapshot; contact repository owner)",
        "Accept": "text/html",
    })
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"ROBASE returned HTTP {response.status}")
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise RuntimeError("ROBASE response exceeded 5 MB safety limit")
    document = raw.decode("utf-8", errors="strict")
    snapshot = parse_robase_html(document)
    if len(snapshot["items"]) < 100:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", document, flags=re.I | re.S)
        title = " ".join(title_match.group(1).split())[:100] if title_match else "missing"
        diagnostic_parser = _TableParser()
        diagnostic_parser.feed(document)
        row_sizes = [len(row) for row in diagnostic_parser.rows[:3]]
        numeric_rank_rows = sum(bool(row and row[0].strip().isdigit()) for row in diagnostic_parser.rows)
        valid_price_rows = sum(
            len(row) > 2 and bool(PRICE_RE.fullmatch(row[2].strip()))
            for row in diagnostic_parser.rows
        )
        tr_count = len(re.findall(r"<tr\b", document, flags=re.I))
        table_count = len(re.findall(r"<table\b", document, flags=re.I))
        raise ValueError(
            "ROBASE table incomplete: parsed=%s tr_tags=%s table_tags=%s bytes=%s "
            "title=%r row_sizes=%s numeric_rank_rows=%s valid_price_rows=%s"
            % (
                len(snapshot["items"]), tr_count, table_count, len(raw), title,
                row_sizes, numeric_rank_rows, valid_price_rows,
            )
        )
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
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="Refresh the public ROBASE MM2 price snapshot")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    snapshot = fetch_snapshot(args.output)
    verified = sum(item["status"] == "verified" for item in snapshot["items"])
    print(
        f"[robase] fetched={len(snapshot['items'])} verified={verified} "
        f"low_sample={len(snapshot['items']) - verified} at={snapshot['fetched_at_utc']}"
    )


if __name__ == "__main__":
    main()
