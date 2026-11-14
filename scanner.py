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
import signal
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import urllib.request
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlsplit, urlunsplit, parse_qs

import rblxvalue_guard
from tools.metrics import update_metrics
from tools.alert_policy import rank_and_split
from tools.cash_market import calculate_cash_side, cash_profit_bounds, load_cash_price_catalog, normalize_cash_name

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
MAX_ALERTS_PER_RUN = max(0, int(os.environ.get("MAX_ALERTS_PER_RUN", "0")))
_SESSION_ALERTS_SENT = 0
_SESSION_ALERT_LIMIT = MAX_ALERTS_PER_RUN
ALERT_DELAY_SECONDS = max(0.0, float(os.environ.get("ALERT_DELAY_SECONDS", "1.1")) )
VALUES_FILE = Path("data/supreme_values.json")
VALUES_AUDIT_FILE = Path("data/supreme_values_audit.json")
RBLXVALUE_KEY_HEALTH_FILE = Path("data/rblxvalue_key_health.json")
RBLXVALUE_KEY_HEALTH_INTERVAL_SECONDS = 6 * 60 * 60
RBLXVALUE_KEY_HEALTH_RETRY_SECONDS = 3 * 60 * 60
FAILURE_FILE = Path("data/failure_state.json")
HISTORY_FILE = Path("data/history.csv")
MAX_ALERTED = 5000
ALERTED_FILE = Path("data/alerted.json")
UNCERTAIN_FILE = Path("data/telegram_uncertain.json")
SEEN_FILE = Path("data/seen_ads.json")
PENDING_FILE = Path("data/pending_recheck.json")
PENDING_RECHECK_SCHEDULE_FILE = Path("data/pending_recheck_schedule.json")
PENDING_RECHECK_BACKOFF_SECONDS = (300, 900, 1800, 3600, 21600)
VALID_PENDING_REASONS = frozenset({
    "legacy_unclassified", "unmatched_value", "stability_unverified",
    "stability_blocked", "quantity_uncertain", "unparsed_feed",
    "telegram_failure", "deferred_offer",
})
PRIORITY_PENDING_REASONS = frozenset({
    "quantity_uncertain", "unparsed_feed", "telegram_failure", "deferred_offer",
})
MAX_PENDING_RECHECK_PER_SCAN = max(1, int(os.environ.get('MAX_PENDING_RECHECK_PER_SCAN', '50')))
MAX_BACKLOG_RECHECK_PAGES = max(1, int(os.environ.get('MAX_BACKLOG_RECHECK_PAGES', '25')))
BACKLOG_RECHECK_EVERY_CYCLES = max(1, int(os.environ.get('BACKLOG_RECHECK_EVERY_CYCLES', '4')))
FIRST_SEEN_FILE = Path("data/first_seen_ads.json")
LAST_SUCCESS_FILE = Path("data/last_successful_scan.json")
WATCH_FILE = Path("data/watch_ads.json")
ALIASES_FILE = Path("aliases.json")
UNMATCHED_FILE = Path("data/unmatched_names.json")
MAINTENANCE_FILE = Path("data/maintenance_alerts.json")
BLOXSWAPS_PAGE_SIZE = 20
MAX_FEED_PAGES = max(1, min(1000, int(os.environ.get("MAX_FEED_PAGES", "1000"))))
VALUES_REVALIDATION_INTERVAL_SECONDS = 12 * 60 * 60
RBLXVALUE_META_POLL_INTERVAL_SECONDS = 60 * 60
RBLXVALUE_META_FILE = Path("data/rblxvalue_meta.json")
RBLXVALUE_DETAIL_FILE = Path("data/rblxvalue_detail_enrichment.json")
RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY = max(
    0, int(os.environ.get("RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY", "6"))
)
RBLXVALUE_DETAIL_RETRY_SECONDS = 6 * 60 * 60
RBLXVALUE_META_POLL_ENABLED = os.environ.get("RBLXVALUE_META_POLL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
VALUES_TTL_SECONDS = min(VALUES_REVALIDATION_INTERVAL_SECONDS, max(60, int(os.environ.get("VALUES_TTL_SECONDS", str(VALUES_REVALIDATION_INTERVAL_SECONDS)))))
BLOXSWAPS_ITEMS_TTL_SECONDS = max(300, int(os.environ.get("BLOXSWAPS_ITEMS_TTL_SECONDS", "3600")))
PENDING_TTL_SECONDS = max(86400, int(os.environ.get("PENDING_TTL_SECONDS", str(7 * 86400))))
UNCERTAIN_TTL_SECONDS = max(3600, int(os.environ.get("UNCERTAIN_TTL_SECONDS", str(24 * 3600))))
BLOXSWAPS_ITEMS_FILE = Path("data/bloxswaps_items.json")
SUSPICIOUS_PROFIT_PERCENT = float(os.environ.get("SUSPICIOUS_PROFIT_PERCENT", "100"))
CASH_SELL_HAIRCUT_PCT = float(os.environ.get("CASH_SELL_HAIRCUT_PCT", "15"))
CASH_MIN_PROFIT_USD = float(os.environ.get("CASH_MIN_PROFIT_USD", "1"))
CASH_MIN_MARGIN_PERCENT = float(os.environ.get("CASH_MIN_MARGIN_PERCENT", "10"))
CASH_PRICE_FILE = Path("data/cash_prices.csv")
CASH_PRICE_SNAPSHOT_FILE = Path("data/cash_price_snapshot.json")
TELEGRAM_MAX_CHARS = 4000
_LAST_TELEGRAM_SEND_AT = 0.0
_LAST_ITEMS_FORCE_REFRESH_AT = 0.0

# Items without a numeric Supreme value are excluded from valuation.
IGNORED_VALUE_ITEMS = {"brew", "drool"}


def redact(text):
    """Remove configured credentials and known credential formats from text."""
    safe = str(text)
    configured = {
        os.environ.get(name, "").strip()
        for name in (
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RBLXVALUE_API_KEY",
            "DISCORD_BOT_TOKEN", "DISCORD_MANUAL_MESSAGES_JSON",
            "DISCORD_MANUAL_MESSAGES_JSON_2",
        )
    } | {BOT_TOKEN, CHAT_ID, RBLXVALUE_API_KEY}
    # Also redact individual text fields from manual-import JSON in case a
    # diagnostic accidentally emits a parsed field rather than the full secret.
    for env_name in ("DISCORD_MANUAL_MESSAGES_JSON", "DISCORD_MANUAL_MESSAGES_JSON_2"):
        try:
            payload = json.loads(os.environ.get(env_name, "") or "{}")
            stack = [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, str) and len(item) >= 6:
                    configured.add(item)
        except (ValueError, TypeError):
            pass
    for secret in sorted((v for v in configured if v and len(v) >= 6), key=len, reverse=True):
        safe = safe.replace(secret, "[REDACTED]")
    safe = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot[REDACTED]", safe)
    safe = re.sub(r"rbx_[A-Za-z0-9_-]+", "rbx_[REDACTED]", safe)
    safe = re.sub(r"(?i)(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})", "[REDACTED_DISCORD_TOKEN]", safe)
    safe = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1[REDACTED]", safe)
    return safe

class RedactingStream:
    """Line-buffered stream that redacts secrets before writing logs."""
    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""
    def write(self, value):
        value = str(value)
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._stream.write(redact(line) + "\n")
        return len(value)
    def flush(self):
        if self._buffer:
            self._stream.write(redact(self._buffer))
            self._buffer = ""
        self._stream.flush()
    def __getattr__(self, name):
        return getattr(self._stream, name)

def install_redacting_streams():
    """Install secret-safe stdout/stderr filters once, before scanner output."""
    if not isinstance(sys.stdout, RedactingStream):
        sys.stdout = RedactingStream(sys.stdout)
    if not isinstance(sys.stderr, RedactingStream):
        sys.stderr = RedactingStream(sys.stderr)

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
    receive = bool(re.search(r"\bpropose\b|\boffering\b|\boffer\b", t))
    give = bool(re.search(r"\brecherche\b|\blooking for\b|\brequest\b", t))
    return receive and give


def smallest_matching_nodes(node, predicate):
    if getattr(node, "tag", "") in {"script", "style", "noscript"}:
        return []
    child_matches = []
    for kind, value in node.events:
        if kind == "node" and getattr(value, "tag", "") not in {"script", "style", "noscript"} and predicate(node_text(value)):
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
        # The nearest one-image wrapper is still safe even if it contains the
        # side label. Keep it, then stop climbing so no opposite-side/card data
        # can enter the quantity search.
        wrapper = ancestor
        if _side_marker_count(node_text(ancestor)):
            break
        ancestor = ancestor.parent
    return wrapper


def _structured_quantity_from_nodes(image_node, wrapper, segment_nodes=()):
    """Read data-* counts only from the image ancestry and its own DOM segment."""
    nodes = [image_node]
    current = image_node.parent
    while current is not None:
        nodes.append(current)
        if current is wrapper:
            break
        current = current.parent
    known = {id(node) for node in nodes}
    nodes.extend(node for node in segment_nodes if id(node) not in known)
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


def _has_unreadable_quantity_hint(raw, wrapper=None, segment_nodes=()):
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
    if wrapper is not None or segment_nodes:
        nodes = list(walk_nodes(wrapper)) if wrapper is not None else []
        known = {id(node) for node in nodes}
        nodes.extend(node for node in segment_nodes if id(node) not in known)
        for node in nodes:
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
    """Flatten DOM events while retaining element nodes for segment-scoped metadata."""
    result = []
    def visit(n):
        for kind, value in n.events:
            if kind == "text":
                result.append(("text", value))
            elif value.tag == "img":
                name, _ = split_item_label(value.attrs.get("alt", ""))
                if name:
                    result.append(("img", {"name": name, "node": value}))
                # Coin/decoration images are not item boundaries.
            else:
                result.append(("element", value))
                visit(value)
    visit(node)
    return result


def has_addition_marker(node):
    text = node_text(node).casefold()
    return bool(re.search(r"\bajout(?:s)?\b", text))


def _item_quantity_details(image_node, segment_text, segment_nodes=()):
    wrapper = _item_wrapper(image_node)
    wrapper_text = node_text(wrapper) if wrapper is not None else ""
    alt_name, alt_quantity = split_item_label(image_node.attrs.get("alt", ""))
    wrapper_nodes = list(walk_nodes(wrapper)) if wrapper is not None else []
    structured, invalid_structured = _structured_quantity_from_nodes(
        image_node, wrapper, [*wrapper_nodes, *segment_nodes]
    )
    # The text used to infer a stack is restricted to the image's own segment:
    # after this weapon image and before the next weapon or side marker. Ancestor
    # text is retained only for diagnostic price extraction, never quantity.
    quantity_attribute_nodes = [image_node]
    current = image_node.parent
    while current is not None:
        quantity_attribute_nodes.append(current)
        if current is wrapper:
            break
        current = current.parent
    quantity_attribute_nodes.extend(wrapper_nodes)
    quantity_attribute_nodes.extend(segment_nodes)
    seen_nodes = set()
    quantity_attribute_nodes = [
        node for node in quantity_attribute_nodes
        if not (id(node) in seen_nodes or seen_nodes.add(id(node)))
    ]
    attribute_text = " ".join(
        str(node.attrs.get(key, ""))
        for node in quantity_attribute_nodes
        for key in ("aria-label", "title")
    )
    local_text = " ".join(part for part in (alt_name, segment_text) if part)
    diagnostic_text = " ".join(part for part in (wrapper_text, local_text) if part)
    explicit = _explicit_stack_quantities(local_text + " " + attribute_text)
    semantic_quantities = []
    for node in segment_nodes:
        classes = " ".join((
            node.attrs.get("class", ""), node.attrs.get("id", ""),
            node.attrs.get("data-testid", "")
        )).casefold()
        if re.search(r"quant|count|amount|stack|badge|counter", classes):
            quantity = _quantity_marker(node_text(node))
            if quantity is not None:
                semantic_quantities.append(quantity)
    wrapper_semantic_quantity = _semantic_badge_quantity(wrapper)
    if wrapper_semantic_quantity is not None:
        semantic_quantities.append(wrapper_semantic_quantity)
    semantic_quantity = (
        semantic_quantities[0]
        if semantic_quantities and len(set(semantic_quantities)) == 1
        else None
    )
    unreadable = _has_unreadable_quantity_hint(
        local_text + " " + attribute_text, wrapper, segment_nodes
    )
    if alt_quantity is not None:
        explicit.append(alt_quantity)
    if semantic_quantity is not None:
        explicit.append(semantic_quantity)

    if invalid_structured:
        lower = min(explicit) if explicit else 1
        return None, "uncertain", _listed_price(diagnostic_text), local_text[:240], lower, None, "invalid structured quantity marker"
    if len(set(explicit)) > 1:
        return None, "uncertain", _listed_price(diagnostic_text), local_text[:240], min(explicit), max(explicit), "conflicting explicit quantity markers"
    if unreadable:
        lower = min(explicit) if explicit else 1
        return None, "uncertain", _listed_price(diagnostic_text), local_text[:240], lower, None, "ambiguous quantity marker"
    if structured is not None:
        if explicit and any(value != structured for value in explicit):
            candidates = [structured, *explicit]
            return None, "uncertain", _listed_price(diagnostic_text), local_text[:240], min(candidates), max(candidates), "HTML/structured quantity disagreement"
        return structured, "structured", _listed_price(diagnostic_text), local_text[:240], structured, structured, ""
    if explicit:
        return explicit[0], "explicit", _listed_price(diagnostic_text), local_text[:240], explicit[0], explicit[0], ""
    return 1, "implicit_single", _listed_price(diagnostic_text), local_text[:240], 1, 1, ""

def extract_side_items(node):
    """Extract each weapon's complete DOM segment without crossing item/side boundaries."""
    events = collect_ordered_events(node)
    side = None
    received = []
    requested = []
    prefix_text = []
    prefix_nodes = []
    current = None

    def finish_item():
        nonlocal current
        if current is None:
            return
        quantity, source, price, raw_excerpt, quantity_min, quantity_max, reason = _item_quantity_details(
            current["node"], " ".join(current["text"]), current["nodes"]
        )
        target = received if current["side"] == "receive" else requested
        target.append({
            "name": current["name"],
            "quantity": quantity,
            "quantity_source": source,
            "quantity_min": quantity_min,
            "quantity_max": quantity_max,
            "quantity_reason": reason,
            "listed_price": price,
            "raw_excerpt": raw_excerpt,
        })
        current = None

    for kind, value in events:
        if kind == "text":
            low = value.casefold()
            if re.search(r"\bpropose\b|\boffering\b|\boffer\b", low):
                finish_item()
                side = "receive"
                prefix_text = []
                prefix_nodes = []
            elif re.search(r"\brecherche\b|\blooking\s+for\b|\brequest\b", low):
                finish_item()
                side = "give"
                prefix_text = []
                prefix_nodes = []
            elif side:
                if current is None:
                    prefix_text.append(value)
                else:
                    current["text"].append(value)
            continue

        if kind == "element":
            if not side:
                continue
            image_count = len(collect_images_in_order(value))
            if current is None:
                # Elements before this weapon belong to its prefix only if they
                # contain at most this one weapon; shared multi-item containers
                # are never allowed to donate metadata.
                if image_count <= 1:
                    prefix_nodes.append(value)
            elif image_count == 0:
                current["nodes"].append(value)
            continue

        if kind == "img" and value and side:
            finish_item()
            current = {
                "name": value["name"],
                "node": value["node"],
                "side": side,
                "text": list(prefix_text),
                "nodes": list(prefix_nodes),
            }
            prefix_text = []
            prefix_nodes = []

    finish_item()
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


def legacy_stable_listing_id(owner_url, receive_items, give_items, listing_url="", listing_key_hint=""):
    """Reproduce the pre-ad-ID hash so persisted alerts/quarantines migrate safely."""
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
        listing_key = sorted(ad_values)
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


def legacy_alert_state(listing, alerted_ids, uncertain_ids):
    """Return the old hash's persisted delivery state for one-time migration."""
    legacy_id = str(listing.get("legacy_id") or "")
    trade_id = str(listing.get("id") or "")
    if not legacy_id or legacy_id == trade_id:
        return ""
    if legacy_id in alerted_ids:
        return "alerted"
    if legacy_id in uncertain_ids:
        return "uncertain"
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
        # Prefer the source post ID; item contents and quantities can change.
        listing_key = sorted(ad_values)
    elif owner_url:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    else:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    # A source ad ID is the durable identity of a BloxSwaps post. Item
    # quantities and card text can change while the post remains the same;
    # including them here would create a fresh ID and resend an old alert.
    if ad_values or listing_key_hint:
        identity = {"owner": owner_identity, "listing_key": listing_key}
    else:
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


def _match_by_identity(left, right):
    """Match by unique IDs first, then unique normalized names for remaining rows."""
    def item_id(item): return str(item.get('structured_item_id') or '')
    def name_key(item): return normalize_name(str(item.get('name') or ''))
    pairs, used_left, used_right = [], set(), set()
    left_ids, right_ids = {}, {}
    for i, item in enumerate(left):
        value = item_id(item)
        if value: left_ids.setdefault(value, []).append(i)
    for i, item in enumerate(right):
        value = item_id(item)
        if value: right_ids.setdefault(value, []).append(i)
    for value, li in left_ids.items():
        ri = right_ids.get(value, [])
        if len(li) == 1 and len(ri) == 1:
            pairs.append((li[0], ri[0]))
            used_left.add(li[0])
            used_right.add(ri[0])
    left_names, right_names = {}, {}
    for i, item in enumerate(left):
        key = name_key(item)
        if i not in used_left and key: left_names.setdefault(key, []).append(i)
    for i, item in enumerate(right):
        key = name_key(item)
        if i not in used_right and key: right_names.setdefault(key, []).append(i)
    for key, li in left_names.items():
        ri = right_names.get(key, [])
        if len(li) == 1 and len(ri) == 1:
            pairs.append((li[0], ri[0]))
            used_left.add(li[0])
            used_right.add(ri[0])
    return pairs

def _uncertain_quantity(item, reason):
    item['quantity'] = None
    item['quantity_source'] = 'uncertain'
    item['quantity_min'] = item.get('quantity_min') or 1
    item['quantity_max'] = None
    item['quantity_reason'] = reason

def _apply_structured_quantities(listing, structured, item_map=None):
    trade_items = structured.get('tradePostItems')
    if not isinstance(trade_items, list): return False
    for field, role in (('receive_items', 'OFFERED'), ('give_items', 'REQUESTED')):
        rows = [row for row in trade_items if isinstance(row, dict) and str(row.get('tradeRole', '')).upper() == role]
        items = listing.get(field, [])
        row_items = []
        for row in rows:
            item_id = str(row.get('itemDetailsId') or '')
            details = (item_map or {}).get(item_id) or {}
            row_items.append({'name': str(details.get('displayName') or row.get('displayName') or row.get('name') or '')})
        pairs = _match_by_identity(items, row_items)
        matched_items = {i for i, _ in pairs}
        if len(pairs) != len(items) or len(pairs) != len(rows):
            for i, item in enumerate(items):
                if i not in matched_items: _uncertain_quantity(item, 'RSC/HTML item identity is missing or ambiguous')
        for item_i, row_i in pairs:
            item, row = items[item_i], rows[row_i]
            item['structured_item_id'] = str(row.get('itemDetailsId') or '')
            html_source, html_quantity = item.get('quantity_source', 'implicit_single'), item.get('quantity')
            item['html_quantity'], item['html_quantity_source'] = html_quantity, html_source
            try: quantity = int(row.get('quantity'))
            except (TypeError, ValueError): quantity = None
            if quantity is None or not 1 <= quantity <= 100:
                _uncertain_quantity(item, 'invalid structured quantity')
            elif html_source == 'uncertain':
                _uncertain_quantity(item, 'HTML quantity marker is ambiguous; structured data cannot override the conflict')
            elif html_quantity != quantity:
                known = [int(v) for v in (html_quantity, quantity) if v is not None and str(v).isdigit() and 1 <= int(v) <= 100]
                _uncertain_quantity(item, f'HTML ({html_source}) says {html_quantity}; structured data says {quantity}')
                item['quantity_min'] = min(known) if known else 1
                item['quantity_max'] = max(known) if known else None
            else:
                item.update({'quantity': quantity, 'quantity_source': 'structured', 'quantity_min': quantity, 'quantity_max': quantity, 'quantity_reason': ''})
    listing['structured_trade_post_id'] = str(structured.get('id', ''))
    listing['requested_wildcards'] = list(structured.get('requestedWildcards') or [])
    return True

def parse_page(html, item_map=None):
    parser = PageTree()
    parser.feed(html)
    candidates = smallest_matching_nodes(parser.root, has_trade_sides)
    structured_entries = parse_structured_trade_data(html)
    listings = []
    additions = 0
    addition_ids = []
    malformed = 0
    malformed_samples = []
    structured_matched = 0
    for node in candidates:
        context = listing_context(node)
        text = node_text(context)
        link, owner_url = listing_links(context)
        source_ad_id = _ad_id_from_url(link) or listing_identifier(context)
        structured = structured_entries.get(source_ad_id)
        structured_add = bool(
            structured and any(
                str(value).casefold() in {"add", "ajout", "ajouts"}
                for value in (structured.get("requestedWildcards") or [])
            )
        )
        # Check the single-card context before attempting side parsing. Never
        # inspect the whole feed container for an Ajout marker.
        if has_addition_marker(context) or structured_add:
            additions += 1
            if source_ad_id:
                addition_ids.append(source_ad_id)
            continue
        received, requested = extract_side_items(node)
        if not received or not requested:
            malformed += 1
            malformed_samples.append(text[:240])
            continue
        listing = {
            "id": stable_listing_id(owner_url, received, requested, link, listing_identifier(context)),
            "legacy_id": legacy_stable_listing_id(owner_url, received, requested, link, listing_identifier(context)),
            "receive_items": received,
            "give_items": requested,
            "url": link,
            "owner_url": owner_url,
            "source_text": text[:700],
            "source_ad_id": source_ad_id,
        }
        if structured and _apply_structured_quantities(listing, structured, item_map):
            structured_matched += 1
        listings.append(listing)
    unique = {item["id"]: item for item in listings}
    parsed = list(unique.values())
    return parsed, {
        "candidate_count": len(candidates),
        "addition_count": additions,
        "addition_ids": addition_ids,
        "malformed_count": malformed,
        "malformed_samples": malformed_samples[:5],
        "owner_link_count": sum(1 for item in parsed if item.get("owner_url")),
        "structured_entry_count": len(structured_entries),
        "structured_matched_count": structured_matched,
        "structured_unmatched_count": max(0, len(structured_entries) - structured_matched),
    }


def deduplicate_listings(listings):
    """Keep the first parsed instance of each stable listing ID in one feed batch."""
    unique = []
    seen = set()
    for listing in listings:
        trade_id = str(listing.get("id") or "")
        if not trade_id:
            raise RuntimeError("[parser] Listing has no stable ID; refusing to evaluate it.")
        if trade_id in seen:
            continue
        seen.add(trade_id)
        unique.append(listing)
    return unique


def parse_listings(html):
    return parse_page(html)[0]


def request_with_retry(req, timeout, label, attempts=4, return_headers=False, allow_not_modified=False):
    """Retry transient failures with exponential backoff and respect Retry-After."""
    is_rblxvalue = label == "RBLXValue API"
    for attempt in range(attempts):
        if is_rblxvalue:
            rblxvalue_guard.reserve_request(key=RBLXVALUE_API_KEY)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
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
                rblxvalue_guard.raise_for_key_error(exc.code, detail, key=RBLXVALUE_API_KEY)
            if exc.code == 403:
                safe_detail = redact(detail).strip()
                if safe_detail.startswith("<"):
                    safe_detail = "[HTML response omitted; inspect response headers in diagnostics]"
                if not safe_detail:
                    safe_detail = "empty response body"
                raise RuntimeError(
                    f"[http] {label}: HTTP 403 Forbidden; upstream response: {safe_detail[:240]}"
                ) from exc
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == attempts - 1:
                raise RuntimeError(f"{label}: HTTP {exc.code}: {detail[:200]}") from exc
            delay = min(30, 2 ** (attempt + 1))
            retry_after = exc.headers.get("Retry-After", "")
            if retry_after:
                try:
                    delay = max(0, int(retry_after))
                except ValueError:
                    try:
                        delay = max(0, (parsedate_to_datetime(retry_after) -
                            datetime.now(timezone.utc)).total_seconds())
                    except Exception:
                        pass
            delay = rblxvalue_guard.jittered_delay(delay)
            print(f"[retry] {label}: HTTP {exc.code}; waiting {delay:.2f}s")
            time.sleep(delay)
        except (URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"{label}: network failure after {attempts} attempts: {exc}") from exc
            delay = rblxvalue_guard.jittered_delay(min(30, 2 ** (attempt + 1)))
            print(f"[retry] {label}: network error; waiting {delay:.2f}s")
            time.sleep(delay)


def http_get(url, timeout=35):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    body = request_with_retry(req, timeout, "BloxSwaps page")
    html = body.decode("utf-8", errors="replace")
    sample = html[:12000].casefold()
    if any(marker in sample for marker in (
        "cf-chl-", "challenge-platform", "attention required! | cloudflare",
        "just a moment...", "captcha-container",
    )):
        raise RuntimeError("[cloudflare] BloxSwaps returned a challenge page instead of trade listings.")
    return html

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


def fetch_bloxswaps_items_map(force_refresh=False):
    global _LAST_ITEMS_FORCE_REFRESH_AT
    force_allowed = True
    if force_refresh:
        now = time.time()
        # A single catalogue refresh is enough to resolve newly introduced IDs.
        # Re-fetching every 60 seconds caused several identical ~50s downloads per
        # scan session. Keep the cooldown for ten minutes across scan cycles.
        force_allowed = now - _LAST_ITEMS_FORCE_REFRESH_AT >= 600
        if force_allowed:
            _LAST_ITEMS_FORCE_REFRESH_AT = now
        else:
            print("[bloxswaps] Unknown item ID refresh is cooling down (10 minutes); retaining safe unmatched status")
    if BLOXSWAPS_ITEMS_FILE.exists():
        try:
            cache = json.loads(BLOXSWAPS_ITEMS_FILE.read_text(encoding="utf-8"))
            age = max(0, int(time.time() - float(cache.get("fetched_at", 0))))
            cached = cache.get("items")
            if isinstance(cached, dict) and cached and force_refresh and not force_allowed:
                print(f"[bloxswaps] Catalogue force-refresh suppressed by 10-minute cooldown; reusing {len(cached)} cached names")
                return cached
            if not force_refresh and age <= BLOXSWAPS_ITEMS_TTL_SECONDS and isinstance(cached, dict) and cached:
                print(f"[bloxswaps] Using fresh item catalogue ({len(cached)} names; age {age}s)")
                return cached
            if force_refresh and not force_allowed:
                raise RuntimeError("[bloxswaps] Catalogue refresh is rate-limited and no cached catalogue is available.")
        except (OSError, ValueError, TypeError):
            print("[bloxswaps] Item catalogue cache is unreadable; attempting live refresh")
    if force_refresh and not force_allowed:
        raise RuntimeError("[bloxswaps] Catalogue refresh is rate-limited and no cached catalogue is available.")
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
    BLOXSWAPS_ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(BLOXSWAPS_ITEMS_FILE, json.dumps({
        "fetched_at": time.time(), "items": mapping,
    }, ensure_ascii=False) + "\n")
    print(f"[bloxswaps] Refreshed {len(mapping)} item-name mappings from site catalogue")
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
        name = str(details.get("displayName")) if details else str(
            row.get("displayName") or row.get("name") or f"Unknown item ID {item_id or 'missing'}"
        )
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
            "catalog_slug": str(details.get("slug") or "") if details else "",
            "catalog_rarity": str(details.get("rarity") or "") if details else "",
            "catalog_type": str(details.get("type") or "") if details else "",
            "catalog_chroma": bool(details.get("chroma")) if details else False,
            "catalog_year": details.get("year") if details else None,
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
    listing["legacy_id"] = legacy_stable_listing_id(owner_url, receive, give, listing_url, "")
    return listing


def _merge_html_quantity_diagnostics(api_listing, html_listing):
    """Cross-check quantities only after identity matching; ambiguous pairs fail closed."""
    api_listing['id'] = html_listing.get('id') or api_listing['id']
    api_listing['legacy_id'] = html_listing.get('legacy_id') or api_listing.get('legacy_id')
    # BloxSwaps profile URLs use the site's profile ID, while the listing query
    # uses robloxUserId. The HTML owner link is the canonical profile destination.
    api_listing['owner_url'] = html_listing.get('owner_url') or api_listing.get('owner_url')
    api_listing['source_text'] = html_listing.get('source_text', api_listing.get('source_text', ''))
    for field in ('receive_items', 'give_items'):
        api_items, html_items = api_listing.get(field, []), html_listing.get(field, [])
        pairs = _match_by_identity(api_items, html_items)
        matched = {i for i, _ in pairs}
        for i, item in enumerate(api_items):
            if i not in matched: _uncertain_quantity(item, 'HTML/RSC item identity is missing or ambiguous')
        for api_i, html_i in pairs:
            api_item, html_item = api_items[api_i], html_items[html_i]
            api_item['listed_price'] = html_item.get('listed_price')
            api_item['raw_excerpt'] = html_item.get('raw_excerpt', api_item.get('raw_excerpt', ''))
            html_source = html_item.get('html_quantity_source', html_item.get('quantity_source', 'implicit_single'))
            html_quantity, api_quantity = html_item.get('html_quantity', html_item.get('quantity')), api_item.get('quantity')
            api_item['structured_quantity'] = api_quantity
            api_item['html_quantity'], api_item['html_quantity_source'] = html_quantity, html_source
            if html_source == 'uncertain':
                _uncertain_quantity(api_item, html_item.get('quantity_reason', 'ambiguous HTML quantity'))
                api_item['quantity_min'], api_item['quantity_max'] = html_item.get('quantity_min', 1), html_item.get('quantity_max')
            elif html_quantity != api_quantity:
                known = [int(v) for v in (html_quantity, api_quantity) if v is not None and str(v).isdigit() and 1 <= int(v) <= 100]
                _uncertain_quantity(api_item, f'HTML quantity {html_quantity} disagrees with RSC quantity {api_quantity}')
                api_item['quantity_min'] = min(known) if known else 1
                api_item['quantity_max'] = max(known) if known else None
    return api_listing

def _parse_feed_timestamp(raw):
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _last_successful_scan_timestamp():
    if not LAST_SUCCESS_FILE.exists():
        return None
    try:
        state = json.loads(LAST_SUCCESS_FILE.read_text(encoding="utf-8"))
        return _parse_feed_timestamp(state.get("completed_at_utc"))
    except (OSError, ValueError, TypeError):
        raise RuntimeError("[state] Last-success checkpoint is unreadable; refusing unsafe pagination boundary.")


def fetch_bloxswaps_all(seen_ad_ids=None, pending_ad_ids=None, watch_ad_ids=None):
    """Fetch recent RSC/API posts and recheck unresolved IDs beyond seen posts."""
    if seen_ad_ids is None and pending_ad_ids:
        seen_ad_ids = set()
    html = fetch_bloxswaps()
    initial_page = parse_structured_initial_page(html)
    structured_rows = [
        row for post in (initial_page.get("items") or []) if isinstance(post, dict)
        for row in (post.get("tradePostItems") or []) if isinstance(row, dict)
    ] if initial_page else []
    needs_catalogue = any(
        row.get("itemDetailsId")
        for row in structured_rows
    )
    initial_item_map = fetch_bloxswaps_items_map() if needs_catalogue else {}
    raw_first_items = initial_page.get("items") or [] if initial_page else []
    first_page_unknown_ids = {
        str(row.get("itemDetailsId") or "")
        for post in raw_first_items if isinstance(post, dict)
        for row in (post.get("tradePostItems") or []) if isinstance(row, dict)
        if row.get("itemDetailsId")
        and str(row.get("itemDetailsId")) not in initial_item_map
    }
    if first_page_unknown_ids:
        try:
            refreshed_map = fetch_bloxswaps_items_map(force_refresh=True)
            if refreshed_map:
                initial_item_map = refreshed_map
            still_unknown = sorted(first_page_unknown_ids - set(initial_item_map.keys()))
            if still_unknown:
                send_maintenance_alert(
                    "unknown-item-id-catalogue",
                    "BloxSwaps returned itemDetailsId values absent from its catalogue: "
                    + ", ".join(still_unknown[:12])
                    + ". No item names or values were guessed; affected posts remain eligible for recheck.",
                    cooldown_seconds=3600,
                )
        except Exception as exc:
            send_maintenance_alert(
                "unknown-item-id-catalogue-refresh-failed",
                "Could not refresh the BloxSwaps item catalogue after an unknown itemDetailsId. "
                f"Refresh failed safely ({type(exc).__name__}); no values were guessed.",
                cooldown_seconds=3600,
            )
    first_listings, stats = parse_page(html, initial_item_map)
    if not initial_page:
        stats.update({
            "pagination_pages": 1, "pagination_complete": False,
            "pagination_source": "html-only", "addition_ids": [],
            "unparsed_ad_ids": [], "html_parse_warning_ids": [],
        })
        send_maintenance_alert(
            "rsc-data-missing",
            "BloxSwaps page is HTML-only: RSC initialPage/cursor data is missing. "
            "The scan is treated as failed; no partial feed is trusted.",
            cooldown_seconds=900,
        )
        raise RuntimeError("[bloxswaps] RSC initialPage/cursor data missing; refusing a partial HTML-only scan.")

    if seen_ad_ids is None:
        # Cold start: evaluate verifiably recent posts across cursor pages, then
        # seed older posts. Undated and pinned posts never advance the age boundary.
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - 3600
        seed_ids, recent_ids, recent_listings = [], [], []
        addition_ids, unparsed_ids = [], []
        additions = malformed = candidate_count = api_posts = 0
        old_streak = page_count = 1
        age_boundary = False
        complete = False
        seen_raw_ids = set()
        page_items = raw_first_items
        cursor = (initial_page.get('meta') or {}).get('cursor')
        by_ad_id = {str(item.get('source_ad_id')): item for item in first_listings if item.get('source_ad_id')}

        def refresh_catalogue_for_post(post):
            nonlocal initial_item_map
            unknown_ids = {
                str(row.get('itemDetailsId') or '')
                for row in (post.get('tradePostItems') or []) if isinstance(row, dict)
                if row.get('itemDetailsId') and str(row.get('itemDetailsId')) not in initial_item_map
            }
            if not unknown_ids: return
            try:
                refreshed_map = fetch_bloxswaps_items_map(force_refresh=True)
                if refreshed_map: initial_item_map = refreshed_map
                still_unknown = sorted(unknown_ids - set(initial_item_map.keys()))
                if still_unknown:
                    send_maintenance_alert(
                        'unknown-item-id-catalogue',
                        'BloxSwaps returned itemDetailsId values absent from its catalogue: '
                        + ', '.join(still_unknown[:12])
                        + '. No item names or values were guessed; affected posts remain eligible for recheck.',
                        cooldown_seconds=3600,
                    )
            except Exception as exc:
                send_maintenance_alert(
                    'unknown-item-id-catalogue-refresh-failed',
                    'Could not refresh the BloxSwaps item catalogue after an unknown itemDetailsId. '
                    f'Refresh failed safely ({type(exc).__name__}); no values were guessed.',
                    cooldown_seconds=3600,
                )

        while True:
            for post in page_items:
                if not isinstance(post, dict) or not post.get('id'):
                    malformed += 1
                    continue
                post_id = str(post['id'])
                if post_id in seen_raw_ids: continue
                seen_raw_ids.add(post_id)
                refreshed_ts = _parse_feed_timestamp(
                    post.get('lastRefreshedAt') or post.get('createdAt') or post.get('created_at')
                )
                is_pinned = bool(post.get('isPinned') or post.get('pinned'))
                if refreshed_ts is None:
                    seed_ids.append(post_id)
                    old_streak = 0
                    continue
                if refreshed_ts < cutoff:
                    seed_ids.append(post_id)
                    old_streak = 0 if is_pinned else old_streak + 1
                    if old_streak >= 25:
                        age_boundary = True
                        complete = True
                        break
                    continue
                old_streak = 0
                recent_ids.append(post_id)
                candidate_count += 1
                wildcards = post.get('requestedWildcards') or []
                if any(str(value).casefold() in {'add', 'ajout', 'ajouts'} for value in wildcards):
                    additions += 1
                    addition_ids.append(post_id)
                    continue
                refresh_catalogue_for_post(post)
                listing = _api_trade_post_to_listing(post, initial_item_map)
                if listing:
                    html_listing = by_ad_id.get(post_id)
                    if html_listing: listing = _merge_html_quantity_diagnostics(listing, html_listing)
                    recent_listings.append(listing)
                else:
                    malformed += 1
                    unparsed_ids.append(post_id)
            if age_boundary:
                break
            if not cursor:
                complete = True
                break
            if page_count >= MAX_FEED_PAGES:
                complete = False
                break
            page = bloxswaps_api_get_json('/trade-posts', {'cursor': cursor, 'pageSize': BLOXSWAPS_PAGE_SIZE})
            if not isinstance(page, dict) or not isinstance(page.get('items'), list):
                raise RuntimeError('[bloxswaps] Cursor API returned an invalid page during cold-start discovery.')
            page_items = page.get('items') or []
            api_posts += len(page_items)
            page_count += 1
            next_cursor = (page.get('meta') or {}).get('cursor')
            if next_cursor and next_cursor == cursor:
                raise RuntimeError('[bloxswaps] Cursor did not advance during cold-start discovery; refusing to seed an incomplete feed.')
            cursor = next_cursor

        stats.update({
            'pagination_pages': page_count,
            'pagination_api_posts': api_posts,
            'pagination_complete': complete,
            'pagination_source': 'rsc-cold-start',
            'seen_boundary_reached': age_boundary,
            'seen_streak': old_streak,
            'cold_start': True,
            'cold_start_seed_ids': seed_ids,
            'cold_start_recent_ids': recent_ids,
            'candidate_count': candidate_count,
            'addition_count': additions,
            'malformed_count': malformed,
            'addition_ids': addition_ids,
            'unparsed_ad_ids': unparsed_ids,
            'pending_remaining_ids': [],
            'watch_remaining_ids': [],
            'owner_link_count': sum(1 for item in recent_listings if item.get('owner_url')),
        })
        discovered = seed_ids + recent_ids
        return recent_listings, stats, discovered

    seen = set(str(value) for value in seen_ad_ids)
    pending_remaining = set(str(value) for value in (pending_ad_ids or []))
    watch_ids = set(str(value) for value in (watch_ad_ids or []))
    watch_remaining = set(watch_ids)
    pending_found_ids = []
    by_ad_id = {
        str(listing.get("source_ad_id")): listing
        for listing in first_listings if listing.get("source_ad_id")
    }
    html_addition_ids = set(str(value) for value in stats.get("addition_ids", []))
    collected = []
    discovered_ids = []
    addition_ids = []
    unparsed_ids = []
    unparsed_samples = []
    html_parse_warning_ids = []
    boundary_reached = False
    seen_streak = 0
    seen_ids_skipped = []
    last_success_ts = _last_successful_scan_timestamp()
    additions = 0
    malformed = 0
    api_listings_added = 0
    api_posts_scanned = 0
    candidate_count = 0
    item_map = initial_item_map

    def consume_raw_posts(raw_posts, compare_html=False):
        nonlocal boundary_reached, seen_streak, additions, malformed, api_listings_added, candidate_count, item_map, watch_remaining
        for raw in raw_posts:
            if not isinstance(raw, dict) or not raw.get("id"):
                malformed += 1
                continue
            post_id = str(raw["id"])
            refreshed_ts = _parse_feed_timestamp(
                raw.get("lastRefreshedAt") or raw.get("createdAt") or raw.get("created_at")
            )
            recently_refreshed = (
                refreshed_ts is not None and last_success_ts is not None
                and refreshed_ts >= last_success_ts - 90
            )
            is_pinned = bool(raw.get("isPinned") or raw.get("pinned"))
            if post_id in watch_remaining:
                watch_remaining.discard(post_id)
            if boundary_reached and post_id not in pending_remaining and post_id not in watch_ids and not recently_refreshed:
                # Once the new-feed boundary is proven, only inspect the bounded
                # pending/watch backlog. Do not let old unresolved IDs pull the
                # hot path through the entire historical feed.
                continue
            if post_id in seen and post_id not in pending_remaining and post_id not in watch_ids and not recently_refreshed:
                seen_ids_skipped.append(post_id)
                if is_pinned:
                    seen_streak = 0
                    continue
                seen_streak += 1
                # Without a trusted success checkpoint, the seen streak cannot
                # prove that older pages were already covered. Keep paging so a
                # prior capped/incomplete run cannot permanently hide older posts.
                if seen_streak >= 25 and last_success_ts is not None:
                    boundary_reached = True
                continue
            seen_streak = 0
            if post_id in pending_remaining:
                pending_remaining.discard(post_id)
                pending_found_ids.append(post_id)
            if post_id in discovered_ids:
                continue
            discovered_ids.append(post_id)
            candidate_count += 1
            wildcards = raw.get("requestedWildcards") or []
            if post_id in html_addition_ids or any(str(value).casefold() in {"add", "ajout", "ajouts"} for value in wildcards):
                additions += 1
                addition_ids.append(post_id)
                continue
            if item_map is None:
                needs_catalogue = any(
                    isinstance(row, dict) and row.get("itemDetailsId")
                    and not (row.get("displayName") or row.get("name"))
                    for row in (raw.get("tradePostItems") or [])
                )
                item_map = fetch_bloxswaps_items_map() if needs_catalogue else {}
            unknown_ids = {
                str(row.get("itemDetailsId") or "")
                for row in (raw.get("tradePostItems") or [])
                if isinstance(row, dict) and row.get("itemDetailsId")
                and str(row.get("itemDetailsId")) not in item_map
            }
            if unknown_ids:
                try:
                    refreshed_map = fetch_bloxswaps_items_map(force_refresh=True)
                    if refreshed_map:
                        item_map = refreshed_map
                    still_unknown = sorted(unknown_ids - set(item_map.keys()))
                    if still_unknown:
                        send_maintenance_alert(
                            "unknown-item-id-catalogue",
                            "BloxSwaps returned itemDetailsId values absent from its catalogue: "
                            + ", ".join(sorted(unknown_ids)[:12])
                            + ". No item names or values were guessed; affected posts remain eligible for recheck.",
                            cooldown_seconds=3600,
                        )
                except Exception as exc:
                    send_maintenance_alert(
                        "unknown-item-id-catalogue-refresh-failed",
                        "Could not refresh the BloxSwaps item catalogue after an unknown itemDetailsId. "
                        f"Refresh failed safely ({type(exc).__name__}); no values were guessed.",
                        cooldown_seconds=3600,
                    )
            listing = _api_trade_post_to_listing(raw, item_map)
            if listing and compare_html:
                html_listing = by_ad_id.get(post_id)
                if html_listing:
                    listing = _merge_html_quantity_diagnostics(listing, html_listing)
                else:
                    listing["html_card_parse_warning"] = True
                    html_parse_warning_ids.append(post_id)
            if listing:
                collected.append(listing)
                if not compare_html:
                    api_listings_added += 1
            else:
                malformed += 1
                unparsed_ids.append(post_id)
                unparsed_samples.append(json.dumps(raw, ensure_ascii=False)[:240])

    consume_raw_posts(raw_first_items, compare_html=True)
    cursor = (initial_page.get("meta") or {}).get("cursor")
    pages_scanned = 1
    seen_cursors = {str(cursor)} if cursor else set()
    backlog_pages_scanned = 0
    backlog_page_limit_reached = False
    if cursor:
        while cursor and pages_scanned < MAX_FEED_PAGES:
            if boundary_reached:
                if not (pending_remaining or watch_remaining):
                    break
                if backlog_pages_scanned >= MAX_BACKLOG_RECHECK_PAGES:
                    backlog_page_limit_reached = True
                    break
            payload = bloxswaps_api_get_json(
                "/trade-posts", {"cursor": cursor, "pageSize": BLOXSWAPS_PAGE_SIZE}
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise RuntimeError("[bloxswaps] Cursor API returned an invalid page structure.")
            page_items = payload["items"]
            api_posts_scanned += len(page_items)
            boundary_before_page = boundary_reached
            pages_scanned += 1
            if boundary_before_page:
                backlog_pages_scanned += 1
            consume_raw_posts(page_items, compare_html=False)
            if boundary_reached and not (pending_remaining or watch_remaining):
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
    watch_search_complete = not watch_remaining or not cursor
    unique = {}
    for listing in collected:
        unique[listing["id"]] = listing
    result = list(unique.values())
    html_malformed_count = int(stats.get("malformed_count", 0))
    stats.update({
        "candidate_count": candidate_count,
        "addition_count": additions,
        "malformed_count": malformed,
        "html_malformed_count": html_malformed_count,
        "html_parse_warning_count": len(html_parse_warning_ids),
        "html_parse_warning_ids": html_parse_warning_ids,
        "owner_link_count": sum(1 for item in result if item.get("owner_url")),
        "pagination_pages": pages_scanned,
        "pagination_api_posts": api_posts_scanned,
        "pagination_complete": pagination_complete,
        "pagination_source": "RSC + cursor API",
        "seen_boundary_reached": boundary_reached,
        "seen_streak": seen_streak,
        "seen_ids_skipped": seen_ids_skipped,
        "watch_remaining_ids": sorted(watch_remaining),
        "pending_found_ids": pending_found_ids,
        "pending_remaining_ids": sorted(pending_remaining),
        "pending_complete": not pending_remaining,
        "watch_search_complete": watch_search_complete,
        "backlog_pages_scanned": backlog_pages_scanned,
        "backlog_page_limit_reached": backlog_page_limit_reached,
        "api_listings_added": api_listings_added,
        "addition_ids": addition_ids,
        "unparsed_ad_ids": unparsed_ids,
        "malformed_samples": (stats.get("malformed_samples", []) + unparsed_samples)[:5],
    })
    if not pagination_complete:
        print(f"[maintenance] Pagination stopped at MAX_FEED_PAGES={MAX_FEED_PAGES} with a cursor remaining.")
    return result, stats, discovered_ids


def normalize_name(name):
    value = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    value = value.casefold().replace("&", "and")
    value = value.replace("'", "").replace("’", "")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalize_slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


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


def _iter_value_variants(values):
    for parent_key, record in values.items():
        if not isinstance(record, dict):
            continue
        variants = record.get("variants")
        if isinstance(variants, list) and variants:
            for variant in variants:
                if isinstance(variant, dict):
                    yield parent_key, record, variant
        else:
            yield parent_key, record, record


def _catalog_value_metadata(item):
    if not isinstance(item, dict):
        return "", "", "", "", None, None
    # Only structured catalogue fields are trusted. HTML parser fields such as
    # "type" or "slug" are presentation hints, not canonical item identity.
    slug = normalize_slug(item.get("catalog_slug") or "")
    rarity = str(item.get("catalog_rarity") or "").strip().casefold()
    raw_chroma = item.get("catalog_chroma")
    if raw_chroma is None:
        chroma = None
    else:
        chroma = raw_chroma is True or str(raw_chroma).casefold() in {"true", "1", "yes"}
    item_type = str(item.get("catalog_type") or "").strip().casefold()
    raw_year = item.get("catalog_year")
    try:
        year = int(raw_year)
        if not 2000 <= year <= 2100:
            year = None
    except (TypeError, ValueError):
        year = None
    return slug, rarity, item_type, normalize_name(item.get("name") or ""), year, chroma


def _filter_variants_by_catalog(candidates, item):
    slug, rarity, item_type, _, year, chroma = _catalog_value_metadata(item)
    if slug:
        slug_matches = [row for row in candidates if normalize_slug(row[2].get("slug")) == slug]
        if slug_matches:
            candidates = slug_matches
    if rarity:
        candidates = [row for row in candidates if str(row[2].get("category") or "").strip().casefold() == rarity]
    if item_type:
        candidates = [row for row in candidates if str(row[2].get("type") or "").strip().casefold() == item_type]
    if chroma is not None:
        def is_chroma_variant(row):
            name = normalize_name(row[2].get("name") or "")
            slug = normalize_slug(row[2].get("slug") or "")
            return name.startswith("chroma ") or slug.startswith("chroma-")
        has_chroma_metadata = any(is_chroma_variant(row) for row in candidates)
        chroma_matches = [row for row in candidates if is_chroma_variant(row) is chroma]
        # Apply chroma identity only when the Supreme catalogue exposes it in
        # either the canonical name or slug. A conflicting explicit flag must
        # not silently select the opposite variant.
        if has_chroma_metadata:
            candidates = chroma_matches
    if year is not None:
        year_pattern = re.compile(rf"(?<!\\d){year}(?!\\d)")
        year_matches = [
            row for row in candidates
            if year_pattern.search(str(row[2].get("name") or ""))
            or year_pattern.search(str(row[2].get("slug") or ""))
        ]
        # A year is a disambiguator only when the source catalogue year is
        # explicitly represented by a Supreme variant. Never guess a nearby year.
        if year_matches:
            candidates = year_matches
    return candidates


def _record_for_variant(variant):
    selected = dict(variant)
    selected["variants"] = [dict(variant)]
    return selected


def resolve_value_record(name, values, aliases=None, item=None):
    """Resolve by exact source slug/name, catalogue identity or reviewed alias; never fuzzy-guess."""
    if isinstance(item, dict) and item.get("unmatched_item_id"):
        return None, "unmatched", []
    name_key = normalize_name(name)
    all_variants = list(_iter_value_variants(values))
    metadata_item = dict(item) if isinstance(item, dict) else {}
    metadata_item["name"] = name
    catalog_slug, catalog_rarity, catalog_type, _, _, _ = _catalog_value_metadata(metadata_item)

    if catalog_slug:
        slug_candidates = [row for row in all_variants if normalize_slug(row[2].get("slug")) == catalog_slug]
        if slug_candidates:
            filtered = _filter_variants_by_catalog(slug_candidates, metadata_item)
            if len(filtered) == 1:
                return _record_for_variant(filtered[0][2]), "slug", []
            unresolved = filtered or slug_candidates
            return None, "ambiguous", [str(row[2].get("name") or row[0]) for row in unresolved[:8]]

    candidates = [
        row for row in all_variants
        if normalize_name(row[2].get("name") or row[0]) == name_key
    ]
    source = "exact"
    if not candidates and (catalog_rarity or catalog_type):
        candidates = [
            row for row in all_variants
            if normalize_name(row[2].get("name") or row[0]).startswith(name_key + " ")
        ]
        source = "metadata"

    aliases = aliases or {}
    if not candidates:
        canonical = aliases.get(name_key)
        if canonical:
            canonical_key = normalize_name(canonical)
            candidates = [
                row for row in all_variants
                if normalize_name(row[2].get("name") or row[0]) == canonical_key
            ]
            source = "alias"
    if not candidates:
        return None, "unmatched", []

    filtered = _filter_variants_by_catalog(candidates, metadata_item)
    if len(filtered) == 1:
        if len(candidates) > 1 and source == "exact":
            source = "metadata"
        return _record_for_variant(filtered[0][2]), source, []
    unresolved = filtered or candidates
    return None, "ambiguous", [
        str(row[2].get("name") or row[0])
        + (f" [{row[2].get('category')}/{row[2].get('type')}]" if row[2].get("category") or row[2].get("type") else "")
        for row in unresolved[:8]
    ]


def _normalize_stability(value):
    """Preserve the published stability label without inventing a fallback."""
    if not isinstance(value, str):
        return None
    label = " ".join(value.split()).strip()
    if not label or len(label) > 48:
        return None
    return label

# RBLXValue exposes its own stability labels; they are not guaranteed to be a
# verbatim copy of Supreme Values' website taxonomy. Unknown/missing labels are
# never treated as a healthy status for an item we would receive.
RBLXVALUE_STABILITY_LABELS = frozenset({
    "rising", "overpaid for", "doing well", "improving", "fluctuating",
    "receding", "stable", "underpaid for", "decreasing", "unsafe",
    "untradeable", "unstable", "volatile",
})
BLOCKED_RECEIVE_STABILITY = frozenset({
    "decreasing", "unsafe", "untradeable", "unstable", "volatile", "fluctuating",
})


def _optional_bounded_int(value, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _load_rblxvalue_detail_state():
    if not RBLXVALUE_DETAIL_FILE.exists():
        return {"version": 1, "day": "", "daily_lookups": 0, "attempted": {}}
    try:
        state = json.loads(RBLXVALUE_DETAIL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[state] RBLXValue detail-enrichment state is unreadable.") from exc
    if (
        not isinstance(state, dict) or state.get("version") != 1
        or not isinstance(state.get("attempted"), dict)
        or not isinstance(state.get("daily_lookups"), int)
    ):
        raise RuntimeError("[state] RBLXValue detail-enrichment state has an invalid structure.")
    return state


def _save_rblxvalue_detail_state(state):
    atomic_write_text(
        RBLXVALUE_DETAIL_FILE,
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _enrich_catalog_metadata_gaps(values, cache_document, source_meta=None, persist_cache=True):
    """Use the official single-item API endpoint to recover missing type/metadata, budgeted daily."""
    if not RBLXVALUE_API_KEY or not isinstance(values, dict):
        return 0
    source_version = (
        (source_meta or {}).get("last_updated")
        or (cache_document or {}).get("source_last_updated")
        or ""
    )
    if not source_version:
        return 0
    state = _load_rblxvalue_detail_state()
    now = time.time()
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["daily_lookups"] = 0
    candidates = []
    for record in values.values():
        if not isinstance(record, dict):
            continue
        variants = record.get("variants") or [record]
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            missing_type = not str(variant.get("type") or "").strip()
            has_numeric_value = variant.get("value") is not None
            missing_valuable_metadata = has_numeric_value and (
                not variant.get("stability")
                or variant.get("demand") is None
                or variant.get("rarity") is None
            )
            if not missing_type and not missing_valuable_metadata:
                continue
            slug = str(variant.get("slug") or "").strip()
            if not slug:
                continue
            prior = state["attempted"].get(slug, {})
            if isinstance(prior, dict):
                if (
                    prior.get("status") in {"enriched", "no_additional_metadata"}
                    and prior.get("item_last_updated") == variant.get("last_updated")
                ):
                    continue
                if prior.get("source_version") == source_version:
                    status = prior.get("status")
                    retry_after = float(prior.get("retry_after", 0) or 0)
                    if status in {"pending", "error"}:
                        if retry_after > now:
                            continue
                    else:
                        continue
            try:
                value_priority = float(variant.get("value") or 0)
            except (TypeError, ValueError):
                value_priority = 0.0
            priority = 0 if missing_type else 1
            candidates.append((priority, -value_priority, slug, variant))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    remaining = max(
        0, RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY - int(state.get("daily_lookups", 0))
    )
    changed = 0
    for _priority, _value_priority, slug, variant in candidates[:remaining]:
        state["daily_lookups"] += 1
        state["attempted"][slug] = {
            "source_version": source_version,
            "item_last_updated": variant.get("last_updated"),
            "attempted_at": now,
            "retry_after": now + RBLXVALUE_DETAIL_RETRY_SECONDS,
            "status": "pending",
        }
        _save_rblxvalue_detail_state(state)
        try:
            payload = api_get_json(f"/items/{slug}")
            detail = payload.get("item") if isinstance(payload, dict) else None
            if not isinstance(detail, dict):
                raise RuntimeError("detail endpoint returned no item")
            source_id = str(variant.get("id") or "")
            detail_id = str(detail.get("id") or "")
            if source_id and detail_id and source_id != detail_id:
                raise RuntimeError("detail endpoint item ID did not match catalogue identity")
            detail_slug = str(detail.get("slug") or "").strip().casefold()
            if detail_slug and detail_slug != slug.casefold():
                raise RuntimeError("detail endpoint slug did not match catalogue identity")
            before = dict(variant)
            detail_type = str(detail.get("type") or "").strip().casefold()
            if detail_type:
                variant["type"] = detail_type
            if not variant.get("stability") and detail.get("stability"):
                variant["stability"] = _normalize_stability(detail.get("stability"))
            if variant.get("demand") is None:
                variant["demand"] = _optional_bounded_int(detail.get("demand"), 0, 10)
            if variant.get("rarity") is None:
                variant["rarity"] = _optional_bounded_int(detail.get("rarity"), 0, 10)
            if not variant.get("range") and detail.get("range"):
                variant["range"] = str(detail.get("range"))
            for field in ("acronym", "event_name", "description", "image_url", "fandom_url", "last_updated"):
                if not variant.get(field) and detail.get(field) is not None:
                    variant[field] = str(detail.get(field))[:500] if field == "description" else detail.get(field)
            if variant.get("tier") is None:
                variant["tier"] = _optional_bounded_int(detail.get("tier"), 0, 100)
            if variant.get("raw_source_value") is None and detail.get("value_source_b") is not None:
                variant["raw_source_value"] = detail.get("value_source_b")
            if variant.get("value") is None:
                numeric_value, value_status = _supreme_numeric_value(detail)
                if numeric_value is not None:
                    variant["value"] = numeric_value
                    low, high = _parse_numeric_value_range(detail.get("range"), numeric_value)
                    variant["value_min"], variant["value_max"] = low, high
                    variant["value_status"] = value_status
            if variant.get("secondary_value") is None and detail.get("value_source_a") is not None:
                try:
                    secondary = float(detail["value_source_a"])
                    if math.isfinite(secondary) and secondary > 0:
                        variant["secondary_value"] = secondary
                except (TypeError, ValueError):
                    pass
            changed_fields = [key for key in variant if before.get(key) != variant.get(key)]
            state["attempted"][slug]["status"] = "enriched" if changed_fields else "no_additional_metadata"
            state["attempted"][slug]["fields"] = changed_fields
            if changed_fields:
                changed += 1
                print(f"[rblxvalue-detail] Enriched {variant.get('name') or slug}: {', '.join(changed_fields)}")
        except Exception as exc:
            state["attempted"][slug]["status"] = "error"
            state["attempted"][slug]["error"] = type(exc).__name__
            print(f"[rblxvalue-detail] Lookup skipped safely for {slug}: {type(exc).__name__}")
            if rblxvalue_guard.is_key_blocked(key=RBLXVALUE_API_KEY):
                _save_rblxvalue_detail_state(state)
                raise RuntimeError("[rblxvalue-detail] API key became blocked during detail enrichment.") from exc
        _save_rblxvalue_detail_state(state)
    if changed and isinstance(cache_document, dict):
        cache_document["values"] = values
        if persist_cache:
            atomic_write_text(
                VALUES_FILE,
                json.dumps(cache_document, ensure_ascii=False, indent=2) + "\n",
            )
    return changed


def _read_rblxvalue_meta_state():
    try:
        state = json.loads(RBLXVALUE_META_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return state if isinstance(state, dict) else {}


def _fetch_rblxvalue_meta(force=False):
    """Poll source metadata hourly; the upstream dataset itself updates every ~12h."""
    if not RBLXVALUE_META_POLL_ENABLED or not RBLXVALUE_API_KEY:
        return None
    now = time.time()
    state = _read_rblxvalue_meta_state()
    try:
        checked_at = float(state.get("checked_at_epoch", 0))
    except (TypeError, ValueError):
        checked_at = 0
    cached_meta_valid = (
        isinstance(state.get("last_updated"), str)
        and bool(state.get("last_updated").strip())
        and isinstance(state.get("items_total"), int)
        and state.get("items_total", 0) >= 100
    )
    if (
        not force and cached_meta_valid and checked_at > 0
        and 0 <= now - checked_at < RBLXVALUE_META_POLL_INTERVAL_SECONDS
    ):
        return state
    payload = api_get_json("/meta")
    if not isinstance(payload, dict) or not isinstance(payload.get("last_updated"), str):
        raise RuntimeError("[rblxvalue-meta] Missing or invalid last_updated metadata")
    totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    try:
        item_total = int(totals.get("items", 0))
    except (TypeError, ValueError):
        item_total = 0
    if item_total < 100:
        raise RuntimeError("[rblxvalue-meta] Implausible source item total; refusing metadata")
    updated = {
        "schema_version": 1,
        "checked_at_epoch": now,
        "last_updated": payload["last_updated"],
        "items_total": item_total,
        "sets_total": int(totals.get("sets", 0) or 0),
        "update_interval_hours": payload.get("update_interval_hours"),
    }
    atomic_write_text(RBLXVALUE_META_FILE, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return updated


def _supreme_numeric_value(item):
    """Return scalar Supreme value; small Common/Uncommon/Rare xN counts are not scalar values."""
    try:
        value = float(item.get("value_source_b"))
    except (TypeError, ValueError):
        return None, "not_numeric"
    if not math.isfinite(value) or value <= 0:
        return None, "not_listed"
    category = str(item.get("category") or "").strip().casefold()
    if category in {"common", "uncommon", "rare"} and value < 10:
        return None, "tier_item_count"
    return value, "numeric"


def _parse_numeric_value_range(raw_range, base_value):
    if base_value is None:
        return None, None
    if raw_range is None or not str(raw_range).strip():
        return base_value, base_value
    text = str(raw_range).replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return base_value, base_value
    try:
        low, high = float(match.group(1)), float(match.group(2))
    except (TypeError, ValueError):
        return base_value, base_value
    if not all(math.isfinite(v) and v > 0 for v in (low, high)) or low > high:
        return base_value, base_value
    return low, high


def api_get_json(path, params=None):
    if not RBLXVALUE_API_KEY:
        raise RuntimeError("Missing RBLXVALUE_API_KEY GitHub Actions secret.")
    query = "?" + urlencode(params or {}) if params else ""
    url = RBLXVALUE_API_BASE + path + query
    req = urllib.request.Request(url, headers={
        "X-Api-Key": RBLXVALUE_API_KEY,
        "Accept": "application/json",
        "User-Agent": "MM2TradeScanner/2.2 (+https://github.com/hg5911959-hue/mm2-trade-scanner)",
    })
    raw = request_with_retry(req, 30, "RBLXValue API")
    try:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict):
            error_fields = {key: payload.get(key) for key in ("message", "error", "detail", "status", "code")}
            rblxvalue_guard.raise_for_key_error(200, json.dumps(error_fields), key=RBLXVALUE_API_KEY)
        rblxvalue_guard.mark_key_success(key=RBLXVALUE_API_KEY)
        return payload
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("[rblxvalue] Invalid JSON response") from exc


def api_get_json_conditional(path, params=None, etag=None):
    """Fetch one RBLXValue page conditionally; a 304 is safe only with its cached page."""
    if not RBLXVALUE_API_KEY:
        raise RuntimeError("Missing RBLXVALUE_API_KEY GitHub Actions secret.")
    query = "?" + urlencode(params or {}) if params else ""
    url = RBLXVALUE_API_BASE + path + query
    headers = {
        "X-Api-Key": RBLXVALUE_API_KEY,
        "Accept": "application/json",
        "User-Agent": "MM2TradeScanner/2.1",
    }
    if etag:
        headers["If-None-Match"] = str(etag)
    req = urllib.request.Request(url, headers=headers)
    raw, response_headers = request_with_retry(
        req, 30, "RBLXValue API", return_headers=True, allow_not_modified=True
    )
    response_etag = response_headers.get("ETag") or etag
    if raw is None:
        rblxvalue_guard.mark_key_success(key=RBLXVALUE_API_KEY)
        return None, response_etag, True
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("[rblxvalue] Invalid JSON response") from exc
    if isinstance(payload, dict):
        error_fields = {key: payload.get(key) for key in ("message", "error", "detail", "status", "code")}
        rblxvalue_guard.raise_for_key_error(200, json.dumps(error_fields), key=RBLXVALUE_API_KEY)
    rblxvalue_guard.mark_key_success(key=RBLXVALUE_API_KEY)
    return payload, response_etag, False


def _read_values_cache_document():
    """Read v4 snapshots and safely migrate v3 snapshots using their raw item pages."""
    try:
        cached = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
        version = cached.get("schema_version")
        if version not in (3, 4):
            return None
        values = cached.get("values", {})
        if not (
            isinstance(values, dict) and len(values) >= 100
            and all(isinstance(record, dict) and isinstance(record.get("variants"), list) and record["variants"] for record in values.values())
        ):
            return None
        if version == 3:
            by_identity = {}
            for page in (cached.get("pages") or {}).values():
                for item in (page.get("items") or []) if isinstance(page, dict) else []:
                    if not isinstance(item, dict):
                        continue
                    identity = (
                        str(item.get("id") or ""), str(item.get("slug") or ""),
                        str(item.get("category") or "").casefold(),
                        str(item.get("type") or "").casefold(),
                    )
                    by_identity[identity] = item
            migrated = {}
            for name, record in values.items():
                updated = dict(record)
                variants = []
                for variant in record["variants"]:
                    row = dict(variant)
                    identity = (
                        str(row.get("id") or ""), str(row.get("slug") or ""),
                        str(row.get("category") or "").casefold(),
                        str(row.get("type") or "").casefold(),
                    )
                    raw = by_identity.get(identity)
                    row["stability"] = _normalize_stability(raw.get("stability")) if raw else None
                    if raw and not row.get("last_updated"):
                        row["last_updated"] = raw.get("last_updated")
                    variants.append(row)
                updated["variants"] = variants
                if len(variants) == 1:
                    updated["stability"] = variants[0].get("stability")
                migrated[name] = updated
            cached = dict(cached)
            cached["schema_version"] = 4
            cached["values"] = migrated
            atomic_write_text(VALUES_FILE, json.dumps(cached, ensure_ascii=False, indent=2) + "\n")
            print("[supreme-cache] Compatible schema-v3 cache migrated and persisted as schema v4; no API request required")
        return cached
    except (ValueError, OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None


def _read_values_cache():
    cached = _read_values_cache_document()
    if cached is None:
        return None, None
    try:
        age = max(0, int(time.time() - float(cached.get("fetched_at", 0))))
        return cached["values"], age
    except (TypeError, ValueError, KeyError):
        return None, None


def _write_supreme_values_audit(values, fetched_at=None):
    """Persist a complete row-by-row audit of every Supreme source record."""
    rows = []
    counts = {
        "records": 0, "numeric": 0, "non_numeric": 0, "identity_collisions": 0,
        "weapons": 0, "knives": 0, "guns": 0,
        "weapons_missing_stability": 0, "weapons_untradeable": 0,
        "weapons_missing_demand": 0, "weapons_missing_rarity": 0,
        "records_missing_type": 0, "weapon_candidates_unclassified": 0,
    }
    for key, record in sorted((values or {}).items()):
        variants = record.get("variants") if isinstance(record, dict) else None
        variants = variants if isinstance(variants, list) and variants else [record]
        if len(variants) > 1:
            counts["identity_collisions"] += 1
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            counts["records"] += 1
            numeric = variant.get("value")
            is_numeric = isinstance(numeric, (int, float)) and math.isfinite(float(numeric)) and float(numeric) > 0
            counts["numeric" if is_numeric else "non_numeric"] += 1
            item_type = str(variant.get("type") or "").strip().casefold()
            stability = _normalize_stability(variant.get("stability"))
            if not item_type:
                counts["records_missing_type"] += 1
                if str(variant.get("category") or "").strip().casefold() not in {"", "pets", "misc"}:
                    counts["weapon_candidates_unclassified"] += 1
            if item_type in {"knife", "gun"}:
                counts["weapons"] += 1
                counts["knives" if item_type == "knife" else "guns"] += 1
                if not stability:
                    counts["weapons_missing_stability"] += 1
                if stability and stability.casefold() == "untradeable":
                    counts["weapons_untradeable"] += 1
                if variant.get("demand") is None:
                    counts["weapons_missing_demand"] += 1
                if variant.get("rarity") is None:
                    counts["weapons_missing_rarity"] += 1
            rows.append({
                "name": str(variant.get("name") or record.get("name") or key),
                "id": str(variant.get("id") or ""),
                "slug": str(variant.get("slug") or ""),
                "category": str(variant.get("category") or ""),
                "type": str(variant.get("type") or ""),
                "value": numeric if is_numeric else None,
                "value_min": variant.get("value_min"),
                "value_max": variant.get("value_max"),
                "range": str(variant.get("range") or ""),
                "raw_source_value": variant.get("raw_source_value"),
                "value_status": str(variant.get("value_status") or ("numeric" if is_numeric else "not_numeric")),
                "stability": stability,
                "demand": variant.get("demand"),
                "rarity": variant.get("rarity"),
                "acronym": str(variant.get("acronym") or ""),
                "event_name": variant.get("event_name"),
                "tier": variant.get("tier"),
                "last_updated": variant.get("last_updated"),
            })
    VALUES_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(VALUES_AUDIT_FILE, json.dumps({
        "schema_version": 1,
        "source": "RBLXValue API v2 value_source_b (Supreme Values)",
        "fetched_at": fetched_at or time.time(),
        "counts": counts,
        "items": rows,
    }, ensure_ascii=False, indent=2) + "\n")
    print(
        f"[supreme-audit] Complete audit saved: {counts['records']} identities; "
        f"{counts['numeric']} numeric; {counts['non_numeric']} non-numeric; "
        f"{counts['identity_collisions']} same-name collision groups"
    )


def verify_rblxvalue_key_if_due():
    """Verify the configured credential periodically without refreshing the full catalogue."""
    if not RBLXVALUE_API_KEY:
        return False

    now = time.time()
    fingerprint = rblxvalue_guard.key_fingerprint(RBLXVALUE_API_KEY)
    try:
        state = json.loads(RBLXVALUE_KEY_HEALTH_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError, TypeError):
        state = {}
        print("[rblxvalue-key-check] Health state is unreadable; performing a fresh one-request check")

    same_key = state.get("key_fingerprint") == fingerprint
    try:
        last_success = float(state.get("last_success_at_epoch", 0)) if same_key else 0
    except (TypeError, ValueError):
        last_success = 0
    try:
        last_attempt = float(state.get("last_attempt_at_epoch", 0)) if same_key else 0
    except (TypeError, ValueError):
        last_attempt = 0

    # Older health-state versions did not record the API's credit requirement.
    # Refresh once after this schema change, but keep the normal retry cooldown
    # if a previous metadata refresh was already attempted and failed.
    has_credit_metadata = isinstance(state.get("attribution_required"), bool)
    if same_key and not has_credit_metadata:
        if last_attempt <= last_success:
            last_attempt = 0
        last_success = 0

    if last_success > 0 and 0 <= now - last_success < RBLXVALUE_KEY_HEALTH_INTERVAL_SECONDS:
        print(
            f"[rblxvalue-key-check] Last successful check is "
            f"{int(now - last_success)}s old; next check due after "
            f"{RBLXVALUE_KEY_HEALTH_INTERVAL_SECONDS}s"
        )
        return False
    if last_attempt > 0 and 0 <= now - last_attempt < RBLXVALUE_KEY_HEALTH_RETRY_SECONDS:
        print("[rblxvalue-key-check] Previous check was inconclusive; retry cooldown active")
        return False

    # Persist the attempt before network I/O so repeated scan cycles cannot
    # hammer the endpoint when the provider is temporarily unavailable.
    attempt_state = {
        "schema_version": 1,
        "key_fingerprint": fingerprint,
        "last_attempt_at_epoch": now,
        "last_success_at_epoch": last_success or None,
        "attribution_required": state.get("attribution_required") if same_key else None,
    }
    atomic_write_text(
        RBLXVALUE_KEY_HEALTH_FILE,
        json.dumps(attempt_state, ensure_ascii=False, indent=2) + "\n",
    )
    try:
        payload, _etag, not_modified = api_get_json_conditional(
            "/items", {"limit": 1, "offset": 0}
        )
        if (
            not_modified or not isinstance(payload, dict)
            or not isinstance(payload.get("items"), list) or not payload["items"]
        ):
            raise RuntimeError("[rblxvalue-key-check] API returned no valid item payload")
    except rblxvalue_guard.RblxValueKeyBlocked:
        raise
    except Exception as exc:
        print(
            f"[rblxvalue-key-check] INCONCLUSIVE: {type(exc).__name__}; "
            f"fresh Supreme cache remains usable; retry after cooldown"
        )
        return False

    attribution_required = payload.get("credit") == RBLXVALUE_ATTRIBUTION
    atomic_write_text(
        RBLXVALUE_KEY_HEALTH_FILE,
        json.dumps({
            "schema_version": 1,
            "key_fingerprint": fingerprint,
            "last_attempt_at_epoch": now,
            "last_success_at_epoch": now,
            "attribution_required": attribution_required,
        }, ensure_ascii=False, indent=2) + "\n",
    )
    if attribution_required:
        print("[rblxvalue-key-check] PASS: credential accepted; free-plan attribution flag detected")
    else:
        print("[rblxvalue-key-check] PASS: credential accepted; API reports no required credit flag")
    return True


def _source_snapshot_matches_meta(cached_document, source_meta):
    """Require both source timestamp and complete catalogue size to match."""
    return bool(
        isinstance(cached_document, dict)
        and isinstance(source_meta, dict)
        and cached_document.get("source_last_updated") == source_meta.get("last_updated")
        and cached_document.get("source_items_total") == source_meta.get("items_total")
    )


def load_supreme_values():
    # Never spend API budget or perform health checks for a credential already blocked.
    if RBLXVALUE_API_KEY and rblxvalue_guard.is_key_blocked(key=RBLXVALUE_API_KEY):
        raise RuntimeError(
            "[supreme] API key circuit breaker active; refusing stale-cache calculations "
            "[rblxvalue-key-blocked] no request sent"
        )
    # Schema v4 preserves item identity and Supreme stability; older caches lack stability.
    cached_document = _read_values_cache_document()
    cached_values, cached_age = _read_values_cache()
    source_meta = None
    if (
        cached_values is not None and RBLXVALUE_API_KEY
        and cached_age is not None and cached_age <= VALUES_TTL_SECONDS
    ):
        verify_rblxvalue_key_if_due()
        if RBLXVALUE_META_POLL_ENABLED:
            try:
                source_meta = _fetch_rblxvalue_meta()
            except Exception as exc:
                print(f"[rblxvalue-meta] Poll failed safely: {type(exc).__name__}; cached snapshot policy remains in force")
        if source_meta and _source_snapshot_matches_meta(cached_document, source_meta):
            print(
                "[supreme] Source metadata and catalogue size unchanged "
                f"(updated {source_meta.get('last_updated')}; items {source_meta.get('items_total')}); "
                "reusing complete cached catalogue"
            )
            _enrich_catalog_metadata_gaps(cached_values, cached_document, source_meta)
            _write_supreme_values_audit(cached_values, fetched_at=(cached_document or {}).get("fetched_at"))
            return cached_values
        if source_meta and cached_document:
            previous_updated = cached_document.get("source_last_updated") or "unknown"
            previous_total = cached_document.get("source_items_total") or "unknown"
            print(
                "[supreme] Source snapshot mismatch "
                f"(timestamp {previous_updated} -> {source_meta.get('last_updated')}; "
                f"items {previous_total} -> {source_meta.get('items_total')}); refreshing catalogue now"
            )
            cached_age = VALUES_TTL_SECONDS + 1
    if cached_values is not None and cached_age is not None and cached_age <= VALUES_TTL_SECONDS:
        print(f"[supreme] Using fresh source_b snapshot (age {cached_age}s; TTL {VALUES_TTL_SECONDS}s)")
        _enrich_catalog_metadata_gaps(cached_values, cached_document, source_meta)
        _write_supreme_values_audit(cached_values, fetched_at=(cached_document or {}).get("fetched_at"))
        return cached_values
    if not RBLXVALUE_API_KEY:
        raise RuntimeError("[supreme] API key missing and no fresh cached source_b snapshot; refusing stale values.")
    if RBLXVALUE_META_POLL_ENABLED and source_meta is None:
        try:
            source_meta = _fetch_rblxvalue_meta(force=True)
        except Exception as exc:
            raise RuntimeError(
                f"[supreme] Could not verify source version before pagination ({type(exc).__name__}); "
                "refusing an unverified catalogue refresh"
            ) from exc
    if cached_values is not None:
        print(f"[supreme] Cached snapshot expired (age {cached_age}s); conditionally refreshing API")
    cached_pages = cached_document.get("pages", {}) if isinstance(cached_document, dict) else {}
    if not isinstance(cached_pages, dict):
        cached_pages = {}

    all_values, refreshed_pages, numeric_count = {}, {}, 0
    try:
        offset, page_size, total = 0, 100, None
        while total is None or offset < total:
            page_key = str(offset)
            old_page = cached_pages.get(page_key, {})
            old_page = old_page if isinstance(old_page, dict) else {}
            payload, etag, not_modified = api_get_json_conditional(
                "/items", {"limit": page_size, "offset": offset}, old_page.get("etag")
            )
            if not_modified:
                items = old_page.get("items")
                if not isinstance(items, list) or not items:
                    raise RuntimeError(f"[supreme] API returned 304 for offset {offset} without a cached page.")
                reported_total = int(old_page.get("total", len(items)))
                print(f"[supreme] Page offset={offset} unchanged by ETag; reusing {len(items)} cached rows")
            else:
                if not isinstance(payload, dict):
                    raise RuntimeError("[supreme] API returned an invalid page structure.")
                items = payload.get("items")
                if not isinstance(items, list):
                    raise RuntimeError("[supreme] API returned a page without an item list.")
                reported_total = int(payload.get("total", len(items)))
            if reported_total < 0:
                raise RuntimeError("[supreme] API returned a negative item total.")
            if total is not None and reported_total != total:
                raise RuntimeError(
                    f"[supreme] API item total changed during pagination ({total} -> {reported_total}); refusing partial snapshot."
                )
            total = reported_total
            if not items:
                if offset < total:
                    raise RuntimeError(
                        f"[supreme] API returned an empty page at offset {offset} before reported total {total}."
                    )
                break
            if len(items) > page_size or offset + len(items) > total:
                raise RuntimeError(
                    f"[supreme] API page at offset {offset} exceeds the reported pagination boundary ({total})."
                )
            refreshed_pages[page_key] = {"etag": etag, "total": total, "items": items}

            for item in items:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"])
                value, value_status = _supreme_numeric_value(item)
                value_min, value_max = _parse_numeric_value_range(item.get("range"), value)
                secondary = item.get("value_source_a")
                try:
                    secondary = float(secondary) if secondary is not None else None
                    if secondary is not None and (not math.isfinite(secondary) or secondary <= 0):
                        secondary = None
                except (TypeError, ValueError):
                    secondary = None
                category = str(item.get("category") or "").strip().casefold()
                item_type = str(item.get("type") or "").strip().casefold()
                candidate = {
                    "id": str(item.get("id") or ""), "slug": str(item.get("slug") or ""),
                    "name": name, "value": value, "value_min": value_min, "value_max": value_max,
                    "raw_source_value": item.get("value_source_b"), "value_status": value_status,
                    "secondary_value": secondary, "category": category, "type": item_type,
                    # The API does not document stability as source-specific. Preserve the
                    # provider label verbatim; do not claim it is a verified Supreme-only field.
                    "stability": _normalize_stability(item.get("stability")),
                    "range": str(item.get("range") or ""),
                    "demand": _optional_bounded_int(item.get("demand"), 0, 10),
                    "rarity": _optional_bounded_int(item.get("rarity"), 0, 10),
                    "acronym": str(item.get("acronym") or ""),
                    "event_name": str(item.get("event_name") or ""),
                    "tier": _optional_bounded_int(item.get("tier"), 0, 100),
                    "description": str(item.get("description") or "")[:500],
                    "image_url": str(item.get("image_url") or ""),
                    "fandom_url": str(item.get("fandom_url") or ""),
                    "last_updated": item.get("last_updated"),
                }
                key = normalize_name(name)
                existing = all_values.setdefault(key, {
                    "name": name, "value": value, "value_min": value_min,
                    "value_max": value_max, "secondary_value": secondary, "variants": [],
                })
                variants = existing["variants"]
                identity = (
                    candidate["id"] or candidate["slug"] or normalize_slug(name),
                    category, item_type,
                )
                previous_index = next((
                    index for index, variant in enumerate(variants)
                    if (
                        variant.get("id") or variant.get("slug") or normalize_slug(variant.get("name")),
                        str(variant.get("category") or "").casefold(),
                        str(variant.get("type") or "").casefold(),
                    ) == identity
                ), None)
                if previous_index is None:
                    variants.append(candidate)
                    if value is not None:
                        numeric_count += 1
                else:
                    previous = variants[previous_index]
                    variants[previous_index] = candidate
                    if previous.get("value") is None and value is not None:
                        numeric_count += 1
                    elif previous.get("value") is not None and value is None:
                        numeric_count -= 1
                if len(variants) == 1:
                    existing.update({
                        "name": name, "value": value, "value_min": value_min,
                        "value_max": value_max, "secondary_value": secondary,
                    })
                else:
                    # Same-name identities must never become a synthetic high/low range.
                    existing["value"] = existing["value_min"] = existing["value_max"] = None
                    existing["secondary_value"] = None
            offset += len(items)
            if offset < total and len(items) < page_size:
                raise RuntimeError(
                    f"[supreme] API returned a short page at offset {page_key} ({len(items)} rows) "
                    f"while {total - offset} rows were still reported; refusing partial snapshot."
                )
            if offset >= total:
                break
            time.sleep(0.25)
        if numeric_count < 100:
            raise RuntimeError(f"[supreme] Only {numeric_count} numeric source_b values loaded; refusing to calculate")
    except Exception as exc:
        # Preserve the safe failure, but expose the sanitized upstream reason for diagnosis.
        reason = redact(str(exc))[:300]
        raise RuntimeError(
            f"[supreme] Could not refresh current source_b values; refusing stale-cache calculations "
            f"({type(exc).__name__}: {reason})"
        ) from exc

    if RBLXVALUE_META_POLL_ENABLED and RBLXVALUE_API_KEY:
        try:
            latest_meta = _fetch_rblxvalue_meta(force=True)
            if source_meta and latest_meta and (
                source_meta.get("last_updated") != latest_meta.get("last_updated")
                or source_meta.get("items_total") != latest_meta.get("items_total")
            ):
                raise RuntimeError(
                    "[supreme] Source version or catalogue size changed during pagination; "
                    "refusing a mixed-version catalogue snapshot"
                )
            if latest_meta and sum(
                len(page.get("items", [])) for page in refreshed_pages.values()
            ) != latest_meta.get("items_total"):
                raise RuntimeError(
                    "[supreme] Paginated row count does not match the source metadata total; "
                    "refusing an incomplete catalogue"
                )
            source_meta = latest_meta or source_meta
        except Exception as exc:
            raise RuntimeError(
                f"[supreme] Could not verify source version after pagination ({type(exc).__name__}); refusing snapshot"
            ) from exc
    cache_document = {
        "schema_version": 4, "fetched_at": time.time(),
        "source_last_updated": source_meta.get("last_updated") if source_meta else None,
        "source_items_total": source_meta.get("items_total") if source_meta else None,
        "source": "Supreme Values via RBLXValue API value_source_b",
        "pages": refreshed_pages, "values": all_values,
    }
    _enrich_catalog_metadata_gaps(all_values, cache_document, source_meta, persist_cache=False)
    VALUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        VALUES_FILE, json.dumps(cache_document, ensure_ascii=False, indent=2) + "\n"
    )
    collisions = sum(1 for record in all_values.values() if len(record.get("variants", [])) > 1)
    print(f"[supreme] Loaded {numeric_count} numeric Supreme source_b values across {len(all_values)} names; {collisions} collisions kept separate")
    _write_supreme_values_audit(all_values, fetched_at=time.time())
    return all_values


def _variant_numeric_bounds(variant):
    if not isinstance(variant, dict):
        return None, None
    try:
        base = float(variant.get("value"))
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(base) or base <= 0:
        return None, None
    try:
        low, high = float(variant.get("value_min", base)), float(variant.get("value_max", base))
    except (TypeError, ValueError):
        low, high = base, base
    if not all(math.isfinite(v) and v > 0 for v in (low, high)) or low > high:
        low, high = base, base
    return low, high


def _select_conservative_variant(record, side):
    variants = record.get("variants") if isinstance(record, dict) else None
    variants = variants if isinstance(variants, list) and variants else [record]
    if len(variants) != 1:
        return None, None
    selected = variants[0]
    low, high = _variant_numeric_bounds(selected)
    if low is None or high is None:
        return None, None
    return (high if side == "give" else low), selected


def _resolved_item_group_key(record, item):
    variant = (record.get("variants") or [record])[0] if isinstance(record, dict) else {}
    return variant.get("slug") or variant.get("id") or (
        normalize_name(variant.get("name") or item.get("name", ""))
        + "|" + str(variant.get("category") or "") + "|" + str(variant.get("type") or "")
    )


def calculate_side(items, values, aliases=None, side="give"):
    total, breakdown, missing, grouped = 0.0, [], [], {}
    for item in items:
        if normalize_name(item["name"]) in IGNORED_VALUE_ITEMS:
            continue
        record, match_source, candidates = resolve_value_record(item["name"], values, aliases, item=item)
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
        group_key = _resolved_item_group_key(record, item)
        entry = grouped.setdefault(group_key, {
            "name": selected.get("name", record.get("name", item["name"])),
            "quantity": 0, "unit_value": unit_value,
            "stability": _normalize_stability(selected.get("stability")),
        })
        quantity = item.get("quantity")
        if quantity is None or item.get("quantity_source") == "uncertain":
            missing.append(f"{item['name']} (quantity uncertain)")
            continue
        if not 1 <= int(quantity) <= 100:
            missing.append(f"{item['name']} (quantity outside supported range)")
            continue
        entry["quantity"] += int(quantity)
    for entry in grouped.values():
        item_total = entry["unit_value"] * entry["quantity"]
        total += item_total
        suffix = f" x{entry['quantity']}" if entry["quantity"] > 1 else ""
        stability = entry.get("stability") or "N/A"
        breakdown.append(
            f"• {entry['name']}{suffix} — {entry['unit_value']:g} each = {item_total:g}"
            f" | Stability: {stability}"
        )
    return total, breakdown, missing


def calculate_side_bounds(items, values, aliases=None, side="give"):
    grouped, missing, uncertain = {}, [], []
    for item in items:
        if normalize_name(item["name"]) in IGNORED_VALUE_ITEMS:
            continue
        record, match_source, candidates = resolve_value_record(item["name"], values, aliases, item=item)
        if not record:
            suffix = f" (ambiguous: {', '.join(candidates)})" if candidates else ""
            missing.append(f"{item['name']}{suffix}")
            continue
        selected = (record.get("variants") or [record])[0]
        stability = _normalize_stability(selected.get("stability"))
        stability_key = stability.casefold() if stability else ""
        if stability_key == "untradeable":
            missing.append(f"{item['name']} (Untradeable; cannot be included on either trade side)")
            continue
        if side == "receive" and "stability" in selected:
            if stability_key not in RBLXVALUE_STABILITY_LABELS:
                missing.append(f"{item['name']} (RBLXValue Stability unverified)")
                continue
            if stability_key in BLOCKED_RECEIVE_STABILITY:
                missing.append(
                    f"{item['name']} (RBLXValue Stability: {stability}; excluded)"
                )
                continue
        unit_min, unit_max = _variant_numeric_bounds(selected)
        if unit_min is None or unit_max is None:
            missing.append(item["name"])
            continue
        if match_source != "exact":
            item["value_match_source"] = match_source
            item["resolved_value_name"] = selected.get("name", record.get("name", item["name"]))
        group_key = _resolved_item_group_key(record, item)
        entry = grouped.setdefault(group_key, {
            "name": selected.get("name", record.get("name", item["name"])),
            "category": str(selected.get("category") or "").strip(),
            "type": str(selected.get("type") or "").strip(),
            "source_slug": str(selected.get("slug") or ""),
            "source_id": str(selected.get("id") or ""),
            "stability": stability or "N/A",
            "demand": selected.get("demand"),
            "rarity": selected.get("rarity"),
            "min_quantity": 0, "max_quantity": 0, "unbounded": False,
            "unit_value_min": unit_min, "unit_value_max": unit_max,
        })
        quantity, source = item.get("quantity"), item.get("quantity_source", "implicit_single")
        qmin, qmax = item.get("quantity_min"), item.get("quantity_max")
        if qmin is None:
            qmin = quantity if quantity is not None else 1
        if qmax is None and quantity is not None and source != "uncertain":
            qmax = quantity
        try:
            qmin, qmax = int(qmin), int(qmax) if qmax is not None else None
        except (TypeError, ValueError):
            qmin, qmax, source = 1, None, "uncertain"
        if qmin < 1 or qmin > 100 or (qmax is not None and (qmax < qmin or qmax > 100)):
            qmin, qmax, source = 1, None, "uncertain"
        entry["min_quantity"] += qmin
        if qmax is None:
            # An unknown stack is not a mathematical infinity. It is not safe to
            # calculate any profit until BloxSwaps supplies a finite quantity.
            missing.append(f"{item['name']} (quantity unknown/unbounded)")
            qmax = qmin  # finite placeholder; evaluate_listing rejects via missing
        entry["max_quantity"] += qmax
        if source == "uncertain" or item.get("quantity") is None:
            uncertain.append({
                "name": item["name"], "quantity_min": qmin, "quantity_max": qmax,
                "quantity_source": source, "quantity_reason": item.get("quantity_reason", ""),
                "listed_price": item.get("listed_price"), "raw_excerpt": item.get("raw_excerpt", ""),
            })

    total_min, total_max, breakdown = 0.0, 0.0, []
    for entry in grouped.values():
        low, high = entry["min_quantity"], entry["max_quantity"]
        low_total = entry["unit_value_min"] * low
        high_total = entry["unit_value_max"] * high
        total_min += low_total
        total_max += high_total
        quantity_label = f"x{low}" if low == high else f"x? ({low}..{high})"
        total_label = f"{low_total:g}" if low == high and entry["unit_value_min"] == entry["unit_value_max"] else f"{low_total:g}..{high_total:g}"
        value_label = f"{entry['unit_value_min']:g}" if entry["unit_value_min"] == entry["unit_value_max"] else f"{entry['unit_value_min']:g}..{entry['unit_value_max']:g}"
        identity = " / ".join(part for part in (entry.get("category"), entry.get("type")) if part)
        display_name = f"{entry['name']} [{identity}]" if identity else entry["name"]
        quality_labels = []
        if entry.get("demand") is not None:
            quality_labels.append(f"Demand: {entry['demand']}/10")
        if entry.get("rarity") is not None:
            quality_labels.append(f"Rarity: {entry['rarity']}/10")
        breakdown.append(
            f"• {display_name} {quantity_label} — {value_label} each = {total_label}"
            f" | Stability (RBLXValue): {entry['stability']}"
            + (f" | {' | '.join(quality_labels)}" if quality_labels else "")
        )
    return total_min, total_max, breakdown, missing, uncertain


def format_number(value):
    if math.isinf(float(value)):
        return "-∞" if float(value) < 0 else "∞"
    if math.isnan(float(value)):
        return "uncertain"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class TelegramDeliveryUncertain(RuntimeError):
    """Telegram may have accepted a message but the response was not received."""


def _wait_for_telegram_rate_limit():
    """Respect the configured minimum spacing between messages to one chat."""
    global _LAST_TELEGRAM_SEND_AT
    now = time.monotonic()
    wait = ALERT_DELAY_SECONDS - (now - _LAST_TELEGRAM_SEND_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_TELEGRAM_SEND_AT = time.monotonic()


RBLXVALUE_ATTRIBUTION = "Data from rblxvalue.com"


def send_telegram(message):
    message = str(message)
    needs_attribution = not message.rstrip().endswith(RBLXVALUE_ATTRIBUTION)
    footer = "\n\n" + RBLXVALUE_ATTRIBUTION if needs_attribution else ""
    if len(message) + len(footer) > TELEGRAM_MAX_CHARS:
        # Reserve room for the multipart header and the mandatory source credit
        # in every Telegram message. Never truncate trade details.
        chunk_limit = TELEGRAM_MAX_CHARS - 96
        chunks, current = [], ""
        for line in message.splitlines():
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
                send_telegram(f"💎 MM2 TRADE DETAILS ({index}/{total_chunks})\n{chunk}")
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
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode()

    # sendMessage is non-idempotent. Never apply generic network/5xx retries:
    # Telegram may have accepted the message even when its response was lost.
    for attempt in range(2):
        _wait_for_telegram_rate_limit()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read(2000)
            if exc.code == 429 and attempt < 1:
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
        if int(result.get("error_code", 0) or 0) == 429 and attempt < 1:
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


def load_pending_ids():
    if not PENDING_FILE.exists():
        return {}
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[state] Pending recheck state is unreadable; refusing to lose unresolved listings.") from exc
    if isinstance(data, list):
        # Legacy list has no timestamps; retain every ID for re-evaluation.
        return {str(value): time.time() for value in data if value}
    if not isinstance(data, dict):
        raise RuntimeError("[state] Pending recheck state has an invalid structure.")
    pending = data.get("pending", {})
    if isinstance(pending, list):
        return {str(value): time.time() for value in pending if value}
    if isinstance(pending, dict):
        result = {}
        expired = []
        now = time.time()
        for ad_id, timestamp in pending.items():
            try:
                first_seen = float(timestamp)
            except (TypeError, ValueError):
                raise RuntimeError("[state] Pending recheck timestamp is invalid.")
            if first_seen > 0 and now - first_seen > PENDING_TTL_SECONDS:
                expired.append(str(ad_id))
            else:
                result[str(ad_id)] = first_seen
        if expired:
            save_pending_ids(result)
            print(f"[pending] Expired {len(expired)} unresolved listing(s) after {PENDING_TTL_SECONDS}s.")
            send_maintenance_alert(
                "pending-recheck-expired",
                f"{len(expired)} unresolved listing IDs expired from automatic recheck after "
                f"{PENDING_TTL_SECONDS // 86400} days: {', '.join(expired[:20])}. "
                "Review them manually if they are still active.",
                cooldown_seconds=86400,
            )
        return result
    raise RuntimeError("[state] Pending recheck state has an invalid structure.")


def save_pending_ids(pending):
    if isinstance(pending, set):
        pending = {str(value): time.time() for value in pending}
    ordered = dict(sorted(
        ((str(ad_id), float(timestamp)) for ad_id, timestamp in pending.items()),
        key=lambda row: row[1],
    ))
    atomic_write_text(PENDING_FILE, json.dumps({
        "version": 1, "pending": ordered,
    }, indent=2) + "\n")


def load_pending_recheck_schedule():
    """Load retry schedule metadata separately from first-seen/expiry timestamps."""
    if not PENDING_RECHECK_SCHEDULE_FILE.exists():
        return {}
    try:
        data = json.loads(PENDING_RECHECK_SCHEDULE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[state] Pending recheck schedule is unreadable; refusing an unsafe reset.") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError("[state] Pending recheck schedule has an unsupported structure.")
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        raise RuntimeError("[state] Pending recheck schedule must contain an object.")
    result = {}
    for ad_id, entry in schedule.items():
        if not isinstance(entry, dict):
            raise RuntimeError("[state] Pending recheck schedule entry is invalid.")
        attempts = entry.get("attempts")
        next_check = entry.get("next_check_at")
        if not isinstance(attempts, int) or attempts < 0:
            raise RuntimeError("[state] Pending recheck attempt count is invalid.")
        try:
            next_check = float(next_check)
        except (TypeError, ValueError):
            raise RuntimeError("[state] Pending recheck next_check_at is invalid.") from None
        if not math.isfinite(next_check) or next_check <= 0:
            raise RuntimeError("[state] Pending recheck next_check_at is invalid.")
        reason = entry.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or reason not in VALID_PENDING_REASONS
        ):
            raise RuntimeError("[state] Pending recheck reason is invalid.")
        result[str(ad_id)] = {"attempts": attempts, "next_check_at": next_check}
        if reason:
            result[str(ad_id)]["reason"] = reason
    return result


def save_pending_recheck_schedule(schedule):
    """Atomically persist bounded retry metadata for unresolved listings."""
    atomic_write_text(PENDING_RECHECK_SCHEDULE_FILE, json.dumps({
        "version": 1, "schedule": schedule,
    }, sort_keys=True, indent=2) + "\n")


def due_pending_ids_for_scan(pending, schedule, now=None, limit=MAX_PENDING_RECHECK_PER_SCAN, force=False):
    """Return due unresolved IDs, prioritizing never-checked items and applying a per-scan cap."""
    current = time.time() if now is None else float(now)
    candidates = []
    for ad_id, first_seen in pending.items():
        key = str(ad_id)
        entry = schedule.get(key)
        if not isinstance(entry, dict):
            candidates.append((0, 0.0, float(first_seen), key, first_seen))
            continue
        next_check = float(entry.get("next_check_at", 0))
        if force or next_check <= current:
            candidates.append((
                int(entry.get("attempts", 0)), next_check, float(first_seen), key, first_seen
            ))
    # Recheck the newest unresolved listings first: they are nearest the feed
    # head and can be verified without paging through hours of stale history.
    candidates.sort(key=lambda row: (row[0], row[1], -row[2], row[3]))
    cap = int(limit) if limit is not None else 0
    if cap > 0:
        candidates = candidates[:cap]
    return {row[3]: row[4] for row in candidates}



def seed_pending_ids_as_seen(seen_ads, pending_ids, schedule=None):
    """Keep low-priority unresolved IDs out of the hot feed; leave urgent retries visible."""
    if seen_ads is None:
        return None
    schedule = schedule or {}
    for ad_id in pending_ids or ():
        value = str(ad_id or "").strip()
        entry = schedule.get(value) or {}
        reason = entry.get("reason")
        attempts = int(entry.get("attempts", 0))
        # Preserve one fast retry for legacy pending entries whose old schedule
        # cannot tell whether they were deferred or failed to deliver.
        legacy_needs_fast_retry = reason is None and attempts <= 1
        if value and reason not in PRIORITY_PENDING_REASONS and not legacy_needs_fast_retry:
            seen_ads.add(value)
    return seen_ads


def pending_reason_from_missing(missing):
    """Classify why a listing cannot yet be valued without guessing its status."""
    detail = " ".join(str(value).casefold() for value in (missing or []))
    if "quantity unknown" in detail or "quantity uncertain" in detail:
        return "quantity_uncertain"
    if "stability" in detail:
        return "stability_blocked" if "excluded" in detail else "stability_unverified"
    return "unmatched_value"

def record_pending_recheck_attempts(due_ids, pending, schedule, now=None):
    """Advance retry backoff for checked IDs and initialize newly pending IDs."""
    current = time.time() if now is None else float(now)
    updated = {
        str(ad_id): dict(entry)
        for ad_id, entry in schedule.items()
        if str(ad_id) in pending and isinstance(entry, dict)
    }
    for ad_id in due_ids:
        key = str(ad_id)
        if key not in pending:
            updated.pop(key, None)
            continue
        previous = updated.get(key, {})
        attempts = int(previous.get("attempts", 0)) + 1
        delay = PENDING_RECHECK_BACKOFF_SECONDS[
            min(attempts - 1, len(PENDING_RECHECK_BACKOFF_SECONDS) - 1)
        ]
        reason = previous.get("reason", "legacy_unclassified")
        updated[key] = {
            "attempts": attempts, "next_check_at": current + delay, "reason": reason,
        }
    for ad_id in pending:
        key = str(ad_id)
        if key not in updated:
            updated[key] = {
                "attempts": 0,
                "next_check_at": current + PENDING_RECHECK_BACKOFF_SECONDS[0],
                "reason": "legacy_unclassified",
            }
    save_pending_recheck_schedule(updated)
    return updated


def save_seen_ads(ad_ids):
    ordered = sorted(set(str(value) for value in ad_ids if value))[-50000:]
    atomic_write_text(SEEN_FILE, json.dumps({
        "version": 1, "updated_at": datetime.now(timezone.utc).isoformat(),
        "ad_ids": ordered,
    }, indent=2) + "\n")


def load_first_seen_ads():
    """Load first-observed timestamps without resetting corrupt state."""
    if not FIRST_SEEN_FILE.exists():
        return {}
    try:
        data = json.loads(FIRST_SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[state] First-seen listing timestamps are unreadable; refusing a silent reset.") from exc
    entries = data.get("first_seen") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise RuntimeError("[state] First-seen listing timestamp state has an invalid structure.")
    result = {}
    for ad_id, timestamp in entries.items():
        if not isinstance(ad_id, str) or not ad_id or not isinstance(timestamp, str):
            raise RuntimeError("[state] First-seen listing timestamp entry is invalid.")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("[state] First-seen listing timestamp is invalid.") from exc
        result[ad_id] = timestamp
    return result


def save_first_seen_ads(entries):
    ordered = dict(list(entries.items())[-50000:])
    atomic_write_text(FIRST_SEEN_FILE, json.dumps({
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "first_seen": ordered,
    }, ensure_ascii=False, indent=2) + "\n")


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


def save_uncertain_state(state):
    normalized = {
        str(ad_id): float(timestamp)
        for ad_id, timestamp in state.items()
    }
    atomic_write_text(UNCERTAIN_FILE, json.dumps({
        "version": 2,
        "ids": sorted(normalized),
        "created_at": normalized,
    }, indent=2) + "\n")


def load_uncertain_state():
    if not UNCERTAIN_FILE.exists():
        return {}, []
    try:
        data = json.loads(UNCERTAIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery file is unreadable; refusing to risk duplicate alerts."
        ) from exc
    now = time.time()
    if isinstance(data, list):
        raw_ids = [str(value) for value in data]
        created_at = {ad_id: now for ad_id in raw_ids}
    elif isinstance(data, dict) and isinstance(data.get("ids"), list):
        raw_ids = [str(value) for value in data["ids"]]
        created_at = data.get("created_at") if isinstance(data.get("created_at"), dict) else {}
    else:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery file has an invalid structure; refusing duplicate-risk sends."
        )
    active = {}
    expired = []
    for ad_id in raw_ids:
        try:
            timestamp = float(created_at.get(ad_id, now))
        except (TypeError, ValueError):
            raise RuntimeError("[state] Telegram uncertain-delivery timestamp is invalid.")
        if timestamp > 0 and now - timestamp > UNCERTAIN_TTL_SECONDS:
            expired.append(ad_id)
        else:
            active[ad_id] = timestamp
    if expired:
        save_uncertain_state(active)
    return active, expired


def load_uncertain_ids():
    state, _ = load_uncertain_state()
    return set(state)


def save_uncertain_ids(ids):
    wanted = set(str(value) for value in ids)
    state, _ = load_uncertain_state()
    state = {ad_id: timestamp for ad_id, timestamp in state.items() if ad_id in wanted}
    now = time.time()
    for ad_id in wanted:
        state.setdefault(ad_id, now)
    save_uncertain_state(state)


def _load_json_state(path, default, label):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"[state] {label} is unreadable ({path}); refusing silent reset.") from exc
    return data


def send_maintenance_alert(key, message, cooldown_seconds=3600):
    if os.environ.get("DRY_RUN", "").strip().casefold() == "true":
        print(f"[dry-run] would send maintenance alert: {key}")
        return False
    state = _load_json_state(MAINTENANCE_FILE, {}, "maintenance alert state")
    if not isinstance(state, dict):
        raise RuntimeError("[state] Maintenance alert state has an invalid structure.")
    now = time.time()
    last_sent = float(state.get(key, 0) or 0)
    if last_sent and now - last_sent < cooldown_seconds:
        return False
    # Persist the guard before the non-idempotent Telegram call.
    state[key] = now
    atomic_write_text(MAINTENANCE_FILE, json.dumps(state, indent=2) + "\n")
    try:
        send_telegram("MM2 SCANNER MAINTENANCE\n" + message)
        print(f"[maintenance] Alert sent: {key}")
        return True
    except Exception as exc:
        print(f"[maintenance] Alert failed for {key}: {type(exc).__name__}")
        return False


def record_unmatched_names(names):
    state = _load_json_state(UNMATCHED_FILE, {}, "unmatched-name state")
    if not isinstance(state, dict):
        raise RuntimeError("[state] Unmatched-name state has an invalid structure.")
    now = datetime.now(timezone.utc).isoformat()
    new_names = []
    for raw_name in names:
        name = str(raw_name).strip()
        key = normalize_name(name)
        if not key or key in IGNORED_VALUE_ITEMS:
            continue
        if key not in state:
            state[key] = {"name": name, "first_seen_at": now, "last_seen_at": now}
            new_names.append(name)
        else:
            state[key]["last_seen_at"] = now
    atomic_write_text(UNMATCHED_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    if new_names:
        unique = sorted(set(new_names), key=str.casefold)
        alert_key = "unmatched:" + hashlib.sha256(
            "|".join(normalize_name(name) for name in unique).encode()
        ).hexdigest()[:16]
        send_maintenance_alert(
            alert_key,
            "New item names not found in Supreme source_b (no values guessed): "
            + ", ".join(unique)
            + "\nThey will be rechecked on every scan. Review aliases.json only after confirming the exact item.",
            cooldown_seconds=86400,
        )
    return new_names


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


def _compact_telegram_items(items, limit=None):
    """Render every valued item; never silently hide weapon/value lines in Telegram."""
    items = list(items or [])
    shown = items if limit is None else items[:max(0, int(limit))]
    if limit is not None and len(items) > limit:
        shown.append(f"• … and {len(items) - limit} more items (see diagnostics)")
    return shown or ["• No items"]


def _clean_owner_link(url):
    parsed = urlsplit(str(url or ""))
    if parsed.scheme and parsed.netloc and parsed.path:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    return str(url or BLOXSWAPS_URL)


def format_trade_message(listing, give_total, give_lines, receive_total, receive_lines, difference):
    """Short trade-first Telegram alert; parser diagnostics remain in Actions logs."""
    result = "WIN" if difference > 0 else ("FAIR" if difference == 0 else "LOSS")
    owner_link = (
        str(listing.get("owner_url") or listing.get("url") or "Discord message link unavailable")
        if listing.get("source") == "discord"
        else _clean_owner_link(listing.get("owner_url") or listing.get("url"))
    )
    lines = [
        "💎 MM2 TRADE",
        f"SOURCE: {str(listing.get('source', 'bloxswaps')).upper()}",
        "",
        f"👤 {owner_link}",
    ]
    if listing.get("url") and listing.get("url") != owner_link:
        lines.extend(["", f"🔗 {listing['url']}"])
    if listing.get("valuation_mode") == "cash":
        roi = (difference / give_total * 100.0) if give_total else 0.0
        lines.extend([
            "",
            "📤 GIVE (CASH REPLACEMENT COST):",
            *_compact_telegram_items(give_lines),
            f"Total cost: ${format_number(give_total)}",
            "",
            "📥 GET (NET CASH RESALE):",
            *_compact_telegram_items(receive_lines),
            f"Net resale: ${format_number(receive_total)}",
            "",
            f"💵 CASH PROFIT (CONSERVATIVE): {'+' if difference > 0 else ''}${format_number(difference)}",
            f"CASH ROI: {roi:.2f}%",
            f"Resale haircut: {CASH_SELL_HAIRCUT_PCT:.1f}%",
            f"RESULT: {'CASH WIN' if difference > 0 else 'NO CASH PROFIT'}",
        ])
    else:
        lines.extend([
            "",
            "📤 GIVE:",
            *_compact_telegram_items(give_lines),
            f"Total: {format_number(give_total)}",
            "",
            "📥 GET:",
            *_compact_telegram_items(receive_lines),
            f"Total: {format_number(receive_total)}",
            "",
            f"💰 PROFIT: {'+' if difference > 0 else ''}{format_number(difference)}",
            f"RESULT: {result}",
        ])
    return "\n".join(lines)


def format_unvalued_message(listing, missing):
    def describe(items):
        lines = []
        for item in items:
            quantity = item.get("quantity")
            if quantity is None:
                low = item.get("quantity_min", 1)
                high = item.get("quantity_max")
                label = (
                    f" x? ({low}..{high})" if high is not None
                    else f" x? (quantity unknown; at least {low})"
                )
            else:
                label = f" x{quantity}" if quantity > 1 else ""
            lines.append(f"• {item['name']}{label}")
        return _compact_telegram_items(lines)

    owner_link = (
        str(listing.get("owner_url") or listing.get("url") or "Discord message link unavailable")
        if listing.get("source") == "discord"
        else _clean_owner_link(listing.get("owner_url") or listing.get("url"))
    )
    lines = [
        "⚠️ MM2 MANUAL VALUE CHECK",
        f"SOURCE: {str(listing.get('source', 'bloxswaps')).upper()}",
        "",
        f"👤 {owner_link}",
    ]
    if listing.get("url") and listing.get("url") != owner_link:
        lines.extend(["", f"🔗 {listing['url']}"])
    lines.extend([
        "",
        "📤 GIVE:",
        *describe(listing.get("give_items", [])),
        "",
        "📥 GET:",
        *describe(listing.get("receive_items", [])),
        "",
        "UNMATCHED: " + ", ".join(sorted(set(missing))[:8]),
        "PROFIT: NOT CALCULATED — no value was guessed.",
    ])
    return "\n".join(lines)


def _receive_market_quality(items, values, aliases=None):
    """Conservative value-weighted demand/stability quality for items we receive."""
    stability_scores = {
        "rising": 9, "overpaid for": 8, "overpaid for ": 8,
        "doing well": 7, "improving": 6, "fluctuating": 5,
        "receding": 4, "stable": 3, "underpaid for": 2,
        "decreasing": 1, "unsafe": 0, "untradeable": 0,
        "unstable": 0, "volatile": 0,
    }
    weighted_demand = 0.0
    weighted_stability = 0.0
    total_weight = 0.0
    issues = []
    for item in items:
        record, _match_source, candidates = resolve_value_record(
            item["name"], values, aliases, item=item
        )
        if not record:
            continue  # calculate_side_bounds reports identity/value failures
        variants = record.get("variants") or [record]
        if len(variants) != 1:
            continue  # ambiguous identities are already rejected by valuation
        selected = variants[0]
        low, _high = _variant_numeric_bounds(selected)
        if low is None:
            continue
        # Production API rows always include these keys (possibly null). Older
        # unit fixtures and legacy caches may not; those are handled by the
        # explicit source-version refresh before production use.
        if "demand" not in selected or "stability" not in selected:
            continue
        demand = _optional_bounded_int(selected.get("demand"), 0, 10)
        stability = _normalize_stability(selected.get("stability"))
        if demand is None:
            issues.append(f"{item['name']} (Demand unverified)")
            continue
        stability_key = stability.casefold() if stability else ""
        stability_score = stability_scores.get(stability_key)
        if stability_score is None:
            issues.append(f"{item['name']} (Stability unverified)")
            continue
        quantity = item.get("quantity_min")
        if quantity is None:
            quantity = item.get("quantity")
        try:
            quantity = max(1, int(quantity or 1))
        except (TypeError, ValueError):
            quantity = 1
        weight = low * quantity
        if weight <= 0:
            continue
        total_weight += weight
        weighted_demand += demand * weight
        weighted_stability += stability_score * weight
    if issues:
        return None, None, issues
    if total_weight <= 0:
        return None, None, []
    demand_score = weighted_demand / total_weight
    stability_score = weighted_stability / total_weight
    if demand_score < 4.0:
        issues.append(f"received-side value-weighted Demand is low ({demand_score:.2f}/10; minimum 4.00)")
    if stability_score < 3.0:
        issues.append(f"received-side value-weighted Stability is weak ({stability_score:.2f}/9; minimum 3.00)")
    return demand_score, stability_score, issues


def evaluate_listing(listing, values, aliases=None):
    # Items deliberately omitted from numeric Supreme valuation must still block
    # a WIN: treating an unpriced item as zero can manufacture a false profit.
    ignored_unvalued = ignored_item_names(listing)
    if ignored_unvalued:
        return None, [f"{name} has no numeric Supreme value" for name in ignored_unvalued]
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
    receive_demand_score, receive_stability_score, quality_issues = _receive_market_quality(
        listing["receive_items"], values, aliases
    )
    if quality_issues:
        return None, quality_issues
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
        "receive_demand_score": receive_demand_score,
        "receive_stability_score": receive_stability_score,
    }, []


def qualifies(result, min_diff=None, min_percent=None):
    min_diff = MIN_DIFF if min_diff is None else min_diff
    min_percent = MIN_PROFIT_PERCENT if min_percent is None else min_percent
    # Only fully verified, conservative-positive trades are eligible for Telegram.
    # A best-case-only profit is uncertain and must never be advertised as a WIN.
    if result.get("manual_review"):
        return False
    metric = result.get("profit_worst", result.get("difference", 0))
    return metric > 0 and metric >= min_diff and result["margin"] >= min_percent


def evaluate_cash_listing(listing, values, aliases, cash_catalog):
    """Evaluate cash resale profit only; Supreme is used for identity/stability gates."""
    if not listing.get("give_items") or not listing.get("receive_items"):
        return None, ["one side has no items"]

    prepared = {"give": [], "receive": []}
    missing = []
    for side, field in (("give", "give_items"), ("receive", "receive_items")):
        for item in listing.get(field, []):
            record, match_source, candidates = resolve_value_record(
                item.get("name", ""), values, aliases, item=item
            )
            if not record:
                suffix = f" (ambiguous: {', '.join(candidates)})" if candidates else " (not in Supreme catalogue)"
                missing.append(f"{item.get('name', 'Unknown')}{suffix}")
                continue
            variants = record.get("variants") or [record]
            if len(variants) != 1:
                missing.append(f"{item.get('name')} (ambiguous Supreme identity)")
                continue
            selected = variants[0]
            canonical_name = str(selected.get("name") or record.get("name") or item.get("name") or "").strip()
            if not canonical_name:
                missing.append(f"{item.get('name')} (Supreme identity missing)")
                continue

            stability = _normalize_stability(selected.get("stability"))
            stability_key = stability.casefold() if stability else ""
            if stability_key == "untradeable":
                missing.append(f"{canonical_name} (Untradeable)")
                continue
            if side == "receive":
                if "stability" not in selected or stability_key not in RBLXVALUE_STABILITY_LABELS:
                    missing.append(f"{canonical_name} (Stability unverified)")
                    continue
                if stability_key in BLOCKED_RECEIVE_STABILITY:
                    missing.append(f"{canonical_name} (Stability: {stability}; excluded)")
                    continue

            quantity = item.get("quantity")
            qmin = item.get("quantity_min")
            qmax = item.get("quantity_max")
            source = item.get("quantity_source", "implicit_single")
            try:
                qmin = int(qmin if qmin is not None else (quantity if quantity is not None else 1))
                if qmax is None and quantity is not None and source != "uncertain":
                    qmax = quantity
                qmax = int(qmax) if qmax is not None else None
            except (TypeError, ValueError):
                qmin, qmax = 1, None
            if qmin < 1 or qmin > 100 or qmax is None or qmax < qmin or qmax > 100:
                missing.append(f"{canonical_name} (quantity uncertain/unbounded)")
                continue

            prepared[side].append({
                "name": canonical_name,
                "canonical_name": canonical_name,
                "quantity_min": qmin,
                "quantity_max": qmax,
                "stability": stability or "N/A",
                "match_source": match_source,
            })

    if missing:
        return None, missing
    if not prepared["give"] or not prepared["receive"]:
        return None, ["one side has no Supreme-catalogued items"]

    give_min, give_max, give_lines, give_missing = calculate_cash_side(
        prepared["give"], cash_catalog, "give", 0
    )
    receive_min, receive_max, receive_lines, receive_missing = calculate_cash_side(
        prepared["receive"], cash_catalog, "receive", CASH_SELL_HAIRCUT_PCT
    )
    missing = give_missing + receive_missing
    if missing:
        return None, missing

    give_lines = [
        line + f" | RBLXValue Stability: {item['stability']}"
        for line, item in zip(give_lines, prepared["give"])
    ]
    receive_lines = [
        line + f" | RBLXValue Stability: {item['stability']}"
        for line, item in zip(receive_lines, prepared["receive"])
    ]
    profit_worst, profit_best = cash_profit_bounds(
        (give_min, give_max), (receive_min, receive_max)
    )
    manual_review = profit_worst <= 0 < profit_best
    margin = (profit_worst / give_max * 100.0) if give_max > 0 else 0.0
    listing["valuation_mode"] = "cash"
    return {
        "listing": listing,
        "give_total": give_max,
        "give_total_min": give_min,
        "give_total_max": give_max,
        "give_lines": give_lines,
        "receive_total": receive_min,
        "receive_total_min": receive_min,
        "receive_total_max": receive_max,
        "receive_lines": receive_lines,
        "difference": profit_worst,
        "profit_worst": profit_worst,
        "profit_best": profit_best,
        "margin": margin,
        "manual_review": manual_review,
        "uncertain_items": [],
        "cash_sell_haircut_pct": CASH_SELL_HAIRCUT_PCT,
        "cash_profit_worst": profit_worst,
        "cash_profit_best": profit_best,
        "cash_price_basis": "verified manual USD resale catalogue",
        "receive_demand_score": None,
        "receive_stability_score": None,
    }, []


def qualifies_cash(result):
    """Cash-only alert gate; Supreme numeric value and Demand never rank profit."""
    if result.get("manual_review"):
        return False
    profit = float(result.get("cash_profit_worst", result.get("profit_worst", 0)))
    margin = float(result.get("margin", 0))
    return (
        math.isfinite(profit) and math.isfinite(margin)
        and profit >= CASH_MIN_PROFIT_USD
        and margin >= CASH_MIN_MARGIN_PERCENT
    )


def cash_catalog_snapshot_hash():
    if not CASH_PRICE_SNAPSHOT_FILE.exists():
        return ""
    try:
        data = json.loads(CASH_PRICE_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("[cash] Cash-price snapshot state is unreadable; refusing to skip catalogue rechecks.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sha256"), str):
        raise RuntimeError("[cash] Cash-price snapshot state has an invalid structure.")
    return data["sha256"]


def save_cash_catalog_snapshot(digest):
    atomic_write_text(
        CASH_PRICE_SNAPSHOT_FILE,
        json.dumps({"version": 1, "sha256": str(digest)}, indent=2) + "\n",
    )


def should_alert_malformed_cards(issue_count):
    """Any malformed card is actionable; rate limits belong to the persisted cooldown."""
    try:
        return int(issue_count) > 0
    except (TypeError, ValueError):
        return False


def write_step_summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def listing_watch_fingerprints(listing, values, aliases=None, cash_catalog=None):
    content, valuation = [], []
    for side, field in (('give', 'give_items'), ('receive', 'receive_items')):
        for item in listing.get(field, []):
            name = str(item.get('name') or '')
            content.append((
                side, normalize_name(name), str(item.get('structured_item_id') or ''),
                normalize_slug(item.get('catalog_slug')), str(item.get('catalog_rarity') or ''),
                str(item.get('catalog_type') or ''), bool(item.get('catalog_chroma')),
                item.get('quantity'), item.get('quantity_min'), item.get('quantity_max')
            ))
            record, match_source, _ = resolve_value_record(name, values, aliases, item=item)
            unit_value = None
            unit_min = unit_max = None
            if record:
                unit_value, _selected = _select_conservative_variant(record, side)
                if _selected:
                    unit_min, unit_max = _variant_numeric_bounds(_selected)
            cash_row = None
            if cash_catalog:
                cash_row = cash_catalog.get("items", {}).get(normalize_cash_name(
                    (_selected or {}).get("name") if record else name
                ))
            valuation.append((
                side, normalize_name(name), unit_value, unit_min, unit_max, match_source,
                cash_row.get("cash_price_usd") if cash_row else None,
                cash_row.get("status") if cash_row else "missing",
                cash_row.get("verified_at_utc") if cash_row else "",
            ))
    content_hash = hashlib.sha256(json.dumps(sorted(content, key=lambda row: json.dumps(row, sort_keys=True, default=str)), sort_keys=True, default=str).encode()).hexdigest()
    values_hash = hashlib.sha256(json.dumps(sorted(valuation, key=lambda row: json.dumps(row, sort_keys=True, default=str)), sort_keys=True, default=str).encode()).hexdigest()
    return content_hash, values_hash

def load_watch_state():
    if not WATCH_FILE.exists(): return {}
    try: data = json.loads(WATCH_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc: raise RuntimeError('[state] Watch-window state is unreadable; refusing to lose re-evaluation coverage.') from exc
    ads = data.get('ads') if isinstance(data, dict) else None
    if not isinstance(ads, dict): raise RuntimeError('[state] Watch-window state has an invalid structure.')
    now = time.time()
    active = {}
    for ad_id, entry in ads.items():
        if not isinstance(entry, dict): raise RuntimeError('[state] Watch-window entry has an invalid structure.')
        created = float(entry.get('created_at', 0) or 0)
        if created > 0 and now - created <= 6 * 3600: active[str(ad_id)] = entry
    if len(active) > 300: active = dict(sorted(active.items(), key=lambda pair: float(pair[1].get('updated_at', 0)))[-300:])
    if len(active) != len(ads): save_watch_state(active)
    return active

def save_watch_state(ads):
    now = time.time()
    active = {str(k): v for k, v in ads.items() if isinstance(v, dict) and now - float(v.get('created_at', now)) <= 6 * 3600}
    if len(active) > 300: active = dict(sorted(active.items(), key=lambda pair: float(pair[1].get('updated_at', 0)))[-300:])
    atomic_write_text(WATCH_FILE, json.dumps({'version': 1, 'ads': active}, ensure_ascii=False, indent=2) + "\n")

def update_watch_entry(watch_state, listing, values, aliases=None, cash_catalog=None):
    ad_id = str(listing.get('source_ad_id') or '')
    if not ad_id: return False
    content_hash, values_hash = listing_watch_fingerprints(listing, values, aliases, cash_catalog)
    now = time.time()
    previous = watch_state.get(ad_id, {})
    changed = bool(previous) and (previous.get('content_hash') != content_hash or previous.get('values_hash') != values_hash)
    created = now if changed or not previous else float(previous.get('created_at', now))
    watch_state[ad_id] = {'content_hash': content_hash, 'values_hash': values_hash, 'created_at': created, 'updated_at': now}
    save_watch_state(watch_state)
    return changed

def record_success_if_complete(stats):
    """Refresh the health checkpoint only after the feed boundary is verified."""
    if not isinstance(stats, dict) or not stats.get("pagination_complete", False):
        print(
            "[health] Pagination was incomplete; preserving the previous "
            "last-success checkpoint so the watchdog can detect degraded coverage."
        )
        write_step_summary(
            "ERROR: Feed pagination incomplete; this scan cycle is not successful."
        )
        raise RuntimeError(
            "[bloxswaps] Feed pagination incomplete; refusing to count this scan cycle as successful."
        )
    record_success()
    return True


def scan_once(include_backlog_rechecks=True):
    global _SESSION_ALERTS_SENT
    dry_run = os.environ.get("DRY_RUN", "").strip().casefold() == "true"
    if not dry_run and (not BOT_TOKEN or not CHAT_ID):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets.")
    update_metrics(increments={"scans": 1})

    print("[stage] Checking RBLXValue API credential")
    if not RBLXVALUE_API_KEY:
        print("[supreme] API key missing; only a fresh schema-v4 Supreme cache can be used")
    elif not RBLXVALUE_API_KEY.startswith("rbx_"):
        print("[supreme] API key format looks invalid; cached values will be used if available")
    else:
        print("[stage] API key present; secret contents are intentionally never logged")
    print("[stage] Loading Supreme Values before evaluating trades")
    values = load_supreme_values()
    cash_catalog = load_cash_price_catalog(CASH_PRICE_FILE)
    verified_cash_rows = sum(1 for row in cash_catalog["items"].values() if row["status"] == "verified")
    unverified_cash_rows = sum(1 for row in cash_catalog["items"].values() if row["status"] == "unverified")
    previous_cash_hash = cash_catalog_snapshot_hash()
    cash_prices_changed = previous_cash_hash != cash_catalog["sha256"]
    print("[cash] Loaded %s price rows; verified=%s; unverified=%s; sell_haircut=%s%%" % (
        cash_catalog["row_count"], verified_cash_rows, unverified_cash_rows, CASH_SELL_HAIRCUT_PCT
    ))
    if verified_cash_rows == 0:
        print("[cash] No verified cash prices yet; cash-profit alerts are safely disabled until prices are manually verified.")
    values_snapshot_changed = False
    history_store = None
    try:
        from market_history import MarketHistory
        history_store = MarketHistory()
        if history_store.record_values_snapshot(values):
            values_snapshot_changed = True
            print("[market-history] New Supreme/secondary value snapshot stored")
    except Exception as history_error:
        print(f"[market-history] Value snapshot storage skipped safely: {type(history_error).__name__}")
    aliases = load_aliases()
    print(f"[aliases] Loaded {len(aliases)} reviewed item aliases")
    print("[stage] Loading seen-offer and pending-recheck state")
    seen_ads = load_seen_ads()
    pending_ids = load_pending_ids()
    pending_schedule = load_pending_recheck_schedule()
    if seen_ads is not None:
        new_pending_seen = set(pending_ids) - seen_ads
        if new_pending_seen:
            seed_pending_ids_as_seen(seen_ads, new_pending_seen, pending_schedule)
            save_seen_ads(seen_ads)
            print(
                f"[state] Seeded {len(new_pending_seen)} pending IDs into the seen set; "
                "they remain queued for bounded rechecks."
            )
    all_due_pending_ids = due_pending_ids_for_scan(
        pending_ids, pending_schedule, limit=0
    )
    run_backlog_rechecks = include_backlog_rechecks or values_snapshot_changed or cash_prices_changed
    due_pending_ids = (
        due_pending_ids_for_scan(
            pending_ids, pending_schedule, force=values_snapshot_changed or cash_prices_changed
        )
        if run_backlog_rechecks else {}
    )
    watch_state = load_watch_state()
    # If the seen file was lost but pending items survive, do not cold-start:
    # search due unresolved IDs before establishing a baseline.
    if seen_ads is None and include_backlog_rechecks and (pending_ids or watch_state):
        seen_ads = set()
    print(
        f"[state] Pending listings: {len(pending_ids)} total; "
        f"{len(all_due_pending_ids)} due; "
        f"{len(due_pending_ids)} selected for this scan (cap={MAX_PENDING_RECHECK_PER_SCAN})"
    )
    print("[stage] Fetching BloxSwaps listings and cursor pages")
    bloxswaps_watch_ids = (
        [key for key in watch_state if not str(key).startswith("discord:")]
        if run_backlog_rechecks else []
    )
    scan_mode = (
        "values-refresh-backlog" if values_snapshot_changed
        else "cash-price-refresh-backlog" if cash_prices_changed
        else "backlog-recheck" if include_backlog_rechecks
        else "fast-new-feed"
    )
    print(f"[scan-mode] {scan_mode}; backlog_recheck_cap={MAX_PENDING_RECHECK_PER_SCAN}; page_lookahead={MAX_BACKLOG_RECHECK_PAGES}")
    listings, stats, discovered_ids = fetch_bloxswaps_all(
        seen_ads, due_pending_ids.keys(), bloxswaps_watch_ids
    )
    pending_schedule = record_pending_recheck_attempts(
        due_pending_ids, pending_ids, pending_schedule
    )
    # The collector writes a bounded local JSON file; parse it into the same
    # normalized schema, but keep its IDs namespaced away from BloxSwaps cursors.
    from discord_parser import load_discord_listings

    try:
        discord_listings, discord_rejected = load_discord_listings()
    except Exception as discord_error:
        discord_listings, discord_rejected = [], 0
        print(f"[discord-parser] source skipped safely: {type(discord_error).__name__}; BloxSwaps scan continues")
    if discord_listings:
        existing_ids = {str(row.get("id") or "") for row in listings}
        fresh_discord = [row for row in discord_listings if str(row.get("id") or "") not in existing_ids]
        listings.extend(fresh_discord)
    else:
        fresh_discord = []
    print(
        f"[discord-parser] parsed={len(discord_listings)} "
        f"new_for_batch={len(fresh_discord)} rejected={discord_rejected}"
    )
    first_seen_ads = load_first_seen_ads()
    first_seen_changed = False
    observed_at = datetime.now(timezone.utc).isoformat()
    for ad_id in discovered_ids:
        key = str(ad_id)
        if key and key not in first_seen_ads:
            first_seen_ads[key] = observed_at
            first_seen_changed = True
    for listing in listings:
        source_ad_id = str(listing.get("source_ad_id") or "")
        if listing.get("source") == "discord":
            listing["first_seen_at_utc"] = listing.get("posted_at") or observed_at
        else:
            listing["first_seen_at_utc"] = first_seen_ads.get(source_ad_id, observed_at)
    if first_seen_changed:
        save_first_seen_ads(first_seen_ads)
    if stats.get("cold_start"):
        seed_ids = set(str(value) for value in stats.get("cold_start_seed_ids", discovered_ids))
        save_seen_ads(seed_ids)
        seen_ads = seed_ids
        seed_pending_ids_as_seen(seen_ads, pending_ids, pending_schedule)
        save_seen_ads(seen_ads)
        print(
            f"[state] Cold-start baseline saved for {len(seed_ids)} historical posts; "
            f"{len(stats.get('cold_start_recent_ids', []))} posts refreshed within 60 minutes will be evaluated."
        )
        if not listings:
            record_success_if_complete(stats)
    save_cash_catalog_snapshot(cash_catalog["sha256"])
            write_step_summary("Cold-start baseline saved; no verifiably recent post was eligible for evaluation.")
            return
    else:
        seen_ads = set(seen_ads or [])
    pending_count_before = len(pending_ids)
    for ad_id in stats.get("unparsed_ad_ids", []):
        key = str(ad_id)
        pending_ids.setdefault(key, time.time())
        if key in seen_ads:
            seen_ads.discard(key)
            save_seen_ads(seen_ads)
        if key not in pending_schedule or pending_schedule[key].get("reason") != "unparsed_feed":
            previous_attempts = int(pending_schedule.get(key, {}).get("attempts", 0))
            pending_schedule[key] = {
                "attempts": previous_attempts,
                "next_check_at": time.time(),
                "reason": "unparsed_feed",
            }
    if len(pending_ids) != pending_count_before:
        save_pending_ids(pending_ids)
        save_pending_recheck_schedule(pending_schedule)
    addition_ids = set(stats.get("addition_ids", []))
    if addition_ids:
        seen_ads.update(addition_ids)
        for ad_id in addition_ids:
            key = str(ad_id)
            pending_ids.pop(key, None)
            pending_schedule.pop(key, None)
        save_seen_ads(seen_ads)
        save_pending_ids(pending_ids)
        save_pending_recheck_schedule(pending_schedule)
    if stats.get("watch_remaining_ids") and stats.get("watch_search_complete", False):
        missing_watch_ids = set(stats.get("watch_remaining_ids", []))
        for ad_id in missing_watch_ids:
            watch_state.pop(str(ad_id), None)
        save_watch_state(watch_state)
        send_maintenance_alert(
            "watch-ads-not-found",
            f"{len(missing_watch_ids)} watched listing(s) were no longer present in the searched feed window; "
            "they were removed from the six-hour watch window.",
            cooldown_seconds=3600,
        )
    if stats.get("pending_remaining_ids"):
        print(
            f"[pending] {len(stats['pending_remaining_ids'])} unresolved ad ID(s) "
            "were not found before the pagination boundary/cap."
        )
        if stats.get("pagination_complete"):
            send_maintenance_alert(
                "pending-ads-not-found",
                f"Could not locate {len(stats['pending_remaining_ids'])} pending listing(s) "
                "in the recent cursor pages. They remain queued for recheck.",
                cooldown_seconds=21600,
            )
    if stats.get("backlog_page_limit_reached"):
        print(
            f"[pending] Backlog page lookahead capped at {MAX_BACKLOG_RECHECK_PAGES}; "
            "unresolved IDs remain queued without blocking the new-feed boundary."
        )
    if not listings:
        update_metrics(increments={"parse_empty": 1})
        html_issue_count = max(
            int(stats.get("html_malformed_count", 0)),
            int(stats.get("html_parse_warning_count", 0)),
        )
        if stats.get("seen_boundary_reached") and not stats.get("malformed_count") and not html_issue_count:
            print("[bloxswaps] Feed is current: first visible offers were already processed.")
            record_success_if_complete(stats)
    save_cash_catalog_snapshot(cash_catalog["sha256"])
            write_step_summary("Feed current: no new trade posts before the seen cursor boundary.")
            return
        if stats["candidate_count"] > 0 and stats["addition_count"] == stats["candidate_count"]:
            print(f"[bloxswaps] No eligible listings: all {stats['addition_count']} candidate cards contained Ajouts.")
            record_success_if_complete(stats)
    save_cash_catalog_snapshot(cash_catalog["sha256"])
            write_step_summary("Successful scan: all current candidates excluded for Ajouts.")
            return
        if stats["candidate_count"] == 0 and stats.get("pagination_source") == "html-only":
            raise RuntimeError("[bloxswaps] No trade cards parsed; page structure may have changed or feed is empty.")
        if stats["candidate_count"] == 0 and not stats.get("pagination_complete"):
            raise RuntimeError("[bloxswaps] No feed entries parsed and pagination could not be verified.")
    parsed_count = len(listings)
    listings = deduplicate_listings(listings)
    if history_store is not None:
        try:
            stored = history_store.record_listings(listings)
            print(f"[market-history] Listings recorded or refreshed: {stored}")
        except Exception as history_error:
            print(f"[market-history] Listing storage skipped safely: {type(history_error).__name__}")
    duplicate_count = parsed_count - len(listings)
    if duplicate_count:
        print(f"[dedupe] Ignored {duplicate_count} duplicate listing row(s) in this scan batch.")
    print(f"[bloxswaps] Eligible listings after Ajouts filter: {len(listings)}")
    print(f"[bloxswaps] Direct owner profiles parsed: {stats.get('owner_link_count', 0)}/{len(listings)}")
    print(
        f"[pagination] pages={stats.get('pagination_pages', 1)} "
        f"api_posts={stats.get('pagination_api_posts', 0)} "
        f"complete={stats.get('pagination_complete', False)} "
        f"seen_boundary={stats.get('seen_boundary_reached', False)}"
    )
    malformed_count = int(stats.get("malformed_count", 0))
    html_issue_count = max(
        int(stats.get("html_malformed_count", 0)),
        int(stats.get("html_parse_warning_count", 0)),
    )
    issue_count = malformed_count + html_issue_count
    if issue_count:
        print(
            f"[maintenance] feed issues: api_unparsed={malformed_count}, "
            f"html_unparsed_or_malformed={html_issue_count}; "
            f"unparsed IDs: {', '.join(stats.get('unparsed_ad_ids', [])[:10])}; "
            f"HTML warning IDs: {', '.join(stats.get('html_parse_warning_ids', [])[:10])}"
        )
        ratio = issue_count / max(1, stats.get("candidate_count", 0))
        if should_alert_malformed_cards(issue_count):
            samples = " | ".join(stats.get("malformed_samples", [])[:3])
            send_maintenance_alert(
                "malformed-feed-cards",
                f"Cards needing parser review: {issue_count}/{stats.get('candidate_count', 0)} "
                f"({ratio:.1%}). Samples: {samples[:700]}",
                cooldown_seconds=3600,
            )
    if not stats.get("pagination_complete", False):
        print("[maintenance] Feed pagination is incomplete; more recent posts may remain beyond the scan limit.")
        send_maintenance_alert(
            "pagination-incomplete",
            f"Cursor scan incomplete after {stats.get('pagination_pages', 1)} pages. "
            "Newer posts may remain unscanned; inspect the latest GitHub Actions logs.",
            cooldown_seconds=1800,
        )
    # Runtime visibility for real-feed quantity validation: log only explicit stacks.
    for listing in listings:
        for side_label, field in (("Propose/receive", "receive_items"), ("Recherche/give", "give_items")):
            for item in listing.get(field, []):
                quantity = item.get("quantity")
                if quantity is not None and quantity > 1:
                    print(
                        f"[quantity] {side_label}: {item['name']} x{quantity} "
                        f"(listing {listing['id']})"
                    )
    alerted = load_alerted()
    alerted_set = set(alerted)
    uncertain_state, expired_uncertain = load_uncertain_state()
    uncertain_ids = set(uncertain_state)
    if expired_uncertain:
        print(
            f"[telegram] {len(expired_uncertain)} uncertain-delivery quarantine(s) expired; "
            "their feed IDs remain marked seen and will not be auto-resent."
        )
        send_maintenance_alert(
            "telegram-quarantine-expired",
            f"{len(expired_uncertain)} Telegram delivery quarantine(s) expired after "
            f"{UNCERTAIN_TTL_SECONDS // 3600} hours. The listings remain marked seen; "
            "check Telegram manually. No automatic resend was performed.",
            cooldown_seconds=86400,
        )
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
    watch_unchanged_skipped = 0
    uncertain_skipped = 0

    def mark_alerted(trade_id):
        if trade_id not in alerted_set:
            alerted.append(trade_id)
            alerted_set.add(trade_id)
            save_alerted(alerted)

    def mark_pending(listing, reason="legacy_unclassified"):
        if listing.get("source") == "discord":
            return  # Discord messages are reloaded from their own bounded source file.
        source_id = str(listing.get("source_ad_id") or "")
        if source_id:
            now = time.time()
            pending_ids.setdefault(source_id, now)
            previous = pending_schedule.get(source_id, {})
            attempts = int(previous.get("attempts", 0))
            if reason in PRIORITY_PENDING_REASONS:
                pending_schedule[source_id] = {
                    "attempts": attempts, "next_check_at": now, "reason": reason,
                }
                if source_id in seen_ads:
                    seen_ads.discard(source_id)
                    save_seen_ads(seen_ads)
            else:
                next_check = previous.get("next_check_at")
                if previous.get("reason") != reason or not next_check:
                    next_check = now + PENDING_RECHECK_BACKOFF_SECONDS[0]
                pending_schedule[source_id] = {
                    "attempts": attempts, "next_check_at": float(next_check), "reason": reason,
                }
            save_pending_recheck_schedule(pending_schedule)
            save_pending_ids(pending_ids)

    def mark_seen_ad(listing, clear_pending=True):
        if listing.get("source") == "discord":
            return  # Never add Discord snowflakes to BloxSwaps seen/pending cursors.
        source_id = str(listing.get("source_ad_id") or "")
        if source_id and source_id not in seen_ads:
            seen_ads.add(source_id)
            save_seen_ads(seen_ads)
        if clear_pending and source_id and source_id in pending_ids:
            pending_ids.pop(source_id, None)
            pending_schedule.pop(source_id, None)
            save_pending_ids(pending_ids)
            save_pending_recheck_schedule(pending_schedule)

    skipped_unknown = 0
    sent = 0
    evaluated = 0
    opportunities = []
    def deliver_opportunity(op):
        nonlocal sent
        global _SESSION_ALERTS_SENT
        listing = op["listing"]
        trade_id = listing["id"]
        # Re-check immediately before every non-idempotent send. A previously
        # alerted stable ad may be sent again only when its content or value
        # fingerprint changed; uncertain Telegram deliveries remain quarantined.
        watch_id = str(listing.get("source_ad_id") or "")
        previous_watch = watch_state.get(watch_id, {})
        current_content_hash, current_values_hash = listing_watch_fingerprints(listing, values, aliases, cash_catalog)
        variant_changed = bool(previous_watch) and (
            previous_watch.get("content_hash") != current_content_hash
            or previous_watch.get("values_hash") != current_values_hash
        )
        if (trade_id in alerted_set and not variant_changed) or trade_id in uncertain_ids:
            print(f"[dedupe] Skipping unchanged or uncertain listing {trade_id} before Telegram send.")
            return
        if os.environ.get("DRY_RUN", "").strip().casefold() == "true":
            print(f"[dry-run] would send: {trade_id} profit={op['difference']:g}")
            return
        listing["alert_attempted_at_utc"] = datetime.now(timezone.utc).isoformat()
        message = format_trade_message(
            listing, op["give_total"], op["give_lines"],
            op["receive_total"], op["receive_lines"], op["difference"]
        )
        if op.get("manual_review"):
            message = (
                "⚠️ MANUAL CHECK — profit is not guaranteed; verify the stack before trading.\n\n"
                + message
            )
            message += (
                f"\n⚠️ POSSIBLE PROFIT RANGE: {format_number(op['profit_worst'])} "
                f"to {format_number(op['profit_best'])}"
            )
        message += f"\n📊 MARGIN: {op['margin']:.2f}%"
        if op.get("receive_demand_score") is not None and op.get("receive_stability_score") is not None:
            message += (
                f"\n📈 GET QUALITY: Demand {op['receive_demand_score']:.2f}/10"
                f" | Stability {op['receive_stability_score']:.2f}/9"
            )
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
        _SESSION_ALERTS_SENT += 1
        try:
            send_telegram(message)
        except TelegramDeliveryUncertain:
            print(
                f"[telegram] Delivery uncertain for listing {trade_id}; "
                "quarantined, skipping this message and continuing the batch."
            )
            # The pre-send guard remains in uncertain_ids; persist the feed ID too.
            mark_seen_ad(listing)
            return
        except Exception as exc:
            uncertain_ids.discard(trade_id)
            save_uncertain_ids(uncertain_ids)
            mark_pending(listing, reason="telegram_failure")
            print(
                f"[telegram] Definite send failure for listing {trade_id}: "
                f"{type(exc).__name__}; queued for a later scan and continuing the batch."
            )
            return
        mark_alerted(trade_id)
        update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
        uncertain_ids.discard(trade_id)
        save_uncertain_ids(uncertain_ids)
        mark_seen_ad(listing)
        listing["alerted_at_utc"] = datetime.now(timezone.utc).isoformat()
        if listing.get("source") != "discord":
            append_history(listing, op)
        first_seen_ts = _parse_feed_timestamp(listing.get("first_seen_at_utc"))
        alerted_ts = _parse_feed_timestamp(listing["alerted_at_utc"])
        detection_latency = (
            max(0.0, alerted_ts - first_seen_ts)
            if first_seen_ts is not None and alerted_ts is not None else None
        )
        latency_label = f"{detection_latency:.2f}s" if detection_latency is not None else "unknown"
        print(
            f"[timing] listing={listing.get('source_ad_id') or trade_id} "
            f"first_seen={listing.get('first_seen_at_utc', 'unknown')} "
            f"alerted={listing['alerted_at_utc']} "
            f"first_seen_to_alert={latency_label}"
        )
        sent += 1
        update_metrics(increments={"alerts_sent": 1})

    for listing in listings:
        trade_id = listing["id"]
        legacy_state = legacy_alert_state(listing, alerted_set, uncertain_ids)
        # One-time migration: old quantity-sensitive hashes may still exist in
        # alerted/history/quarantine state. Promote their new stable ad ID
        # before any Telegram send so the upgrade cannot replay old alerts.
        if legacy_state == "alerted":
            print(f"[state-migration] Legacy alerted ID matched; promoting stable ad ID {trade_id}.")
            mark_alerted(trade_id)
            mark_seen_ad(listing)
            already_alerted_skipped += 1
            continue
        if legacy_state == "uncertain":
            print(f"[state-migration] Legacy uncertain-delivery ID matched; promoting quarantine for {trade_id}.")
            uncertain_ids.add(trade_id)
            save_uncertain_ids(uncertain_ids)
            mark_seen_ad(listing)
            uncertain_skipped += 1
            continue
        watch_id = str(listing.get("source_ad_id") or "")
        previous_watch = watch_state.get(watch_id, {})
        content_hash, values_hash = listing_watch_fingerprints(listing, values, aliases, cash_catalog)
        fingerprint_changed = bool(previous_watch) and (
            previous_watch.get("content_hash") != content_hash
            or previous_watch.get("values_hash") != values_hash
        )
        if trade_id in alerted_set and not fingerprint_changed:
            if not previous_watch:
                update_watch_entry(watch_state, listing, values, aliases)
            already_alerted_skipped += 1
            mark_seen_ad(listing)
            continue
        if previous_watch and not fingerprint_changed and watch_id not in pending_ids:
            watch_unchanged_skipped += 1
            mark_seen_ad(listing)
            continue
        if trade_id in uncertain_ids:
            uncertain_skipped += 1
            mark_seen_ad(listing)
            continue
        result, missing = evaluate_cash_listing(listing, values, aliases, cash_catalog)
        evaluated += 1
        if result is None:
            skipped_unknown += 1
            print(
                f"[skip-unknown] Listing retained for recheck; missing/unvalued items: "
                f"{', '.join(missing)}"
            )
            pending_reason = pending_reason_from_missing(missing)
            mark_pending(listing, reason=pending_reason)
            unknown_names = [
                str(value).split(" (", 1)[0]
                for value in missing
                if value
                and not str(value).startswith("one side has")
                and "quantity unknown" not in str(value).casefold()
                and "quantity uncertain" not in str(value).casefold()
                and "stability" not in str(value).casefold()
            ]
            if unknown_names:
                record_unmatched_names(unknown_names)
            update_watch_entry(watch_state, listing, values, aliases)
            # Low-priority missing values/stability remain queued but leave the
            # hot path. Urgent quantity uncertainty stays visible for quick retry.
            if pending_reason not in PRIORITY_PENDING_REASONS:
                mark_seen_ad(listing, clear_pending=False)
            continue
        if not qualifies_cash(result):
            if result.get("uncertain_items"):
                mark_pending(listing, reason="quantity_uncertain")
            else:
                mark_seen_ad(listing)
            update_watch_entry(watch_state, listing, values, aliases)
            continue
        opportunities.append(result)
        if history_store is not None:
            try:
                history_store.record_opportunity(result)
            except Exception as history_error:
                print(f"[market-history] Opportunity storage skipped safely: {type(history_error).__name__}")
        # Delivery is deferred until all candidates are ranked.

    max_per_scan = max(0, int(os.environ.get("MAX_ALERTS_PER_SCAN", "15")))
    selected, deferred_items = rank_and_split(opportunities, max_per_scan)
    deferred = len(deferred_items)
    if deferred:
        update_metrics(increments={"alerts_deferred": deferred})
    for opportunity in deferred_items:
        if os.environ.get("DRY_RUN", "").strip().casefold() != "true":
            mark_pending(opportunity["listing"], reason="deferred_offer")
    for opportunity in selected:
        deliver_opportunity(opportunity)
    if deferred and os.environ.get("DRY_RUN", "").strip().casefold() != "true":
        try:
            send_telegram(f"{deferred} more qualifying offers deferred to the next scan")
        except Exception as exc:
            print(f"[telegram] Deferred notice failed: {type(exc).__name__}")
    elif deferred:
        print(f"[dry-run] {deferred} qualifying offers deferred")

    summary = (
        f"Parsed={len(listings)} | evaluated={evaluated} | unmatched={skipped_unknown} | "
        f"already_alerted={already_alerted_skipped} | watch_unchanged={watch_unchanged_skipped} | uncertain_quarantined={uncertain_skipped} | "
        f"pending_recheck={len(pending_ids)} | qualified={len(opportunities)} | "
        f"alerts={sent} | deferred={deferred}"
    )
    print(f"[summary] {summary}")
    write_step_summary(summary)
    record_success_if_complete(stats)
    save_cash_catalog_snapshot(cash_catalog["sha256"])


def compare_secondary_values(items, values):
    differences = []
    for item in items:
        record, _source, _candidates = resolve_value_record(item["name"], values, item=item)
        if not record:
            continue
        variant = (record.get("variants") or [record])[0]
        primary, secondary = variant.get("value"), variant.get("secondary_value")
        if primary and secondary and primary > 0:
            pct = abs(primary - secondary) / primary * 100.0
            if pct >= 30:
                differences.append(f"{variant.get('name', item['name'])}: {pct:.0f}%")
    return ", ".join(differences[:5])


def append_history(listing, op):
    from io import StringIO
    fieldnames = [
        "detected_at_utc", "first_seen_at_utc", "alert_attempted_at_utc",
        "alerted_at_utc", "listing_id", "profit_supreme",
        "profit_margin_percent", "you_give", "you_receive", "listing_url", "owner_url",
    ]
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    existing_rows = []
    if HISTORY_FILE.exists():
        try:
            with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames and "listing_id" in reader.fieldnames:
                    existing_rows = list(reader)
        except (OSError, csv.Error):
            raise RuntimeError("[state] History file could not be migrated safely; refusing to overwrite it.")
    writer.writeheader()
    for old_row in existing_rows:
        migrated = {key: old_row.get(key, "") for key in fieldnames}
        migrated["first_seen_at_utc"] = migrated["first_seen_at_utc"] or ""
        migrated["alert_attempted_at_utc"] = migrated["alert_attempted_at_utc"] or old_row.get("detected_at_utc", "")
        migrated["alerted_at_utc"] = migrated["alerted_at_utc"] or old_row.get("detected_at_utc", "")
        writer.writerow(migrated)
    alerted_at = listing.get("alerted_at_utc") or datetime.now(timezone.utc).isoformat()
    writer.writerow({
        "detected_at_utc": alerted_at,
        "first_seen_at_utc": listing.get("first_seen_at_utc", ""),
        "alert_attempted_at_utc": listing.get("alert_attempted_at_utc", alerted_at),
        "alerted_at_utc": alerted_at,
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
    completed_at = datetime.now(timezone.utc).isoformat()
    atomic_write_text(FAILURE_FILE, json.dumps({
        "consecutive_failures": 0,
        "last_success_at_utc": completed_at,
    }, indent=2) + "\n")
    atomic_write_text(LAST_SUCCESS_FILE, json.dumps({
        "completed_at_utc": completed_at,
    }, indent=2) + "\n")


def record_failure(error):
    FAILURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(FAILURE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"consecutive_failures": 0}
    count = int(state.get("consecutive_failures", 0)) + 1
    update_metrics(increments={"scan_failures": 1})
    state["consecutive_failures"] = count
    atomic_write_text(FAILURE_FILE, json.dumps(state, indent=2) + "\n")
    print(f"[failure] Consecutive failures: {count}")
    error_text = str(error)
    safe_reason = redact(error_text)[:240]
    if os.environ.get("DRY_RUN", "").strip().casefold() == "true":
        print("[dry-run] failure alert suppressed")
        return
    if "[rblxvalue-key-blocked]" in error_text:
        block_metrics = rblxvalue_guard._read_json(rblxvalue_guard.BLOCK_FILE)
        update_metrics(increments={"key_blocked_seconds": int(block_metrics.get("blocked_seconds", 0))})
        if rblxvalue_guard.claim_block_alert(key=RBLXVALUE_API_KEY):
            try:
                fingerprint = rblxvalue_guard.key_fingerprint(RBLXVALUE_API_KEY)
                block_state = rblxvalue_guard._read_json(rblxvalue_guard.BLOCK_FILE)
                if block_state.get("permanent"):
                    send_telegram("RBLXValue key rejected 3 times - replace the key or contact RBLXValue on Discord")
                else:
                    send_telegram(
                        "MM2 RBLXValue API CREDENTIAL REJECTED\n"
                        "The upstream rejected the configured credential; calculations remain Fail-Closed.\n"
                        f"Safe diagnostic: {safe_reason}\n"
                        f"Blocked for {int(block_state.get('blocked_seconds', 0)) // 3600} hours or until key rotation.\n"
                        f"Key fingerprint: {fingerprint}"
                    )
                rblxvalue_guard.mark_block_alert_sent(key=RBLXVALUE_API_KEY)
                print("[telegram] RBLXValue key-block alert sent (24-hour cooldown)")
            except Exception as notify_error:
                print(f"[telegram] RBLXValue key-block alert failed: {type(notify_error).__name__}")
        else:
            print("[telegram] RBLXValue key-block alert suppressed by 24-hour cooldown")
        return
    if "[rblxvalue-budget-exhausted]" in error_text:
        print("[rblxvalue-budget] Daily request budget exhausted; repeated-failure alert suppressed")
        return
    if count >= FAILURE_THRESHOLD and (
        count == FAILURE_THRESHOLD or
        (count > FAILURE_THRESHOLD and (count - FAILURE_THRESHOLD) % 5 == 0)
    ):
        safe_error = redact(error)
        try:
            send_telegram(
                "MM2 Trade Scanner ALERT\n"
                f"Failed {count} consecutive runs.\nLatest error: {safe_error[:600]}\n"
                "See GitHub Actions diagnostics."
            )
            print("[telegram] Repeated-failure alert sent")
        except Exception as notify_error:
            print(f"[telegram] Failure alert could not be sent: {type(notify_error).__name__}")


def minimum_cycle_start_window(duration_seconds):
    """Reserve enough time for a full scan cycle based on observed production runtime.

    Production scans take roughly 4.5 minutes; the former 25%/240s reserve
    allowed a new 267s cycle to start with only 243s remaining.
    """
    duration = max(0.1, float(duration_seconds))
    if duration <= 300:
        return max(0.1, duration * 0.25)
    return min(360.0, max(300.0, duration * 0.4))


def should_start_scan_cycle(remaining_seconds, duration_seconds):
    """Do not begin a full scan cycle when the polling window is nearly exhausted."""
    return float(remaining_seconds) >= minimum_cycle_start_window(duration_seconds)

def should_run_backlog_recheck(cycle_number, every=None):
    """Schedule expensive pending/watch work separately from the hot new-feed path."""
    cadence = BACKLOG_RECHECK_EVERY_CYCLES if every is None else max(1, int(every))
    return int(cycle_number) > 0 and int(cycle_number) % cadence == 0


def run_scan_loop(duration_seconds=720, interval_seconds=45):
    """Poll repeatedly in one Actions job while preserving one alert budget per job."""
    global _SESSION_ALERTS_SENT, _SESSION_ALERT_LIMIT
    duration_seconds = max(1, int(duration_seconds))
    interval_seconds = max(30, min(60, int(interval_seconds)))
    _SESSION_ALERTS_SENT = 0
    _SESSION_ALERT_LIMIT = max(0, int(os.environ.get("MAX_ALERTS_PER_SESSION", str(MAX_ALERTS_PER_RUN))))
    started = time.monotonic()
    deadline = started + duration_seconds
    stop = {"requested": False}
    previous_handlers = {}

    def request_stop(signum, frame):
        stop["requested"] = True
        print(f"[loop] Graceful shutdown requested (signal={signum}); finishing current scan cycle.")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass

    cycles = 0
    successful_cycles = 0
    last_failure = None
    try:
        while not stop["requested"] and should_start_scan_cycle(deadline - time.monotonic(), duration_seconds):
            cycle_started = time.monotonic()
            cycles += 1
            print(f"[loop] Starting scan cycle {cycles}; elapsed={cycle_started - started:.1f}s")
            try:
                include_backlog = should_run_backlog_recheck(cycles)
                scan_once(include_backlog_rechecks=include_backlog)
                successful_cycles += 1
            except Exception as exc:
                last_failure = exc
                print(f"[loop] Scan cycle {cycles} failed safely: {type(exc).__name__}; reason={redact(exc)[:240]}")
                record_failure(exc)
            cycle_elapsed = time.monotonic() - cycle_started
            remaining = deadline - time.monotonic()
            print(
                f"[loop] Cycle {cycles} finished in {cycle_elapsed:.1f}s; "
                f"alerts_this_session={_SESSION_ALERTS_SENT}/{_SESSION_ALERT_LIMIT}; "
                f"remaining={max(0, remaining):.1f}s"
            )
            sleep_for = min(max(0.0, interval_seconds - cycle_elapsed), max(0.0, remaining))
            sleep_until = time.monotonic() + sleep_for
            while not stop["requested"] and time.monotonic() < sleep_until:
                time.sleep(min(1.0, sleep_until - time.monotonic()))
    finally:
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
    elapsed = time.monotonic() - started
    rblxvalue_guard.log_request_count()
    update_metrics(increments={"rblxvalue_requests": rblxvalue_guard.requests_this_run()})
    print(f"[loop] Finished: cycles={cycles}, successful={successful_cycles}, elapsed={elapsed:.1f}s")
    if successful_cycles == 0:
        if last_failure and (
            "[rblxvalue-key-blocked]" in str(last_failure)
            or "[rblxvalue-budget-exhausted]" in str(last_failure)
        ):
            raise RuntimeError(str(last_failure))
        raise RuntimeError("No scan cycle completed successfully during the scheduled polling window.")
    return {"cycles": cycles, "successful_cycles": successful_cycles, "elapsed_seconds": elapsed}


def main():
    install_redacting_streams()
    rblxvalue_guard.reset_run_counter()
    if os.environ.get("RBLXVALUE_FORCE_KEY_CHECK", "").strip().casefold() == "true":
        print("[rblxvalue-key-check] Starting one-request credential verification")
        try:
            payload, _, not_modified = api_get_json_conditional("/items", {"limit": 1, "offset": 0})
            if not_modified or not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not payload["items"]:
                raise RuntimeError("[rblxvalue-key-check] API credential check returned no valid item payload")
            print("[rblxvalue-key-check] PASS: credential accepted; one item returned")
        except Exception:
            rblxvalue_guard.log_request_count()
            raise
    duration = int(os.environ.get("MM2_SCAN_DURATION_SECONDS", "0") or 0)
    if duration > 0:
        interval = int(os.environ.get("MM2_SCAN_INTERVAL_SECONDS", "45") or 45)
        return run_scan_loop(duration, interval)
    global _SESSION_ALERTS_SENT, _SESSION_ALERT_LIMIT
    _SESSION_ALERTS_SENT = 0
    _SESSION_ALERT_LIMIT = max(0, int(os.environ.get("MAX_ALERTS_PER_SESSION", str(MAX_ALERTS_PER_RUN))))
    try:
        result = scan_once(include_backlog_rechecks=False)
        rblxvalue_guard.log_request_count()
        update_metrics(increments={"rblxvalue_requests": rblxvalue_guard.requests_this_run()})
        return result
    except Exception:
        rblxvalue_guard.log_request_count()
        update_metrics(increments={"rblxvalue_requests": rblxvalue_guard.requests_this_run()})
        raise

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}; reason={redact(exc)[:300]}", file=sys.stderr)
        record_failure(exc)
        sys.exit(1)
