#!/usr/bin/env python3
"""Scan public BloxSwaps MM2 P2P listings and send value-positive alerts."""
import hashlib
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

BLOXSWAPS_URL = os.environ.get(
    "BLOXSWAPS_URL",
    "https://bloxswaps.com/fr/trade-mm2?trade-type=P2P"
).strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MIN_DIFF = float(os.environ.get("MIN_VALUE_DIFFERENCE", "0"))
SEEN_FILE = Path("data/seen.json")
MAX_SEEN = 5000


class Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.parts = []


class PageTree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data and data.strip():
            self.stack[-1].parts.append(data.strip())


def node_text(node):
    chunks = list(node.parts)
    alt = node.attrs.get("alt")
    if node.tag == "img" and alt:
        chunks.append(alt)
    for child in node.children:
        chunks.append(node_text(child))
    return " ".join(chunks)


def has_sides(text):
    t = text.casefold()
    return (("propose" in t and "recherche" in t) or
            ("offer" in t and "request" in t) or
            ("offering" in t and "looking for" in t))


def smallest_listing_nodes(node):
    child_matches = []
    for child in node.children:
        if has_sides(node_text(child)):
            child_matches.extend(smallest_listing_nodes(child))
    if child_matches:
        return child_matches
    return [node] if has_sides(node_text(node)) else []


def amount_after(text, labels):
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[:\-]?\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*[KMB]?)",
            text, flags=re.IGNORECASE
        )
        if match:
            raw = re.sub(r"\s+", "", match.group(1)).replace(",", "")
            multiplier = 1
            if raw[-1:].upper() in ("K", "M", "B"):
                multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[raw[-1].upper()]
                raw = raw[:-1]
            return float(raw) * multiplier
    return None


def image_names(node):
    found = []
    def walk(n):
        if n.tag == "img":
            alt = n.attrs.get("alt", "").strip()
            alt = re.sub(r"^Image de\s+", "", alt, flags=re.I)
            alt = re.sub(r"\s+dans Murder Mystery 2$", "", alt, flags=re.I)
            alt = re.sub(r"^Image of\s+", "", alt, flags=re.I)
            alt = re.sub(r"\s+in Murder Mystery 2$", "", alt, flags=re.I)
            if alt and alt.casefold() not in ("murder mystery 2", "mm2"):
                found.append(alt)
        for c in n.children:
            walk(c)
    walk(node)
    return list(dict.fromkeys(found))


def listing_link(node):
    links = []
    def walk(n):
        if n.tag == "a" and n.attrs.get("href"):
            links.append(n.attrs["href"])
        for c in n.children:
            walk(c)
    walk(node)
    # Prefer a listing-specific discussion/details link over navigation links.
    for href in links:
        if any(word in href.casefold() for word in ("trade", "post", "listing", "discussion")):
            return urljoin(BLOXSWAPS_URL, href)
    return urljoin(BLOXSWAPS_URL, links[0]) if links else BLOXSWAPS_URL


def parse_listings(html):
    parser = PageTree()
    parser.feed(html)
    candidates = smallest_listing_nodes(parser.root)
    listings = []
    for node in candidates:
        text = node_text(node)
        offered = amount_after(text, ("Propose", "Offer", "Offering"))
        wanted = amount_after(text, ("Recherche", "Request", "Looking for"))
        if offered is None or wanted is None:
            continue
        names = image_names(node)
        link = listing_link(node)
        stable = hashlib.sha256((text + "|" + link).encode("utf-8")).hexdigest()[:20]
        listings.append({
            "id": stable,
            "give_value": offered,
            "receive_value": wanted,
            "give_names": names,
            "url": link,
            "source_text": text[:500]
        })
    # Keep one result per stable ID in case nested markup produced duplicates.
    unique = {item["id"]: item for item in listings}
    return list(unique.values())


def fetch_page():
    req = urllib.request.Request(
        BLOXSWAPS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MM2TradeScanner/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr,en;q=0.8"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"BloxSwaps returned HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def send_telegram(message):
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def load_seen():
    if not SEEN_FILE.exists():
        return []
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data.get("seen", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets.")

    html = fetch_page()
    listings = parse_listings(html)
    if not listings:
        raise RuntimeError(
            "No BloxSwaps P2P listings could be parsed. The page may have changed "
            "its markup or may require client-side rendering; no alerts were sent."
        )

    seen_list = load_seen()
    seen = set(seen_list)
    new_ids = []
    sent = 0

    for listing in listings:
        trade_id = listing["id"]
        if trade_id in seen:
            continue
        give = listing["give_value"]
        receive = listing["receive_value"]
        difference = receive - give
        new_ids.append(trade_id)

        if difference >= MIN_DIFF:
            message = (
                "MM2 P2P VALUE ALERT — BLOXSWAPS\n"
                f"Offer value: {give:g}\n"
                f"Requested value: {receive:g}\n"
                f"Value difference: {difference:+g}\n"
                f"Items shown: {', '.join(listing['give_names'][:8]) or 'Open listing'}\n"
                f"Source: {listing['url']}"
            )
            send_telegram(message)
            sent += 1

    updated_seen = (seen_list + new_ids)[-MAX_SEEN:]
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"seen": updated_seen}, indent=2) + "\n", encoding="utf-8")
    print(
        f"BloxSwaps listings parsed: {len(listings)} | "
        f"new listings: {len(new_ids)} | alerts sent: {sent}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
