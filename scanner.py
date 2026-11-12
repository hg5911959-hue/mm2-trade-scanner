#!/usr/bin/env python3
"""BloxSwaps MM2 scanner with verified Supreme Values calculations."""
import csv
import hashlib
import json
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
RBLXVALUE_API_KEY = os.environ.get("RBLXVALUE_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MIN_DIFF = float(os.environ.get("MIN_VALUE_DIFFERENCE", "0"))
MIN_PROFIT_PERCENT = float(os.environ.get("MIN_PROFIT_PERCENT", "5"))
FAILURE_THRESHOLD = int(os.environ.get("FAILURE_ALERT_THRESHOLD", "3"))
SEEN_FILE = Path("data/seen.json")
VALUES_FILE = Path("data/supreme_values.json")
FAILURE_FILE = Path("data/failure_state.json")
HISTORY_FILE = Path("data/history.csv")
MAX_SEEN = 5000
ALERTED_FILE = Path("data/alerted.json")
REVIEWED_FILE = Path("data/reviewed.json")
VALUES_TTL_SECONDS = 4 * 60 * 60
SUSPICIOUS_PROFIT_PERCENT = float(os.environ.get("SUSPICIOUS_PROFIT_PERCENT", "200"))
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


def split_item_label(raw):
    """Return an item name and any quantity embedded in its accessible label."""
    name = (raw or "").strip()
    for prefix in ("Image de ", "Image of ", "Image: "):
        if name.casefold().startswith(prefix.casefold()):
            name = name[len(prefix):]
    name = re.sub(r"\s+(dans|in) Murder Mystery 2$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    quantity = 1
    match = re.search(r"(?:[x×]\s*(\d{1,3})|(\d{1,3})\s*[x×])\s*$", name, flags=re.I)
    if match:
        quantity = max(1, min(int(match.group(1) or match.group(2)), 100))
        name = name[:match.start()].strip()
    ignored = {
        "coin", "offers", "offres", "ajouts", "ajout", "item stability",
        "déclassements", "declassements", "surclassements",
        "downgrades", "upgrades", "murder mystery 2", "mm2", "image"
    }
    if not name or name.casefold() in ignored:
        return "", 1
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


def collect_ordered_events(node):
    result = []
    def visit(n):
        for kind, value in n.events:
            if kind == "text":
                result.append(("text", value))
            elif value.tag == "img":
                name, quantity = split_item_label(value.attrs.get("alt", ""))
                result.append(("img", {"name": name, "quantity": quantity}))
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
        elif kind == "img" and value and value["name"] and side:
            # Quantity may be embedded in alt text or rendered nearby as x3, 3x, ×3.
            quantity = value["quantity"]
            if quantity == 1:
                for next_kind, next_value in events[i + 1:]:
                    if next_kind == "img" and next_value and next_value["name"]:
                        break
                    if next_kind == "text":
                        match = re.search(
                            r"(?:[x×]\s*(\d{1,3})|(\d{1,3})\s*x)\b",
                            next_value, flags=re.I
                        )
                        if match:
                            quantity = max(1, min(int(match.group(1) or match.group(2)), 100))
                            break
            target = received if side == "receive" else requested
            target.append({"name": value["name"], "quantity": quantity})
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
        grouped[key] = grouped.get(key, 0) + max(1, int(item.get("quantity", 1)))
    return sorted((name, quantity) for name, quantity in grouped.items())


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


def parse_page(html):
    parser = PageTree()
    parser.feed(html)
    candidates = smallest_matching_nodes(parser.root, has_trade_sides)
    listings = []
    additions = 0
    malformed = 0
    for node in candidates:
        context = listing_context(node)
        text = node_text(context)
        if has_addition_marker(context):
            additions += 1
            continue
        received, requested = extract_side_items(node)
        if not received or not requested:
            malformed += 1
            continue
        link, owner_url = listing_links(context)
        stable = stable_listing_id(owner_url, received, requested, link, listing_identifier(context))
        listings.append({
            "id": stable,
            "receive_items": received,
            "give_items": requested,
            "url": link,
            "owner_url": owner_url,
            "source_text": text[:700],
        })
    unique = {item["id"]: item for item in listings}
    parsed = list(unique.values())
    return parsed, {
        "candidate_count": len(candidates),
        "addition_count": additions,
        "malformed_count": malformed,
        "owner_link_count": sum(1 for item in parsed if item.get("owner_url")),
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


def normalize_name(name):
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    value = value.casefold().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


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
    cached_values, cached_age = _read_values_cache()
    if cached_values is not None and cached_age < VALUES_TTL_SECONDS:
        print(f"[supreme] Using cached source_b values: {len(cached_values)} items; age {cached_age // 60} min")
        return cached_values

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
                if source_value > 0:
                    secondary = item.get("value_source_a")
                    try:
                        secondary = float(secondary) if secondary is not None else None
                    except (TypeError, ValueError):
                        secondary = None
                    all_values[normalize_name(name)] = {
                        "name": name, "value": source_value, "secondary_value": secondary
                    }
            offset += len(items)
            if len(items) < page_size:
                break
            time.sleep(0.25)
        if len(all_values) < 100:
            raise RuntimeError(f"[supreme] Only {len(all_values)} numeric source_b values loaded; refusing to calculate")
    except Exception as exc:
        if cached_values is not None:
            print(f"[supreme] API refresh failed ({type(exc).__name__}); using stale cache ({cached_age // 3600}h old)")
            return cached_values
        raise

    VALUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(VALUES_FILE, json.dumps({
        "fetched_at": time.time(),
        "source": "Supreme Values via RBLXValue API value_source_b",
        "values": all_values,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"[supreme] Loaded {len(all_values)} numeric Supreme source_b values via API")
    return all_values


def calculate_side(items, values):
    total = 0.0
    breakdown = []
    missing = []

    # Aggregate repeated copies of the same weapon instead of collapsing them
    # to one. Also combine explicit xN stack counts with repeated image entries.
    grouped = {}
    for item in items:
        key = normalize_name(item["name"])
        # These have no numeric Supreme value; ignore them rather than
        # blocking the calculation or reporting them as unmatched.
        if key in IGNORED_VALUE_ITEMS:
            continue
        if key not in grouped:
            grouped[key] = {"name": item["name"], "quantity": 0}
        grouped[key]["quantity"] += max(1, int(item.get("quantity", 1)))

    for key, item in grouped.items():
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
    message = str(message)
    if len(message) > TELEGRAM_MAX_CHARS:
        message = message[:TELEGRAM_MAX_CHARS - 24] + "\n[message truncated]"
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode()
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        raw = request_with_retry(req, 20, "Telegram sendMessage", attempts=4)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("[telegram] Invalid JSON response") from exc
        if result.get("ok"):
            return
        if int(result.get("error_code", 0) or 0) == 429 and attempt < 3:
            retry_after = result.get("parameters", {}).get("retry_after", 2 ** (attempt + 1))
            delay = min(60, max(1, int(retry_after)))
            print(f"[retry] Telegram rate limit; waiting {delay}s")
            time.sleep(delay)
            continue
        raise RuntimeError(f"[telegram] API rejected message: {result.get('description', 'unknown error')}")
    raise RuntimeError("[telegram] Rate limit persisted after retries")


def load_seen():
    if not SEEN_FILE.exists():
        return {"seen": [], "id_version": 1, "fresh": True}
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), list):
            return {"seen": data["seen"], "id_version": int(data.get("id_version", 1)), "fresh": False}
        if isinstance(data, list):
            return {"seen": data, "id_version": 1, "fresh": False}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return {"seen": [], "id_version": 1, "fresh": True}


def save_seen(seen_list):
    ordered = list(dict.fromkeys(str(value) for value in seen_list))[-MAX_SEEN:]
    atomic_write_text(SEEN_FILE, json.dumps({"id_version": 3, "seen": ordered}, indent=2) + "\n")


def load_alerted():
    """Load only successfully alerted listings; unknown or unprofitable offers stay eligible."""
    alerted = set()
    try:
        data = json.loads(ALERTED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("alerted"), list):
            alerted.update(str(value) for value in data["alerted"])
    except (OSError, ValueError, TypeError):
        pass
    # Backfill alerts sent by older scanner versions from the durable trade history.
    try:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("listing_id"):
                    alerted.add(str(row["listing_id"]))
    except (OSError, csv.Error):
        pass
    return alerted


def save_alerted(alerted):
    ordered = sorted(set(str(value) for value in alerted))[-MAX_SEEN:]
    atomic_write_text(ALERTED_FILE, json.dumps({"version": 1, "alerted": ordered}, indent=2) + "\n")


def load_reviewed():
    try:
        data = json.loads(REVIEWED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("reviewed"), list):
            return set(str(value) for value in data["reviewed"])
    except (OSError, ValueError, TypeError):
        pass
    return set()


def save_reviewed(reviewed):
    ordered = sorted(set(str(value) for value in reviewed))[-MAX_SEEN:]
    atomic_write_text(REVIEWED_FILE, json.dumps({"version": 1, "reviewed": ordered}, indent=2) + "\n")


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
        f"TOTAL YOU GIVE: {format_number(give_total)}",
        "",
        "YOU RECEIVE (BloxSwaps: Propose):",
        *(receive_lines or ["• No items"]),
        f"TOTAL YOU RECEIVE: {format_number(receive_total)}",
        "",
        f"SUPREME PROFIT: {difference:+,.2f}".replace(".00", ""),
        f"CALCULATED RESULT: {result}",
        "",
        "Values: Supreme Values source_b via RBLXValue API (Data from rblxvalue.com).",
    ]
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


def evaluate_listing(listing, values):
    give_total, give_lines, give_missing = calculate_side(listing["give_items"], values)
    receive_total, receive_lines, receive_missing = calculate_side(listing["receive_items"], values)
    missing = give_missing + receive_missing
    if missing:
        return None, missing
    difference = receive_total - give_total
    margin = (difference / give_total * 100.0) if give_total > 0 else (100.0 if difference > 0 else 0.0)
    return {
        "listing": listing, "give_total": give_total, "give_lines": give_lines,
        "receive_total": receive_total, "receive_lines": receive_lines,
        "difference": difference, "margin": margin,
    }, []


def qualifies(result, min_diff=None, min_percent=None):
    min_diff = MIN_DIFF if min_diff is None else min_diff
    min_percent = MIN_PROFIT_PERCENT if min_percent is None else min_percent
    return result["difference"] >= min_diff and result["margin"] >= min_percent


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
    print("[stage] Fetching BloxSwaps listings")
    html = fetch_bloxswaps()
    listings, stats = parse_page(html)
    if not listings:
        if stats["candidate_count"] > 0 and stats["addition_count"] == stats["candidate_count"]:
            print(f"[bloxswaps] No eligible listings: all {stats['addition_count']} candidate cards contained Ajouts.")
            record_success()
            write_step_summary("Successful scan: all visible candidates excluded for Ajouts.")
            return
        if stats["candidate_count"] == 0:
            raise RuntimeError("[bloxswaps] No trade cards parsed; page structure may have changed or feed is empty.")
        raise RuntimeError(f"[bloxswaps] Parser could not read {stats['malformed_count']} non-Ajouts cards.")
    print(f"[bloxswaps] Eligible listings after Ajouts filter: {len(listings)}")
    print(f"[bloxswaps] Direct owner profiles parsed: {stats.get('owner_link_count', 0)}/{len(listings)}")
    alerted = load_alerted()
    reviewed = load_reviewed()

    def mark_alerted(trade_id):
        if trade_id not in alerted:
            alerted.add(trade_id)
            save_alerted(alerted)

    def mark_reviewed(trade_id):
        if trade_id not in reviewed:
            reviewed.add(trade_id)
            save_reviewed(reviewed)

    skipped_unknown = 0
    sent = 0
    evaluated = 0
    opportunities = []
    for listing in listings:
        trade_id = listing["id"]
        if trade_id in alerted:
            continue
        result, missing = evaluate_listing(listing, values)
        evaluated += 1
        if result is None:
            skipped_unknown += 1
            print(f"[retry-next-scan] Unmatched Supreme item(s): {', '.join(missing)}")
            # Alert once for manual inspection, but keep the listing eligible for
            # a later fully-valued opportunity alert.
            if trade_id not in reviewed:
                send_telegram(format_unvalued_message(listing, missing))
                mark_reviewed(trade_id)
            continue
        if not qualifies(result):
            # Re-evaluate while visible; source values may change during the listing's lifetime.
            continue
        opportunities.append(result)

    opportunities.sort(key=lambda item: (item["difference"], item["margin"]), reverse=True)
    for op in opportunities:
        listing = op["listing"]
        message = format_trade_message(
            listing, op["give_total"], op["give_lines"],
            op["receive_total"], op["receive_lines"], op["difference"]
        )
        message += f"\nPROFIT MARGIN: {op['margin']:.2f}%"
        if op["margin"] >= SUSPICIOUS_PROFIT_PERCENT:
            message += "\nWARNING: unusually high profit - verify item names and values before trading."
        secondary_note = compare_secondary_values(listing["give_items"] + listing["receive_items"], values)
        if secondary_note:
            message += "\nSECOND-SOURCE CHECK (MM2Values): " + secondary_note
        print(f"[telegram] Sending ranked trade; profit={op['difference']:g}")
        send_telegram(message)
        mark_alerted(listing["id"])
        append_history(listing, op)
        sent += 1

    summary = f"Parsed={len(listings)} | evaluated={evaluated} | unmatched={skipped_unknown} | qualified={len(opportunities)} | alerts={sent}"
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
