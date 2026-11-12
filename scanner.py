#!/usr/bin/env python3
"""BloxSwaps MM2 scanner with verified Supreme Values calculations."""
import csv
import difflib
import itertools
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import urllib.request
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlsplit, parse_qs

BLOXSWAPS_URL = os.environ.get(
    "BLOXSWAPS_URL",
    "https://bloxswaps.com/fr/trade-mm2?trade-type=P2P"
).strip()
RBLXVALUE_API_BASE = "https://api.rblxvalue.com/v2"
BLOXSWAPS_API_BASE = "https://api.bloxswaps.com"
RBLXVALUE_API_KEY = os.environ.get("RBLXVALUE_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MIN_DIFF = float(os.environ.get("MIN_VALUE_DIFFERENCE", "0"))
MIN_PROFIT_PERCENT = float(os.environ.get("MIN_PROFIT_PERCENT", "5"))
FAILURE_THRESHOLD = int(os.environ.get("FAILURE_ALERT_THRESHOLD", "3"))
MAX_ALERTS_PER_RUN = max(1, int(os.environ.get("MAX_ALERTS_PER_RUN", "10")))
ALERT_DELAY_SECONDS = max(0.0, float(os.environ.get("ALERT_DELAY_SECONDS", "1")) )
VALUES_FILE = Path("data/supreme_values.json")
FAILURE_FILE = Path("data/failure_state.json")
HISTORY_FILE = Path("data/history.csv")
MAX_ALERTED = 5000
ALERTED_FILE = Path("data/alerted.json")
UNCERTAIN_FILE = Path("data/telegram_uncertain.json")
SEEN_FILE = Path("data/seen_ads.json")
ALIASES_FILE = Path("aliases.json")
UNMATCHED_FILE = Path("data/unmatched_names.json")
MAINTENANCE_FILE = Path("data/maintenance_alerts.json")
BLOXSWAPS_PAGE_SIZE = 20
MAX_FEED_PAGES = max(1, min(100, int(os.environ.get("MAX_FEED_PAGES", "50"))))
VALUES_TTL_SECONDS = 4 * 60 * 60
SUSPICIOUS_PROFIT_PERCENT = float(os.environ.get("SUSPICIOUS_PROFIT_PERCENT", "100"))
TELEGRAM_MAX_CHARS = 4000

# Items without a numeric Supreme value are excluded from valuation.
IGNORED_VALUE_ITEMS = {"brew", "drool"}


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


class Node:
    def __init__(self, tag="", attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parent = parent
        self.events = []
        self._text_cache = None


class PageTree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].events.append(("node", node))
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].events.append(("node", Node(tag, attrs, self.stack[-1])))

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
    if node._text_cache is not None:
        return node._text_cache
    parts = []
    for kind, value in node.events:
        if kind == "text":
            parts.append(value)
        else:
            if value.tag == "img" and value.attrs.get("alt"):
                parts.append(value.attrs["alt"])
            parts.append(node_text(value))
    node._text_cache = " ".join(parts)
    return node._text_cache


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


def split_item_label(raw):
    """Return an item name and any quantity embedded in its accessible label."""
    name = (raw or "").strip()
    for prefix in ("Image de ", "Image of ", "Image: "):
        if name.casefold().startswith(prefix.casefold()):
            name = name[len(prefix):]
    name = re.sub(r"\s+(dans|in) Murder Mystery 2$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    quantity = None
    normalized = _normalize_quantity_text(name)
    match = re.search(r"(?:[x×]\s*(\d{1,3})|(\d{1,3})\s*[x×])\s*$", normalized, flags=re.I)
    if match:
        parsed = int(match.group(1) or match.group(2))
        quantity = parsed if 1 <= parsed <= 100 else None
        name = name[:match.start()].strip()
    ignored = {
        "coin", "offers", "offres", "ajouts", "ajout", "item stability",
        "déclassements", "declassements", "surclassements",
        "downgrades", "upgrades", "murder mystery 2", "mm2", "image"
    }
    if not name or name.casefold() in ignored:
        return "", None
    return name, quantity


def clean_item_name(raw):
    return split_item_label(raw)[0]


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


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _normalize_quantity_text(raw):
    return (str(raw or "").translate(ARABIC_DIGITS)
            .replace("\u00a0", " ").replace("\u202f", " ")
            .replace("\u2009", " ").replace("\u2007", " ")
            .replace("\u2212", "-"))


def _quantity_marker(raw):
    """Parse a whole explicit marker or structured numeric quantity."""
    text = _normalize_quantity_text(raw).strip()
    match = re.fullmatch(r"(?:x|×)\s*(\d{1,3})|(?:\d{1,3})\s*(?:x|×)", text, flags=re.I)
    if match:
        number = re.search(r"\d{1,3}", text)
        parsed = int(number.group())
        return parsed if 1 <= parsed <= 100 else None
    if re.fullmatch(r"\d{1,3}", text):
        parsed = int(text)
        return parsed if 1 <= parsed <= 100 else None
    return None


def _explicit_stack_quantities(raw):
    """Return all explicit xN/×N/Nx markers; never parse bare prices as counts."""
    text = _normalize_quantity_text(raw)
    pattern = r"(?<![A-Za-z0-9])(?:x|×)\s*(\d{1,3})(?!\d)|(?<![\d.,])(\d{1,3})\s*(?:x|×)(?![A-Za-z0-9])"
    values = []
    for match in re.finditer(pattern, text, flags=re.I):
        parsed = int(match.group(1) or match.group(2))
        if not 1 <= parsed <= 100:
            continue
        values.append(parsed)
    return values


def _explicit_stack_quantity(raw):
    quantities = _explicit_stack_quantities(raw)
    return quantities[0] if quantities else None


def _side_marker_count(text):
    return len(re.findall(
        r"\b(?:propose|offering|offer|recherche|looking\s+for|request)\b",
        text or "", flags=re.I
    ))


def _item_wrapper(image_node):
    """Find the largest ancestor that contains exactly this weapon and no side boundary."""
    wrapper = None
    ancestor = image_node.parent
    while ancestor is not None:
        if len(collect_images_in_order(ancestor)) != 1:
            break
        if _side_marker_count(node_text(ancestor)):
            break
        wrapper = ancestor
        ancestor = ancestor.parent
    return wrapper


def _structured_quantity_from_nodes(image_node, wrapper):
    """Read explicit data-* counts only from the image or its isolated item wrapper."""
    nodes = [image_node]
    if wrapper is not None and wrapper is not image_node:
        nodes.append(wrapper)
    found = []
    for node in nodes:
        for key in ("data-quantity", "data-count", "data-amount", "data-stack", "data-stack-count"):
            raw = node.attrs.get(key)
            if raw is not None:
                quantity = _quantity_marker(raw)
                if quantity is None:
                    return None, True
                found.append(quantity)
    if found and len(set(found)) != 1:
        return None, True
    return (found[0], False) if found else (None, False)


def _semantic_badge_quantity(wrapper):
    if wrapper is None:
        return None
    quantities = []
    for node in walk_nodes(wrapper):
        classes = " ".join((
            node.attrs.get("class", ""), node.attrs.get("id", ""),
            node.attrs.get("data-testid", "")
        )).casefold()
        if re.search(r"quant|count|amount|stack|badge|counter", classes):
            quantity = _quantity_marker(node_text(node))
            if quantity is not None:
                quantities.append(quantity)
    if quantities and len(set(quantities)) == 1:
        return quantities[0]
    return None


def _has_unreadable_quantity_hint(raw, wrapper=None):
    text = _normalize_quantity_text(raw)
    has_explicit_in_segment = bool(_explicit_stack_quantities(text))
    invalid_numeric_marker = re.search(
        r"(?<![A-Za-z0-9])(?:x|×)\s*(\d+)(?!\d)|(?<![\d.,])(\d+)\s*(?:x|×)(?![A-Za-z0-9])",
        text, flags=re.I
    )
    if invalid_numeric_marker:
        parsed = int(invalid_numeric_marker.group(1) or invalid_numeric_marker.group(2))
        if not 1 <= parsed <= 100:
            return True
    if re.search(r"(?<![A-Za-z0-9])(?:x|×)\s*(?:$|[^0-9\s])", text, flags=re.I):
        return True
    if wrapper is not None:
        for node in walk_nodes(wrapper):
            for key in ("aria-label", "title"):
                label = node.attrs.get(key, "")
                if label and re.search(r"(?:^|\s)(?:x|×)\s*(?:$|[^0-9])", _normalize_quantity_text(label), re.I):
                    return True
            classes = " ".join((
                node.attrs.get("class", ""), node.attrs.get("id", ""),
                node.attrs.get("data-testid", "")
            )).casefold()
            if re.search(r"quant|count|amount|stack|badge|counter", classes):
                label = node_text(node).strip()
                if label and not _explicit_stack_quantities(label) and _quantity_marker(label) is None and not has_explicit_in_segment:
                    return True
            for key in ("data-quantity", "data-count", "data-amount", "data-stack", "data-stack-count"):
                if key in node.attrs and _quantity_marker(node.attrs.get(key)) is None:
                    return True
    return False


def _listed_price(raw):
    """Parse a visible decimal listing price for diagnostics only."""
    text = _normalize_quantity_text(raw)
    matches = re.findall(r"(?<![A-Za-z0-9])([0-9]+(?:[.,][0-9]+))\s*([KMB])?(?![A-Za-z0-9])", text, flags=re.I)
    if not matches:
        return None
    number, suffix = matches[0]
    value = float(number.replace(",", "."))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix.upper(), 1)
    return value * multiplier


def quantity_from_image_node(image_node):
    """Compatibility helper: return a quantity only when a local source proves it."""
    name, alt_quantity = split_item_label(image_node.attrs.get("alt", ""))
    if alt_quantity is not None:
        return alt_quantity
    wrapper = _item_wrapper(image_node)
    structured, invalid = _structured_quantity_from_nodes(image_node, wrapper)
    if invalid:
        return None
    if structured is not None:
        return structured
    local_text = node_text(wrapper) if wrapper is not None else ""
    explicit = _explicit_stack_quantities(local_text)
    if len(set(explicit)) == 1:
        return explicit[0]
    if len(set(explicit)) > 1 or _has_unreadable_quantity_hint(local_text, wrapper):
        return None
    return None


def collect_ordered_events(node):
    result = []
    def visit(n):
        for kind, value in n.events:
            if kind == "text":
                result.append(("text", value))
            elif value.tag == "img":
                name, _ = split_item_label(value.attrs.get("alt", ""))
                if name:
                    result.append(("img", {"name": name, "node": value}))
                else:
                    # Coin/decoration images are not item boundaries.
                    continue
            else:
                visit(value)
    visit(node)
    return result


def has_addition_marker(node):
    text = node_text(node).casefold()
    return bool(re.search(r"\bajout(?:s)?\b", text))


def _item_quantity_details(image_node, segment_text):
    wrapper = _item_wrapper(image_node)
    wrapper_text = node_text(wrapper) if wrapper is not None else ""
    alt_name, alt_quantity = split_item_label(image_node.attrs.get("alt", ""))
    structured, invalid_structured = _structured_quantity_from_nodes(image_node, wrapper)
    local_text = " ".join(part for part in (wrapper_text, segment_text) if part)
    attribute_text = " ".join(
        str(node.attrs.get(key, ""))
        for node in ([image_node] + (list(walk_nodes(wrapper)) if wrapper is not None else []))
        for key in ("aria-label", "title")
    )
    explicit = _explicit_stack_quantities(local_text + " " + attribute_text)
    semantic_quantity = _semantic_badge_quantity(wrapper)
    unreadable = _has_unreadable_quantity_hint(local_text + " " + attribute_text, wrapper)
    if alt_quantity is not None:
        explicit.append(alt_quantity)
    if semantic_quantity is not None:
        explicit.append(semantic_quantity)

    if invalid_structured:
        lower = min(explicit) if explicit else 1
        return None, "uncertain", _listed_price(local_text), local_text[:240], lower, None, "invalid structured quantity marker"
    if len(set(explicit)) > 1:
        return None, "uncertain", _listed_price(local_text), local_text[:240], min(explicit), max(explicit), "conflicting explicit quantity markers"
    if unreadable:
        lower = min(explicit) if explicit else 1
        return None, "uncertain", _listed_price(local_text), local_text[:240], lower, None, "ambiguous quantity marker"
    if structured is not None:
        if explicit and any(value != structured for value in explicit):
            return None, "uncertain", _listed_price(local_text), local_text[:240], min(candidates), max(candidates), "HTML/structured quantity disagreement"
        return structured, "structured", _listed_price(local_text), local_text[:240], structured, structured, ""
    if explicit:
        return explicit[0], "explicit", _listed_price(local_text), local_text[:240], explicit[0], explicit[0], ""
    return 1, "implicit_single", _listed_price(local_text), local_text[:240], 1, 1, ""

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
            elif re.search(r"\brecherche\b|\blooking\s+for\b|\brequest\b", low):
                side = "give"
            continue
        if kind != "img" or not value or not side:
            continue
        end = i + 1
        suffix = []
        while end < len(events):
            next_kind, next_value = events[end]
            if next_kind == "img" and next_value and next_value.get("name"):
                break
            if next_kind == "text" and re.search(
                r"\b(?:propose|offering|offer|recherche|looking\s+for|request)\b",
                next_value, flags=re.I
            ):
                break
            if next_kind == "text":
                suffix.append(next_value)
            end += 1
        quantity, source, price, raw_excerpt, quantity_min, quantity_max, reason = _item_quantity_details(
            value["node"], " ".join(suffix)
        )
        target = received if side == "receive" else requested
        target.append({
            "name": value["name"],
            "quantity": quantity,
            "quantity_source": source,
            "quantity_min": quantity_min,
            "quantity_max": quantity_max,
            "quantity_reason": reason,
            "listed_price": price,
            "raw_excerpt": raw_excerpt,
        })
    return received, requested


def listing_context(node):
    """Expand a nested trade-side block to its nearest single-card ancestor with links."""
    current = node
    while current.parent is not None:
        parent = current.parent
        parent_text = node_text(parent)
        receive_markers = len(re.findall(r"\bpropose\b|\boffering\b|\boffer\b", parent_text, flags=re.I))
        give_markers = len(re.findall(r"\brecherche\b|\blooking for\b|\brequest\b", parent_text, flags=re.I))
        has_link = any(
            child.tag == "a" and child.attrs.get("href")
            for child in walk_nodes(parent)
        )
        # A feed/list container can contain several cards. Only adopt an
        # ancestor that still describes exactly one offer and one request.
        if has_link and receive_markers == 1 and give_markers == 1:
            return parent
        current = parent
    return node


def listing_links(node):
    links = []
    owner_url = ""
    for child in walk_nodes(node):
        if child.tag == "a" and child.attrs.get("href"):
            href = child.attrs["href"]
            links.append(href)
            if re.search(r"/(?:[a-z]{2}/)?users?/\d+(?:/|$|[?#])", href, flags=re.I) and not owner_url:
                owner_url = urljoin(BLOXSWAPS_URL, href)
    discussion_url = ""
    for href in links:
        if "ad=" in href.casefold() or "discussion" in href.casefold():
            discussion_url = urljoin(BLOXSWAPS_URL, href)
            break
    if not discussion_url:
        for href in links:
            if any(word in href.casefold() for word in ("trade", "post", "listing")):
                discussion_url = urljoin(BLOXSWAPS_URL, href)
                break
    return discussion_url or BLOXSWAPS_URL, owner_url


def _identity_items(items):
    grouped = {}
    for item in items:
        key = normalize_name(item["name"])
        entry = grouped.setdefault(key, {"quantity": 0, "uncertain": False})
        quantity = item.get("quantity")
        if quantity is None or item.get("quantity_source") == "uncertain":
            entry["uncertain"] = True
        elif not entry["uncertain"]:
            entry["quantity"] += int(quantity)
    return sorted(
        (name, None if entry["uncertain"] else entry["quantity"])
        for name, entry in grouped.items()
    )


def listing_identifier(node):
    """Read stable per-card IDs exposed as common data attributes by the source page."""
    for child in walk_nodes(node):
        for key in ("data-ad-id", "data-listing-id", "data-trade-id", "data-post-id"):
            value = child.attrs.get(key)
            if value and str(value).strip():
                return str(value).strip()
    return ""


def stable_listing_id(owner_url, receive_items, give_items, listing_url="", listing_key_hint=""):
    owner = urlsplit(owner_url or "")
    listing = urlsplit(listing_url or "")
    ad_values = []
    for key, values in parse_qs(listing.query, keep_blank_values=False).items():
        if key.casefold() in {"ad", "id", "listing", "post", "trade"}:
            ad_values.extend((key.casefold(), value) for value in values)
    stable_params = []
    for key, values in parse_qs(listing.query, keep_blank_values=False).items():
        if key.casefold() not in {"page", "sort", "trade-type", "type", "filter", "search"}:
            stable_params.extend((key.casefold(), value) for value in values)
    owner_identity = (
        (owner.scheme + "://" + owner.netloc + owner.path).rstrip("/").casefold()
        if owner_url else
        (listing.scheme + "://" + listing.netloc + listing.path).rstrip("/").casefold()
    )
    if listing_key_hint:
        listing_key = {"hint": listing_key_hint}
    elif ad_values:
        # Preserve the pre-upgrade ID for cards with a stable ad query so the
        # migration does not re-alert trades already present in history.csv.
        listing_key = sorted(ad_values)
    elif owner_url:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    else:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    identity = {
        "owner": owner_identity,
        "listing_key": listing_key,
        "receive": _identity_items(receive_items),
        "give": _identity_items(give_items),
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _extract_balanced_json_object(text, marker):
    position = text.find(marker)
    if position < 0:
        return None
    start = text.find("{", position + len(marker))
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        else:
            if char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except (ValueError, TypeError):
                        return None
    return None


def parse_structured_initial_page(html):
    """Extract the first public Next.js RSC initialPage object, including its cursor."""
    decoder = json.JSONDecoder()
    marker = "self.__next_f.push([1,"
    cursor = 0
    while True:
        position = html.find(marker, cursor)
        if position < 0:
            return None
        start = position + len(marker)
        try:
            payload, consumed = decoder.raw_decode(html[start:])
        except (ValueError, TypeError):
            cursor = start
            continue
        cursor = start + consumed
        if not isinstance(payload, str) or "initialPage" not in payload:
            continue
        page = _extract_balanced_json_object(payload, '"initialPage":')
        if isinstance(page, dict) and isinstance(page.get("items"), list):
            return page


def parse_structured_trade_data(html):
    """Map the public RSC feed page by stable BloxSwaps trade-post ID."""
    page = parse_structured_initial_page(html)
    if not page:
        return {}
    return {
        str(listing["id"]): listing
        for listing in page.get("items", [])
        if isinstance(listing, dict) and listing.get("id")
    }


def _ad_id_from_url(url):
    try:
        values = parse_qs(urlsplit(url or "").query).get("ad", [])
        return str(values[0]).strip() if values else ""
    except (TypeError, ValueError):
        return ""


def _apply_structured_quantities(listing, structured):
    trade_items = structured.get("tradePostItems")
    if not isinstance(trade_items, list):
        return False
    roles = {
        "receive_items": "OFFERED",
        "give_items": "REQUESTED",
    }
    for field, role in roles.items():
        rows = [row for row in trade_items if isinstance(row, dict) and str(row.get("tradeRole", "")).upper() == role]
        items = listing.get(field, [])
        if len(rows) != len(items):
            for item in items:
                item["quantity"] = None
                item["quantity_source"] = "uncertain"
                item["quantity_min"] = 1
                item["quantity_max"] = None
                item["quantity_reason"] = "structured/HTML item count mismatch"
            continue
        for item, row in zip(items, rows):
            try:
                quantity = int(row.get("quantity"))
            except (TypeError, ValueError):
                quantity = None
            if quantity is None or not 1 <= quantity <= 100:
                item["quantity"] = None
                item["quantity_source"] = "uncertain"
                item["quantity_min"] = 1
                item["quantity_max"] = None
                item["quantity_reason"] = "invalid structured quantity"
                continue
            html_source = item.get("quantity_source")
            html_quantity = item.get("quantity")
            if html_source == "uncertain":
                item["quantity"] = None
                item["quantity_source"] = "uncertain"
                item["quantity_min"] = item.get("quantity_min", 1)
                item["quantity_max"] = item.get("quantity_max")
                item["quantity_reason"] = "HTML quantity marker is ambiguous; structured data cannot override the conflict"
            elif html_source == "explicit" and html_quantity != quantity:
                item["quantity"] = None
                item["quantity_source"] = "uncertain"
                item["quantity_min"] = min(int(html_quantity), quantity)
                item["quantity_max"] = max(int(html_quantity), quantity)
                item["quantity_reason"] = f"HTML says {html_quantity}, structured data says {quantity}"
            else:
                item["quantity"] = quantity
                item["quantity_source"] = "structured"
                item["quantity_min"] = quantity
                item["quantity_max"] = quantity
                item["quantity_reason"] = ""
                item["structured_item_id"] = str(row.get("itemDetailsId", ""))
    listing["structured_trade_post_id"] = str(structured.get("id", ""))
    listing["requested_wildcards"] = list(structured.get("requestedWildcards") or [])
    return True


def parse_page(html):
    parser = PageTree()
    parser.feed(html)
    candidates = smallest_matching_nodes(parser.root, has_trade_sides)
    structured_entries = parse_structured_trade_data(html)
    listings = []
    additions = 0
    malformed = 0
    structured_matched = 0
    for node in candidates:
        context = listing_context(node)
        text = node_text(context)
        received, requested = extract_side_items(node)
        if not received or not requested:
            malformed += 1
            continue
        link, owner_url = listing_links(context)
        source_ad_id = _ad_id_from_url(link) or listing_identifier(context)
        structured = structured_entries.get(source_ad_id)
        structured_add = bool(
            structured and any(
                str(value).casefold() in {"add", "ajout", "ajouts"}
                for value in (structured.get("requestedWildcards") or [])
            )
        )
        if has_addition_marker(context) or structured_add:
            additions += 1
            continue
        listing = {
            "id": stable_listing_id(owner_url, received, requested, link, listing_identifier(context)),
            "receive_items": received,
            "give_items": requested,
            "url": link,
            "owner_url": owner_url,
            "source_text": text[:700],
            "source_ad_id": source_ad_id,
        }
        if structured and _apply_structured_quantities(listing, structured):
            structured_matched += 1
        listings.append(listing)
    unique = {item["id"]: item for item in listings}
    parsed = list(unique.values())
    return parsed, {
        "candidate_count": len(candidates),
        "addition_count": additions,
        "malformed_count": malformed,
        "owner_link_count": sum(1 for item in parsed if item.get("owner_url")),
        "structured_entry_count": len(structured_entries),
        "structured_matched_count": structured_matched,
        "structured_unmatched_count": max(0, len(structured_entries) - structured_matched),
    }


def parse_listings(html):
    return parse_page(html)[0]


def request_with_retry(req, timeout, label, attempts=4):
    """Retry transient failures with exponential backoff and respect Retry-After."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                print(f"[http] {label}: HTTP {response.status} ({len(body)} bytes)")
                return body
        except HTTPError as exc:
            detail = exc.read(400).decode("utf-8", errors="replace")
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == attempts - 1:
                raise RuntimeError(f"{label}: HTTP {exc.code}: {detail[:200]}") from exc
            delay = min(30, 2 ** (attempt + 1))
            retry_after = exc.headers.get("Retry-After", "")
            if retry_after:
                try:
                    delay = min(60, max(1, int(retry_after)))
                except ValueError:
                    try:
                        delay = min(60, max(1, int((parsedate_to_datetime(retry_after) -
                            datetime.now(timezone.utc)).total_seconds())))
                    except Exception:
                        pass
            print(f"[retry] {label}: HTTP {exc.code}; waiting {delay}s")
            time.sleep(delay)
        except (URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"{label}: network failure after {attempts} attempts: {exc}") from exc
            delay = min(30, 2 ** (attempt + 1))
            print(f"[retry] {label}: network error; waiting {delay}s")
            time.sleep(delay)


def http_get(url, timeout=35):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    return request_with_retry(req, timeout, "BloxSwaps page").decode("utf-8", errors="replace")

def fetch_bloxswaps():
    return http_get(BLOXSWAPS_URL)


def bloxswaps_api_get_json(path, params=None):
    query = "?" + urlencode(params or {}) if params else ""
    url = BLOXSWAPS_API_BASE + path + query
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        "Referer": BLOXSWAPS_URL,
        "Origin": "https://bloxswaps.com",
    })
    raw = request_with_retry(req, 30, "BloxSwaps API")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        sample = raw[:300].decode("utf-8", errors="replace")
        if any(marker in sample.casefold() for marker in ("cloudflare", "captcha", "just a moment", "challenge-platform")):
            raise RuntimeError("[bloxswaps] Cloudflare/CAPTCHA challenge blocked the data API.") from exc
        raise RuntimeError("[bloxswaps] Data API returned invalid JSON; refusing to parse a partial feed.") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"[bloxswaps] Data API error: {str(payload['error'])[:200]}")
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def fetch_bloxswaps_items_map():
    payload = bloxswaps_api_get_json("/items")
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("data", []))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("[bloxswaps] Item catalogue API returned no usable items.")
    mapping = {}
    for item in payload:
        if isinstance(item, dict) and item.get("id") and item.get("displayName"):
            mapping[str(item["id"])] = item
    if not mapping:
        raise RuntimeError("[bloxswaps] Item catalogue has no id/displayName mapping.")
    print(f"[bloxswaps] Loaded {len(mapping)} item-name mappings from site catalogue")
    return mapping


def _api_trade_post_to_listing(post, item_map):
    post_id = str(post.get("id") or "")
    user = post.get("user") if isinstance(post.get("user"), dict) else {}
    roblox_id = str(user.get("robloxUserId") or "")
    username = str(user.get("robloxUsername") or roblox_id or "unknown")
    display_name = str(user.get("robloxDisplayName") or username)
    owner_url = f"https://bloxswaps.com/fr/users/{roblox_id}" if roblox_id else ""
    query = urlencode({
        "trade-type": "P2P", "view": "messages", "with": username,
        "id": roblox_id, "name": display_name, "ad": post_id,
    })
    listing_url = f"https://bloxswaps.com/fr/trade-mm2?{query}"
    receive, give = [], []
    for row in post.get("tradePostItems") or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("tradeRole") or "").upper()
        if role not in {"OFFERED", "REQUESTED"}:
            continue
        item_id = str(row.get("itemDetailsId") or "")
        details = item_map.get(item_id)
        name = str(details.get("displayName")) if details else f"Unknown item ID {item_id}"
        try:
            quantity = int(row.get("quantity"))
        except (TypeError, ValueError):
            quantity = None
        if quantity is not None and not 1 <= quantity <= 100:
            quantity = None
        item = {
            "name": name,
            "quantity": quantity,
            "quantity_source": "structured" if quantity is not None else "uncertain",
            "quantity_min": quantity if quantity is not None else 1,
            "quantity_max": quantity,
            "quantity_reason": "" if quantity is not None else "invalid structured quantity",
            "listed_price": None,
            "raw_excerpt": f"API itemDetailsId={item_id}; role={role}; quantity={row.get('quantity')}",
            "structured_item_id": item_id,
        }
        if not details:
            item["unmatched_item_id"] = item_id
        (receive if role == "OFFERED" else give).append(item)
    if not receive or not give:
        return None
    listing = {
        "id": "",
        "source_ad_id": post_id,
        "structured_trade_post_id": post_id,
        "receive_items": receive,
        "give_items": give,
        "url": listing_url,
        "owner_url": owner_url,
        "source_text": json.dumps({
            "id": post_id, "tradePostItems": post.get("tradePostItems"),
            "requestedWildcards": post.get("requestedWildcards"),
        }, ensure_ascii=False)[:700],
        "requested_wildcards": list(post.get("requestedWildcards") or []),
        "posted_at": post.get("lastRefreshedAt"),
    }
    listing["id"] = stable_listing_id(owner_url, receive, give, listing_url, "")
    return listing


def fetch_bloxswaps_all(seen_ad_ids=None):
    """Fetch RSC first page and cursor pages until a previously seen post is reached."""
    html = fetch_bloxswaps()
    first_listings, stats = parse_page(html)
    initial_page = parse_structured_initial_page(html)
    if not initial_page:
        stats.update({
            "pagination_pages": 1, "pagination_complete": False,
            "pagination_source": "html-only", "addition_ids": [],
            "unparsed_ad_ids": [],
        })
        return first_listings, stats, []

    raw_first_items = initial_page.get("items") or []
    if seen_ad_ids is None:
        # First run after a missing state must not alert the historical feed backlog.
        seed_ids = [
            str(item.get("id")) for item in raw_first_items
            if isinstance(item, dict) and item.get("id")
        ]
        stats.update({
            "pagination_pages": 1, "pagination_complete": False,
            "pagination_source": "rsc-cold-start", "cold_start": True,
            "addition_ids": [], "unparsed_ad_ids": [],
        })
        return [], stats, seed_ids

    seen = set(str(value) for value in seen_ad_ids)
    by_ad_id = {
        str(listing.get("source_ad_id")): listing
        for listing in first_listings if listing.get("source_ad_id")
    }
    collected = []
    discovered_ids = []
    addition_ids = []
    unparsed_ids = []
    boundary_reached = False
    additions = 0
    malformed = 0
    api_listings_added = 0
    api_posts_scanned = 0
    candidate_count = 0

    def consume_raw_posts(raw_posts, from_api=False, item_map=None):
        nonlocal boundary_reached, additions, malformed, api_listings_added, candidate_count
        for raw in raw_posts:
            if not isinstance(raw, dict) or not raw.get("id"):
                malformed += 1
                continue
            post_id = str(raw["id"])
            if post_id in seen:
                boundary_reached = True
                break
            if post_id in discovered_ids:
                continue
            discovered_ids.append(post_id)
            candidate_count += 1
            wildcards = raw.get("requestedWildcards") or []
            if any(str(value).casefold() in {"add", "ajout", "ajouts"} for value in wildcards):
                additions += 1
                addition_ids.append(post_id)
                continue
            listing = by_ad_id.get(post_id) if not from_api else _api_trade_post_to_listing(raw, item_map or {})
            if listing:
                collected.append(listing)
                if from_api:
                    api_listings_added += 1
            else:
                malformed += 1
                unparsed_ids.append(post_id)

    consume_raw_posts(raw_first_items)
    cursor = (initial_page.get("meta") or {}).get("cursor")
    pages_scanned = 1
    seen_cursors = {str(cursor)} if cursor else set()
    if cursor and not boundary_reached:
        item_map = fetch_bloxswaps_items_map()
        while cursor and pages_scanned < MAX_FEED_PAGES:
            payload = bloxswaps_api_get_json(
                "/trade-posts", {"cursor": cursor, "pageSize": BLOXSWAPS_PAGE_SIZE}
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise RuntimeError("[bloxswaps] Cursor API returned an invalid page structure.")
            page_items = payload["items"]
            api_posts_scanned += len(page_items)
            consume_raw_posts(page_items, from_api=True, item_map=item_map)
            pages_scanned += 1
            if boundary_reached:
                break
            next_cursor = (payload.get("meta") or {}).get("cursor")
            if not next_cursor:
                cursor = None
                break
            if str(next_cursor) in seen_cursors:
                raise RuntimeError("[bloxswaps] Cursor repeated; refusing an incomplete or infinite pagination loop.")
            seen_cursors.add(str(next_cursor))
            cursor = next_cursor

    pagination_complete = not cursor or boundary_reached
    unique = {}
    for listing in collected:
        unique[listing["id"]] = listing
    result = list(unique.values())
    stats.update({
        "candidate_count": candidate_count,
        "addition_count": additions,
        "malformed_count": malformed,
        "owner_link_count": sum(1 for item in result if item.get("owner_url")),
        "pagination_pages": pages_scanned,
        "pagination_api_posts": api_posts_scanned,
        "pagination_complete": pagination_complete,
        "pagination_source": "RSC + cursor API",
        "seen_boundary_reached": boundary_reached,
        "api_listings_added": api_listings_added,
        "addition_ids": addition_ids,
        "unparsed_ad_ids": unparsed_ids,
    })
    if not pagination_complete:
        print(f"[maintenance] Pagination stopped at MAX_FEED_PAGES={MAX_FEED_PAGES} with a cursor remaining.")
    return result, stats, discovered_ids


def normalize_name(name):
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    value = value.casefold().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _plural_keys(name):
    words = normalize_name(name).split()
    options = []
    for word in words:
        variants = {word}
        if len(word) > 4 and word.endswith("ies"):
            variants.add(word[:-3] + "y")
        if len(word) > 4 and word.endswith("ves"):
            variants.add(word[:-3] + "f")
            variants.add(word[:-3] + "fe")
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            variants.add(word[:-1])
        options.append(variants)
    if not options:
        return {""}
    return {" ".join(sorted(words_variant)) for words_variant in itertools.product(*options)}




def load_aliases():
    if not ALIASES_FILE.exists():
        return {}
    try:
        data = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[aliases] aliases.json is unreadable; refusing fuzzy value guesses.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("[aliases] aliases.json must contain an object mapping alias to canonical Supreme name.")
    aliases = {}
    for alias, canonical in data.items():
        if isinstance(alias, str) and isinstance(canonical, str) and alias.strip() and canonical.strip():
            aliases[normalize_name(alias)] = canonical.strip()
    return aliases


def resolve_value_record(name, values, aliases=None):
    key = normalize_name(name)
    exact = values.get(key)
    if exact:
        return exact, "exact", []
    aliases = aliases or {}
    canonical = aliases.get(key)
    if canonical:
        record = values.get(normalize_name(canonical))
        if record:
            return record, "alias", []

    plural = _plural_keys(name)
    plural_matches = [
        (candidate_key, record)
        for candidate_key, record in values.items()
        if _plural_keys(candidate_key).intersection(plural)
    ]
    if len(plural_matches) == 1:
        return plural_matches[0][1], "plural", []
    if len(plural_matches) > 1:
        return None, "ambiguous", [record.get("name", candidate_key) for candidate_key, record in plural_matches[:5]]

    scored = []
    for candidate_key, record in values.items():
        score = max(
            difflib.SequenceMatcher(None, left, right).ratio()
            for left in plural for right in _plural_keys(candidate_key)
        )
        if score >= 0.97:
            scored.append((score, candidate_key, record))
    scored.sort(key=lambda row: row[0], reverse=True)
    if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.03):
        return scored[0][2], "fuzzy", []
    if scored:
        return None, "ambiguous", [row[2].get("name", row[1]) for row in scored[:5]]
    return None, "unmatched", []


def api_get_json(path, params=None):
    if not RBLXVALUE_API_KEY:
        raise RuntimeError("Missing RBLXVALUE_API_KEY GitHub Actions secret.")
    query = "?" + urlencode(params or {}) if params else ""
    url = RBLXVALUE_API_BASE + path + query
    req = urllib.request.Request(url, headers={
        "X-Api-Key": RBLXVALUE_API_KEY,
        "Accept": "application/json",
        "User-Agent": "MM2TradeScanner/2.1",
    })
    raw = request_with_retry(req, 30, "RBLXValue API")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("[rblxvalue] Invalid JSON response") from exc


def _read_values_cache():
    try:
        cached = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
        values = cached.get("values", {})
        if isinstance(values, dict) and len(values) >= 100:
            age = max(0, int(time.time() - float(cached.get("fetched_at", 0))))
            return values, age
    except (ValueError, OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return None, None


def load_supreme_values():
    # Always fetch the provider's current snapshot on every scan. The API
    # itself may publish Supreme's upstream changes on its own refresh cadence;
    # never silently calculate trades from an older local snapshot.
    cached_values, cached_age = _read_values_cache()
    if cached_values is not None:
        print(f"[supreme] Cached snapshot available for diagnostics only (age {cached_age // 60} min); refreshing API")

    all_values = {}
    try:
        offset = 0
        page_size = 100
        total = None
        while total is None or offset < total:
            payload = api_get_json("/items", {"limit": page_size, "offset": offset})
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                if offset == 0:
                    raise RuntimeError("[supreme] API returned no item list; no trade values will be guessed")
                break
            total = int(payload.get("total", len(items)))
            for item in items:
                name = item.get("name")
                source_value = item.get("value_source_b")
                if not name or source_value is None:
                    continue
                try:
                    source_value = float(source_value)
                except (TypeError, ValueError):
                    continue
                if source_value > 0 and math.isfinite(source_value):
                    secondary = item.get("value_source_a")
                    try:
                        secondary = float(secondary) if secondary is not None else None
                        if secondary is not None and not math.isfinite(secondary):
                            secondary = None
                    except (TypeError, ValueError):
                        secondary = None
                    key = normalize_name(name)
                    candidate = {
                        "name": name, "value": source_value, "secondary_value": secondary
                    }
                    existing = all_values.get(key)
                    if existing is None:
                        candidate["variants"] = [candidate.copy()]
                        all_values[key] = candidate
                    else:
                        variants = existing.get("variants") or [{
                            "name": existing.get("name", name),
                            "value": float(existing["value"]),
                            "secondary_value": existing.get("secondary_value"),
                        }]
                        if not any(
                            float(variant.get("value", 0)) == source_value
                            and normalize_name(variant.get("name", "")) == normalize_name(name)
                            for variant in variants
                        ):
                            variants.append(candidate.copy())
                        existing["variants"] = variants
                        high = max(float(variant["value"]) for variant in variants)
                        low = min(float(variant["value"]) for variant in variants)
                        existing["value"] = high
                        high_variant = next(variant for variant in variants if float(variant["value"]) == high)
                        existing["name"] = high_variant.get("name", name)
                        existing["secondary_value"] = high_variant.get("secondary_value")
                        print(
                            f"[supreme] Duplicate normalized name {name!r}: "
                            f"preserving range {low:g}..{high:g}; give uses {high:g}, receive uses {low:g}"
                        )
            offset += len(items)
            if len(items) < page_size:
                break
            time.sleep(0.25)
        if len(all_values) < 100:
            raise RuntimeError(f"[supreme] Only {len(all_values)} numeric source_b values loaded; refusing to calculate")
    except Exception as exc:
        # Fail closed: stale Supreme values can manufacture false-profit alerts.
        raise RuntimeError(
            f"[supreme] Could not refresh current source_b values; refusing stale-cache calculations ({type(exc).__name__})"
        ) from exc

    VALUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(VALUES_FILE, json.dumps({
        "fetched_at": time.time(),
        "source": "Supreme Values via RBLXValue API value_source_b",
        "values": all_values,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"[supreme] Loaded {len(all_values)} numeric Supreme source_b values via API")
    return all_values


def _select_conservative_variant(record, side):
    variants = record.get("variants") if isinstance(record, dict) else None
    variants = variants if isinstance(variants, list) and variants else [record]
    valid = []
    for variant in variants:
        try:
            value = float(variant["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            valid.append((value, variant))
    if not valid:
        return None, None
    value, selected = (max(valid, key=lambda row: row[0]) if side == "give"
                       else min(valid, key=lambda row: row[0]))
    return value, selected


def calculate_side(items, values, aliases=None, side="give"):
    total = 0.0
    breakdown = []
    missing = []
    grouped = {}
    for item in items:
        key = normalize_name(item["name"])
        if key in IGNORED_VALUE_ITEMS:
            continue
        record, match_source, candidates = resolve_value_record(item["name"], values, aliases)
        if not record:
            suffix = f" (ambiguous: {', '.join(candidates)})" if candidates else ""
            missing.append(f"{item['name']}{suffix}")
            continue
        unit_value, selected = _select_conservative_variant(record, side)
        if unit_value is None:
            missing.append(item["name"])
            continue
        if match_source != "exact":
            item["value_match_source"] = match_source
            item["resolved_value_name"] = selected.get("name", record.get("name", item["name"]))
        resolved_key = normalize_name(record.get("name", item["name"]))
        entry = grouped.setdefault(resolved_key, {
            "name": selected.get("name", record.get("name", item["name"])),
            "quantity": 0, "unit_value": unit_value,
        })
        quantity = item.get("quantity")
        if quantity is None or item.get("quantity_source") == "uncertain":
            missing.append(f"{item['name']} (quantity uncertain)")
            continue
        if not 1 <= int(quantity) <= 100:
            missing.append(f"{item['name']} (quantity outside supported range)")
            continue
        entry["quantity"] += int(quantity)

    for item in grouped.values():
        quantity = item["quantity"]
        item_total = item["unit_value"] * quantity
        total += item_total
        suffix = f" x{quantity}" if quantity > 1 else ""
        breakdown.append(
            f"• {item['name']}{suffix} — {item['unit_value']:g} each = {item_total:g}"
        )
    return total, breakdown, missing


def calculate_side_bounds(items, values, aliases=None, side="give"):
    """Calculate conservative lower/upper totals without inventing an unknown stack count."""
    grouped = {}
    missing = []
    uncertain = []
    for item in items:
        key = normalize_name(item["name"])
        if key in IGNORED_VALUE_ITEMS:
            continue
        record, match_source, candidates = resolve_value_record(item["name"], values, aliases)
        if not record:
            suffix = f" (ambiguous: {', '.join(candidates)})" if candidates else ""
            missing.append(f"{item['name']}{suffix}")
            continue
        unit_value, selected = _select_conservative_variant(record, side)
        if unit_value is None:
            missing.append(item["name"])
            continue
        if match_source != "exact":
            item["value_match_source"] = match_source
            item["resolved_value_name"] = selected.get("name", record.get("name", item["name"]))
        resolved_key = normalize_name(record.get("name", item["name"]))
        entry = grouped.setdefault(resolved_key, {
            "name": selected.get("name", record.get("name", item["name"])),
            "min_quantity": 0, "max_quantity": 0, "unbounded": False,
            "unit_value": unit_value,
        })
        quantity = item.get("quantity")
        source = item.get("quantity_source", "implicit_single")
        qmin = item.get("quantity_min")
        qmax = item.get("quantity_max")
        if qmin is None:
            qmin = quantity if quantity is not None else 1
        if qmax is None and quantity is not None and source != "uncertain":
            qmax = quantity
        try:
            qmin = int(qmin)
            qmax = int(qmax) if qmax is not None else None
        except (TypeError, ValueError):
            qmin, qmax = 1, None
        if qmin < 1 or qmin > 100 or (qmax is not None and (qmax < qmin or qmax > 100)):
            qmin, qmax = 1, None
            source = "uncertain"
        entry["min_quantity"] += qmin
        if qmax is None:
            entry["unbounded"] = True
        elif not entry["unbounded"]:
            entry["max_quantity"] += qmax
        if source == "uncertain":
            uncertain.append({
                "name": item["name"], "quantity_min": qmin,
                "quantity_max": qmax, "quantity_source": source,
                "quantity_reason": item.get("quantity_reason", ""),
                "listed_price": item.get("listed_price"), "raw_excerpt": item.get("raw_excerpt", ""),
            })

    total_min = 0.0
    total_max = 0.0
    unbounded_total = False
    breakdown = []
    for item in grouped.values():
        low = item["min_quantity"]
        high = None if item["unbounded"] else item["max_quantity"]
        low_total = item["unit_value"] * low
        total_min += low_total
        if high is None:
            unbounded_total = True
            quantity_label = f"x? ({low}..∞)"
            total_label = f"{low_total:g}..∞"
        else:
            high_total = item["unit_value"] * high
            total_max += high_total
            quantity_label = f"x{low}" if low == high else f"x? ({low}..{high})"
            total_label = f"{low_total:g}" if low == high else f"{low_total:g}..{high_total:g}"
        breakdown.append(
            f"• {item['name']} {quantity_label} — "
            f"{item['unit_value']:g} each = {total_label}"
        )
    return total_min, (math.inf if unbounded_total else total_max), breakdown, missing, uncertain


def format_number(value):
    if math.isinf(float(value)):
        return "∞"
    if math.isnan(float(value)):
        return "uncertain"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class TelegramDeliveryUncertain(RuntimeError):
    """Telegram may have accepted a message but the response was not received."""


def send_telegram(message):
    message = str(message)
    if len(message) > TELEGRAM_MAX_CHARS:
        message = message[:TELEGRAM_MAX_CHARS - 24] + "\n[message truncated]"
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode()

    # sendMessage is non-idempotent. Never apply generic network/5xx retries:
    # Telegram may have accepted the message even when its response was lost.
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read(2000)
            if exc.code == 429 and attempt < 3:
                try:
                    result = json.loads(raw.decode("utf-8"))
                    retry_after = result.get("parameters", {}).get("retry_after", 2 ** (attempt + 1))
                except (ValueError, UnicodeDecodeError):
                    retry_after = 2 ** (attempt + 1)
                delay = min(60, max(1, int(retry_after)))
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
        if result.get("ok"):
            return
        if int(result.get("error_code", 0) or 0) == 429 and attempt < 3:
            retry_after = result.get("parameters", {}).get("retry_after", 2 ** (attempt + 1))
            delay = min(60, max(1, int(retry_after)))
            print(f"[retry] Telegram explicitly rejected sendMessage with 429; waiting {delay}s")
            time.sleep(delay)
            continue
        raise RuntimeError(f"[telegram] API rejected message: {result.get('description', 'unknown error')}")
    raise RuntimeError("[telegram] Rate limit persisted after explicit 429 responses")


def load_seen_ads():
    """Return None only when state is genuinely absent; corruption must stop the scan."""
    if not SEEN_FILE.exists():
        return None
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"[state] Seen-ad state is unreadable ({SEEN_FILE}); refusing to replay old feed pages."
        ) from exc
    if isinstance(data, dict) and isinstance(data.get("ad_ids"), list):
        return set(str(value) for value in data["ad_ids"])
    if isinstance(data, list):
        return set(str(value) for value in data)
    raise RuntimeError(
        f"[state] Seen-ad state has an unsupported structure ({SEEN_FILE}); refusing to replay old feed pages."
    )


def save_seen_ads(ad_ids):
    ordered = sorted(set(str(value) for value in ad_ids if value))[-50000:]
    atomic_write_text(SEEN_FILE, json.dumps({
        "version": 1, "updated_at": datetime.now(timezone.utc).isoformat(),
        "ad_ids": ordered,
    }, indent=2) + "\n")


def load_alerted():
    """Load sent IDs; tolerate missing first-run files but never reset on corruption."""
    ordered = []
    if ALERTED_FILE.exists():
        try:
            data = json.loads(ALERTED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"[state] Alerted-state file is unreadable ({ALERTED_FILE}); "
                "refusing to send potentially duplicated alerts."
            ) from exc
        if isinstance(data, dict) and isinstance(data.get("alerted"), list):
            ordered.extend(str(value) for value in data["alerted"])
        elif isinstance(data, list):
            ordered.extend(str(value) for value in data)
        else:
            raise RuntimeError(
                f"[state] Alerted-state file has an unsupported structure ({ALERTED_FILE}); "
                "refusing to send potentially duplicated alerts."
            )

    if HISTORY_FILE.exists():
        try:
            with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "listing_id" not in reader.fieldnames:
                    raise RuntimeError(
                        f"[state] History file has no listing_id column ({HISTORY_FILE}); "
                        "refusing to send potentially duplicated alerts."
                    )
                for row in reader:
                    if row.get("listing_id"):
                        ordered.append(str(row["listing_id"]))
        except (OSError, csv.Error) as exc:
            raise RuntimeError(
                f"[state] History file is unreadable ({HISTORY_FILE}); "
                "refusing to send potentially duplicated alerts."
            ) from exc
    return list(dict.fromkeys(ordered))


def save_alerted(alerted):
    ordered = list(dict.fromkeys(str(value) for value in alerted))[-MAX_ALERTED:]
    atomic_write_text(ALERTED_FILE, json.dumps({"version": 2, "alerted": ordered}, indent=2) + "\n")


def load_uncertain_ids():
    if not UNCERTAIN_FILE.exists():
        return set()
    try:
        data = json.loads(UNCERTAIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery file is unreadable; refusing to risk duplicate alerts."
        ) from exc
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return {str(value) for value in data["ids"]}
    if isinstance(data, list):
        return {str(value) for value in data}
    raise RuntimeError(
        "[state] Telegram uncertain-delivery file has an invalid structure; refusing duplicate-risk sends."
    )


def save_uncertain_ids(ids):
    ordered = sorted(set(str(value) for value in ids))
    atomic_write_text(UNCERTAIN_FILE, json.dumps({"ids": ordered}, indent=2) + "\n")


def ignored_item_names(listing):
    """Return unique explicitly ignored item names in give-then-receive order."""
    names = []
    seen = set()
    for item in listing.get("give_items", []) + listing.get("receive_items", []):
        name = str(item.get("name", "")).strip()
        key = normalize_name(name)
        if key in IGNORED_VALUE_ITEMS and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def format_trade_message(listing, give_total, give_lines, receive_total, receive_lines, difference):
    result = "WIN" if difference > 0 else ("FAIR" if difference == 0 else "LOSS")
    owner_link = listing.get("owner_url") or listing.get("url") or BLOXSWAPS_URL
    lines = [
        "MM2 SUPREME TRADE SCANNER",
        f"OPEN OWNER PROFILE: {owner_link}",
        *( [f"Listing details: {listing['url']}"] if listing.get("url") and listing.get("url") != owner_link else [] ),
        "",
        "YOU GIVE (BloxSwaps: Recherche):",
        *(give_lines or ["• No items"]),
        f"TOTAL YOU GIVE (scenario): {format_number(give_total)}",
        "",
        "YOU RECEIVE (BloxSwaps: Propose):",
        *(receive_lines or ["• No items"]),
        f"TOTAL YOU RECEIVE (scenario): {format_number(receive_total)}",
        "",
        f"SUPREME PROFIT (scenario): {format_number(difference)}",
        f"CALCULATED RESULT: {result}",
        "",
        "QUANTITY PROVENANCE (listed prices are diagnostic only, never used for Supreme value):",
    ]
    for side, field in (("Propose/receive", "receive_items"), ("Recherche/give", "give_items")):
        for item in listing.get(field, []):
            source = item.get("quantity_source", "uncertain")
            quantity = item.get("quantity")
            if source == "uncertain":
                low = item.get("quantity_min", 1)
                high = item.get("quantity_max")
                label = f"uncertain ({low}..{high if high is not None else '∞'})"
            else:
                label = f"{quantity} ({source})"
            price = f"; listed_price={item['listed_price']}" if item.get("listed_price") is not None else ""
            raw = str(item.get("raw_excerpt", "")).replace("\n", " ")[:150]
            lines.append(f"• {side}: {item['name']} — {label}{price}; raw={raw!r}")
    lines.extend([
        "",
        "Values: Supreme Values source_b via RBLXValue API (Data from rblxvalue.com).",
    ])
    return "\n".join(lines)


def format_unvalued_message(listing, missing):
    def describe(items):
        return [
            f"• {item['name']}" + (f" x{item.get('quantity', 1)}" if item.get("quantity", 1) > 1 else "")
            for item in items
        ] or ["• No items"]
    owner_link = listing.get("owner_url") or listing.get("url") or BLOXSWAPS_URL
    lines = [
        "MM2 TRADE SCANNER — MANUAL VALUE CHECK",
        f"OPEN OWNER PROFILE: {owner_link}",
        *( [f"Listing details: {listing['url']}"] if listing.get("url") and listing.get("url") != owner_link else [] ),
        "",
        "YOU GIVE (Recherche):",
        *describe(listing.get("give_items", [])),
        "",
        "YOU RECEIVE (Propose):",
        *describe(listing.get("receive_items", [])),
        "",
        "UNMATCHED SUPREME ITEMS: " + ", ".join(sorted(set(missing))),
        "PROFIT: NOT CALCULATED — no value was guessed.",
    ]
    return "\n".join(lines)


def evaluate_listing(listing, values, aliases=None):
    give_min, give_max, give_lines, give_missing, give_uncertain = calculate_side_bounds(
        listing["give_items"], values, aliases, side="give"
    )
    receive_min, receive_max, receive_lines, receive_missing, receive_uncertain = calculate_side_bounds(
        listing["receive_items"], values, aliases, side="receive"
    )
    missing = give_missing + receive_missing
    if missing:
        return None, missing
    if not give_lines or not receive_lines:
        return None, ["one side has no valued items"]
    uncertain_items = (
        [{"side": "Recherche/give", **item} for item in give_uncertain]
        + [{"side": "Propose/receive", **item} for item in receive_uncertain]
    )
    profit_worst = -math.inf if math.isinf(give_max) else receive_min - give_max
    profit_best = math.inf if math.isinf(receive_max) else receive_max - give_min
    manual_review = bool(uncertain_items and profit_worst <= 0 < profit_best)
    if uncertain_items and not manual_review and profit_worst > 0:
        difference = profit_worst
        give_total = give_max
        receive_total = receive_min
        margin = (difference / give_total * 100.0) if give_total > 0 else 0.0
    elif manual_review:
        difference = profit_best
        give_total = give_min
        receive_total = receive_max
        margin = (difference / give_total * 100.0) if give_total > 0 and math.isfinite(difference) else math.inf
    else:
        difference = receive_min - give_min
        give_total = give_min
        receive_total = receive_min
        margin = (difference / give_total * 100.0) if give_total > 0 else (100.0 if difference > 0 else 0.0)
    return {
        "listing": listing, "give_total": give_total, "give_lines": give_lines,
        "receive_total": receive_total, "receive_lines": receive_lines,
        "difference": difference, "margin": margin,
        "profit_worst": profit_worst, "profit_best": profit_best,
        "manual_review": manual_review, "uncertain_items": uncertain_items,
        "give_total_min": give_min, "give_total_max": give_max,
        "receive_total_min": receive_min, "receive_total_max": receive_max,
    }, []


def qualifies(result, min_diff=None, min_percent=None):
    min_diff = MIN_DIFF if min_diff is None else min_diff
    min_percent = MIN_PROFIT_PERCENT if min_percent is None else min_percent
    metric = result.get(
        "profit_best" if result.get("manual_review") else "profit_worst",
        result.get("difference", 0),
    )
    return metric > 0 and metric >= min_diff and result["margin"] >= min_percent


def write_step_summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets.")

    print("[stage] Checking RBLXValue API credential")
    if not RBLXVALUE_API_KEY:
        print("[supreme] API key missing; cached values will be used if available")
    elif not RBLXVALUE_API_KEY.startswith("rbx_"):
        print("[supreme] API key format looks invalid; cached values will be used if available")
    else:
        print("[stage] API key present; secret contents are intentionally never logged")
    print("[stage] Loading Supreme Values before evaluating trades")
    values = load_supreme_values()
    aliases = load_aliases()
    print(f"[aliases] Loaded {len(aliases)} reviewed item aliases")
    print("[stage] Loading seen-offer state")
    seen_ads = load_seen_ads()
    print("[stage] Fetching BloxSwaps listings and cursor pages")
    listings, stats, discovered_ids = fetch_bloxswaps_all(seen_ads)
    if stats.get("cold_start"):
        save_seen_ads(discovered_ids)
        print(
            f"[state] First-run feed baseline created with {len(discovered_ids)} current posts; "
            "historical listings were marked seen without sending alerts."
        )
        record_success()
        write_step_summary("First-run baseline: existing feed posts marked as seen; new posts will be evaluated next scan.")
        return
    seen_ads = set(seen_ads or [])
    if not listings:
        if stats.get("seen_boundary_reached"):
            print("[bloxswaps] Feed is current: first visible offers were already processed.")
            record_success()
            write_step_summary("Feed current: no new trade posts before the seen cursor boundary.")
            return
        if stats["candidate_count"] > 0 and stats["addition_count"] == stats["candidate_count"]:
            print(f"[bloxswaps] No eligible listings: all {stats['addition_count']} candidate cards contained Ajouts.")
            save_seen_ads(seen_ads | set(stats.get("addition_ids", [])))
            record_success()
            write_step_summary("Successful scan: all current candidates excluded for Ajouts.")
            return
        if stats["candidate_count"] == 0 and stats.get("pagination_source") == "html-only":
            raise RuntimeError("[bloxswaps] No trade cards parsed; page structure may have changed or feed is empty.")
        if stats["candidate_count"] == 0 and not stats.get("pagination_complete"):
            raise RuntimeError("[bloxswaps] No feed entries parsed and pagination could not be verified.")
    print(f"[bloxswaps] Eligible listings after Ajouts filter: {len(listings)}")
    print(f"[bloxswaps] Direct owner profiles parsed: {stats.get('owner_link_count', 0)}/{len(listings)}")
    print(
        f"[pagination] pages={stats.get('pagination_pages', 1)} "
        f"api_posts={stats.get('pagination_api_posts', 0)} "
        f"complete={stats.get('pagination_complete', False)} "
        f"seen_boundary={stats.get('seen_boundary_reached', False)}"
    )
    addition_ids = set(stats.get("addition_ids", []))
    if addition_ids:
        seen_ads.update(addition_ids)
        save_seen_ads(seen_ads)
    malformed_count = int(stats.get("malformed_count", 0))
    if malformed_count:
        print(
            f"[maintenance] {malformed_count} card(s) could not be parsed; "
            f"unparsed IDs: {', '.join(stats.get('unparsed_ad_ids', [])[:10])}"
        )
    if not stats.get("pagination_complete", False) and stats.get("pagination_source") != "html-only":
        print("[maintenance] Feed pagination is incomplete; more recent posts may remain beyond the scan limit.")
    # Runtime visibility for real-feed quantity validation: log only explicit stacks.
    for listing in listings:
        for side_label, field in (("Propose/receive", "receive_items"), ("Recherche/give", "give_items")):
            for item in listing.get(field, []):
                quantity = item.get("quantity", 1)
                if quantity and quantity > 1:
                    print(
                        f"[quantity] {side_label}: {item['name']} x{quantity} "
                        f"(listing {listing['id']})"
                    )
    alerted = load_alerted()
    alerted_set = set(alerted)
    uncertain_ids = load_uncertain_ids()
    if uncertain_ids:
        print(
            f"[telegram] {len(uncertain_ids)} listing(s) have uncertain delivery status "
            "and are quarantined to prevent duplicate messages."
        )
    print(
        f"[state] Loaded {len(alerted_set)} previously alerted listing IDs; "
        f"{len(uncertain_ids)} uncertain-delivery IDs."
    )
    already_alerted_skipped = 0
    uncertain_skipped = 0

    def mark_alerted(trade_id):
        if trade_id not in alerted_set:
            alerted.append(trade_id)
            alerted_set.add(trade_id)
            save_alerted(alerted)

    def mark_seen_ad(listing):
        source_id = str(listing.get("source_ad_id") or "")
        if source_id and source_id not in seen_ads:
            seen_ads.add(source_id)
            save_seen_ads(seen_ads)

    skipped_unknown = 0
    sent = 0
    evaluated = 0
    opportunities = []
    for listing in listings:
        trade_id = listing["id"]
        if trade_id in alerted_set:
            already_alerted_skipped += 1
            mark_seen_ad(listing)
            continue
        if trade_id in uncertain_ids:
            uncertain_skipped += 1
            mark_seen_ad(listing)
            continue
        result, missing = evaluate_listing(listing, values, aliases)
        evaluated += 1
        if result is None:
            skipped_unknown += 1
            print(
                f"[skip-unknown] Listing excluded; missing/unvalued items: "
                f"{', '.join(missing)}"
            )
            # Do not send MANUAL VALUE CHECK alerts. Recheck on later scans in
            # case the Supreme mapping becomes available, but never value partially.
            continue
        if not qualifies(result):
            mark_seen_ad(listing)
            continue
        opportunities.append(result)

    opportunities.sort(key=lambda item: (item["difference"], item["margin"]), reverse=True)
    selected_opportunities = opportunities[:MAX_ALERTS_PER_RUN]
    deferred = max(0, len(opportunities) - len(selected_opportunities))
    for op in selected_opportunities:
        listing = op["listing"]
        message = format_trade_message(
            listing, op["give_total"], op["give_lines"],
            op["receive_total"], op["receive_lines"], op["difference"]
        )
        message += (
            f"\nPROFIT RANGE: worst-case {format_number(op['profit_worst'])}; "
            f"best-case {format_number(op['profit_best'])}"
        )
        if op.get("manual_review"):
            message = (
                "⚠️ MANUAL VERIFICATION REQUIRED — best case is profitable, "
                "but worst case is not guaranteed. Do not trade before checking the stack.\n\n"
                + message
            )
        message += f"\nPROFIT MARGIN (scenario): {op['margin']:.2f}%"
        large_stacks = [
            item["name"] for field in ("give_items", "receive_items")
            for item in listing.get(field, [])
            if (
                (item.get("quantity") is not None and int(item["quantity"]) >= 5)
                or int(item.get("quantity_min") or 0) >= 5
            )
        ]
        if large_stacks:
            message += "\nWARNING: quantity 5+ — verify the stack in BloxSwaps."
        ignored_names = ignored_item_names(listing)
        if ignored_names:
            message += "\nNOTE: not valued (no numeric Supreme value): " + ", ".join(ignored_names)
        if op["margin"] >= SUSPICIOUS_PROFIT_PERCENT:
            message += "\nWARNING: unusually high profit - verify item names and values before trading."
        secondary_note = compare_secondary_values(listing["give_items"] + listing["receive_items"], values)
        if secondary_note:
            message += "\nSECOND-SOURCE CHECK (MM2Values): " + secondary_note
        trade_id = listing["id"]
        print(
            f"[telegram] Sending ranked trade id={trade_id}; "
            f"profit={op['difference']:g}"
        )
        # Persist a delivery guard before the non-idempotent Telegram request.
        # It is cleared only after a definite success or definite API rejection.
        uncertain_ids.add(trade_id)
        save_uncertain_ids(uncertain_ids)
        try:
            send_telegram(message)
        except TelegramDeliveryUncertain:
            print(
                f"[telegram] Delivery uncertain for listing {trade_id}; "
                "quarantined, skipping this message and continuing the batch."
            )
            # The pre-send guard remains in uncertain_ids; persist the feed ID too.
            mark_seen_ad(listing)
            continue
        except Exception as exc:
            uncertain_ids.discard(trade_id)
            save_uncertain_ids(uncertain_ids)
            print(
                f"[telegram] Definite send failure for listing {trade_id}: "
                f"{type(exc).__name__}; will retry on a later scan and continue the batch."
            )
            continue
        mark_alerted(trade_id)
        uncertain_ids.discard(trade_id)
        save_uncertain_ids(uncertain_ids)
        mark_seen_ad(listing)
        append_history(listing, op)
        sent += 1
        if sent < len(selected_opportunities) and ALERT_DELAY_SECONDS > 0:
            time.sleep(ALERT_DELAY_SECONDS)

    summary = (
        f"Parsed={len(listings)} | evaluated={evaluated} | unmatched={skipped_unknown} | "
        f"already_alerted={already_alerted_skipped} | uncertain_quarantined={uncertain_skipped} | "
        f"qualified={len(opportunities)} | alerts={sent} | deferred={deferred}"
    )
    print(f"[summary] {summary}")
    write_step_summary(summary)
    record_success()


def compare_secondary_values(items, values):
    differences = []
    for item in items:
        record = values.get(normalize_name(item["name"]), {})
        primary, secondary = record.get("value"), record.get("secondary_value")
        if primary and secondary and primary > 0:
            pct = abs(primary - secondary) / primary * 100.0
            if pct >= 30:
                differences.append(f"{record.get('name', item['name'])}: {pct:.0f}%")
    return ", ".join(differences[:5])


def append_history(listing, op):
    from io import StringIO
    fieldnames = ["detected_at_utc", "listing_id", "profit_supreme", "profit_margin_percent", "you_give", "you_receive", "listing_url", "owner_url"]
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    if HISTORY_FILE.exists():
        try:
            with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames == fieldnames:
                    writer.writeheader()
                    writer.writerows(reader)
                else:
                    writer.writeheader()
        except (OSError, csv.Error):
            writer.writeheader()
    else:
        writer.writeheader()
    writer.writerow({
        "detected_at_utc": datetime.now(timezone.utc).isoformat(),
        "listing_id": listing["id"],
        "profit_supreme": round(op["difference"], 2),
        "profit_margin_percent": round(op["margin"], 2),
        "you_give": round(op["give_total"], 2),
        "you_receive": round(op["receive_total"], 2),
        "listing_url": listing["url"],
        "owner_url": listing.get("owner_url", ""),
    })
    atomic_write_text(HISTORY_FILE, buffer.getvalue())


def record_success():
    atomic_write_text(FAILURE_FILE, json.dumps({"consecutive_failures": 0}, indent=2) + "\n")


def record_failure(error):
    FAILURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(FAILURE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"consecutive_failures": 0}
    count = int(state.get("consecutive_failures", 0)) + 1
    state["consecutive_failures"] = count
    atomic_write_text(FAILURE_FILE, json.dumps(state, indent=2) + "\n")
    print(f"[failure] Consecutive failures: {count}")
    if count >= FAILURE_THRESHOLD and (
        count == FAILURE_THRESHOLD or
        (count > FAILURE_THRESHOLD and (count - FAILURE_THRESHOLD) % 5 == 0)
    ):
        safe_error = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot[REDACTED]", str(error))
        try:
            send_telegram(
                "MM2 Trade Scanner ALERT\n"
                f"Failed {count} consecutive runs.\nLatest error: {safe_error[:600]}\n"
                "See GitHub Actions diagnostics."
            )
            print("[telegram] Repeated-failure alert sent")
        except Exception as notify_error:
            print(f"[telegram] Failure alert could not be sent: {type(notify_error).__name__}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        record_failure(exc)
        sys.exit(1)
