#!/usr/bin/env python3
"""BloxSwaps MM2 scanner with verified Supreme Values calculations."""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

BLOXSWAPS_URL = os.environ.get(
    "BLOXSWAPS_URL",
    "https://bloxswaps.com/fr/trade-mm2?trade-type=P2P"
).strip()
SUPREME_BASE = "https://supremevalues.com/mm2/"
SUPREME_CATEGORIES = (
    "sets", "uniques", "evos", "ancients", "vintages", "chromas",
    "godlies", "legendaries", "rares", "uncommons", "commons",
    "pets", "misc", "untradables",
)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MIN_DIFF = float(os.environ.get("MIN_VALUE_DIFFERENCE", "0"))
SEEN_FILE = Path("data/seen.json")
VALUES_FILE = Path("data/supreme_values.json")
MAX_SEEN = 5000
VALUES_TTL_SECONDS = 4 * 60 * 60


class Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.events = []


class PageTree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].events.append(("node", node))
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].events.append(("node", Node(tag, attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data and data.strip():
            self.stack[-1].events.append(("text", data.strip()))


def walk_nodes(node):
    yield node
    for kind, value in node.events:
        if kind == "node":
            yield from walk_nodes(value)


def node_text(node):
    parts = []
    for kind, value in node.events:
        if kind == "text":
            parts.append(value)
        else:
            if value.tag == "img" and value.attrs.get("alt"):
                parts.append(value.attrs["alt"])
            parts.append(node_text(value))
    return " ".join(parts)


def has_trade_sides(text):
    t = text.casefold()
    return (("propose" in t and "recherche" in t) or
            ("offer" in t and "request" in t) or
            ("offering" in t and "looking for" in t))


def smallest_matching_nodes(node, predicate):
    child_matches = []
    for kind, value in node.events:
        if kind == "node" and predicate(node_text(value)):
            child_matches.extend(smallest_matching_nodes(value, predicate))
    if child_matches:
        return child_matches
    return [node] if predicate(node_text(node)) else []


def clean_item_name(raw):
    name = (raw or "").strip()
    for prefix in ("Image de ", "Image of ", "Image: "):
        if name.casefold().startswith(prefix.casefold()):
            name = name[len(prefix):]
    name = re.sub(r"\s+(dans|in) Murder Mystery 2$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    if not name or name.casefold() in {
        "coin", "offers", "offres", "ajouts", "item stability",
        "murder mystery 2", "mm2", "image"
    }:
        return ""
    return name


def collect_images_in_order(node):
    images = []
    def visit(n):
        for kind, value in n.events:
            if kind == "node":
                if value.tag == "img":
                    name = clean_item_name(value.attrs.get("alt", ""))
                    if name:
                        images.append(name)
                visit(value)
    visit(node)
    return images


def collect_ordered_events(node):
    result = []
    def visit(n):
        for kind, value in n.events:
            if kind == "text":
                result.append(("text", value))
            elif value.tag == "img":
                result.append(("img", clean_item_name(value.attrs.get("alt", ""))))
            else:
                visit(value)
    visit(node)
    return result


def has_addition_marker(node):
    text = node_text(node).casefold()
    return bool(re.search(r"\bajout(?:s)?\b", text))


def extract_side_items(node):
    events = collect_ordered_events(node)
    side = None
    received = []
    requested = []
    for i, (kind, value) in enumerate(events):
        if kind == "text":
            low = value.casefold()
            if re.search(r"\bpropose\b|\boffering\b|\boffer\b", low):
                side = "receive"
            elif re.search(r"\brecherche\b|\blooking for\b|\brequest\b", low):
                side = "give"
        elif kind == "img" and value and side:
            # BloxSwaps shows an optional xN stack count after the item name.
            quantity = 1
            for next_kind, next_value in events[i + 1:]:
                if next_kind == "img":
                    break
                match = re.search(r"\bx\s*(\d+)\b", next_value, flags=re.I)
                if match:
                    quantity = max(1, min(int(match.group(1)), 100))
                    break
            target = received if side == "receive" else requested
            target.append({"name": value, "quantity": quantity})
    return received, requested


def listing_link(node):
    links = []
    for child in walk_nodes(node):
        if child.tag == "a" and child.attrs.get("href"):
            links.append(child.attrs["href"])
    for href in links:
        if any(word in href.casefold() for word in ("trade", "post", "listing", "discussion")):
            return urljoin(BLOXSWAPS_URL, href)
    return urljoin(BLOXSWAPS_URL, links[0]) if links else BLOXSWAPS_URL


def parse_listings(html):
    parser = PageTree()
    parser.feed(html)
    candidates = smallest_matching_nodes(parser.root, has_trade_sides)
    listings = []
    for node in candidates:
        text = node_text(node)
        if has_addition_marker(node):
            continue
        received, requested = extract_side_items(node)
        if not received or not requested:
            continue
        link = listing_link(node)
        stable = hashlib.sha256((text + "|" + link).encode("utf-8")).hexdigest()[:20]
        listings.append({
            "id": stable,
            "receive_items": received,
            "give_items": requested,
            "url": link,
            "source_text": text[:700],
        })
    unique = {item["id"]: item for item in listings}
    return list(unique.values())


def http_get(url, timeout=35):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(f"[http] {response.status} {url} ({len(body)} chars)")
            return body
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def fetch_bloxswaps():
    return http_get(BLOXSWAPS_URL)


def normalize_name(name):
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    value = value.casefold().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def extract_supreme_value_cards(html):
    parser = PageTree()
    parser.feed(html)
    value_pattern = re.compile(r"\bValue\s*-\s*([0-9][0-9,]*(?:\.\d+)?)", re.I)
    cards = smallest_matching_nodes(
        parser.root, lambda text: bool(value_pattern.search(text))
    )
    values = {}
    for card in cards:
        text = node_text(card)
        match = value_pattern.search(text)
        if not match:
            continue
        number = float(match.group(1).replace(",", ""))
        names = []
        for child in walk_nodes(card):
            if child.tag == "a":
                candidate = clean_item_name(node_text(child))
                if candidate and not candidate.casefold().startswith("image"):
                    names.append(candidate)
        # Item cards commonly have two links: image and the visible item title.
        name = names[-1] if names else ""
        if not name:
            for child in walk_nodes(card):
                if child.tag == "img":
                    name = clean_item_name(child.attrs.get("alt", ""))
                    if name:
                        break
        if name:
            values[normalize_name(name)] = {"name": name, "value": number}
    return values


def load_supreme_values():
    if VALUES_FILE.exists():
        try:
            cached = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("fetched_at", 0))
            values = cached.get("values", {})
            if age < VALUES_TTL_SECONDS and len(values) >= 100:
                print(f"[supreme] Using cached verified values: {len(values)} items; age {int(age / 60)} min")
                return values
        except (ValueError, OSError, json.JSONDecodeError, AttributeError):
            pass

    all_values = {}
    successful_pages = 0
    for category in SUPREME_CATEGORIES:
        url = urljoin(SUPREME_BASE, category)
        html = http_get(url)
        category_values = extract_supreme_value_cards(html)
        if not category_values:
            raise RuntimeError(
                f"[supreme] No values extracted from {url}; refusing to calculate trades"
            )
        all_values.update(category_values)
        successful_pages += 1
        print(f"[supreme] {category}: {len(category_values)} items")

    if successful_pages != len(SUPREME_CATEGORIES) or len(all_values) < 100:
        raise RuntimeError(
            f"[supreme] Incomplete values list: {len(all_values)} items from "
            f"{successful_pages}/{len(SUPREME_CATEGORIES)} pages"
        )

    VALUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    VALUES_FILE.write_text(json.dumps({
        "fetched_at": time.time(),
        "source": "Supreme Values",
        "values": all_values,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[supreme] Refreshed {len(all_values)} verified item values")
    return all_values


def calculate_side(items, values):
    total = 0.0
    breakdown = []
    missing = []
    for item in items:
        key = normalize_name(item["name"])
        record = values.get(key)
        if not record:
            missing.append(item["name"])
            continue
        quantity = item["quantity"]
        item_total = float(record["value"]) * quantity
        total += item_total
        suffix = f" x{quantity}" if quantity > 1 else ""
        breakdown.append(
            f"• {record['name']}{suffix} — {record['value']:g} each = {item_total:g}"
        )
    return total, breakdown, missing


def format_number(value):
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def send_telegram(message):
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"[telegram] HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"[telegram] Network error: {exc.reason}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"[telegram] API rejected message: {result.get('description', 'unknown error')}")


def load_seen():
    if not SEEN_FILE.exists():
        return []
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data.get("seen", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def format_trade_message(listing, give_total, give_lines, receive_total, receive_lines, difference):
    result = "WIN" if difference > 0 else ("FAIR" if difference == 0 else "LOSS")
    lines = [
        "MM2 SUPREME TRADE SCANNER",
        "",
        "YOU GIVE (BloxSwaps: Recherche):",
        *(give_lines or ["• No items"]),
        f"TOTAL YOU GIVE: {format_number(give_total)}",
        "",
        "YOU RECEIVE (BloxSwaps: Propose):",
        *(receive_lines or ["• No items"]),
        f"TOTAL YOU RECEIVE: {format_number(receive_total)}",
        "",
        f"SUPREME PROFIT: {difference:+,.2f}".replace(".00", ""),
        f"CALCULATED RESULT: {result}",
        "",
        "Values checked against Supreme Values.",
        f"Source: {listing['url']}",
    ]
    return "\n".join(lines)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets.")

    print("[stage] Loading Supreme Values before evaluating trades")
    values = load_supreme_values()
    print("[stage] Fetching BloxSwaps listings")
    html = fetch_bloxswaps()
    listings = parse_listings(html)
    print(f"[bloxswaps] Eligible listings after Ajouts filter: {len(listings)}")

    seen_list = load_seen()
    seen = set(seen_list)
    evaluated_ids = []
    sent = 0
    skipped_unknown = 0

    for listing in listings:
        trade_id = listing["id"]
        if trade_id in seen:
            continue

        # BloxSwaps Propose = what the poster offers (you receive).
        # BloxSwaps Recherche = what the poster wants (you give).
        give_total, give_lines, give_missing = calculate_side(listing["give_items"], values)
        receive_total, receive_lines, receive_missing = calculate_side(listing["receive_items"], values)
        missing = give_missing + receive_missing
        if missing:
            skipped_unknown += 1
            print(f"[skip] Unmatched Supreme item(s), no value/alert: {', '.join(missing)}")
            continue

        difference = receive_total - give_total
        evaluated_ids.append(trade_id)

        # Zero-profit trades are intentionally included. Negative results are not sent as opportunities.
        if difference >= MIN_DIFF:
            message = format_trade_message(
                listing, give_total, give_lines, receive_total, receive_lines, difference
            )
            print(f"[telegram] Sending verified trade; profit={difference:g}")
            send_telegram(message)
            sent += 1

    updated_seen = (seen_list + evaluated_ids)[-MAX_SEEN:]
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps({"seen": updated_seen}, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[summary] Parsed={len(listings)} | evaluated={len(evaluated_ids)} | "
        f"unmatched_skipped={skipped_unknown} | alerts={sent}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
