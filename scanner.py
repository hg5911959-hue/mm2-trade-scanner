#!/usr/bin/env python3
"""BloxSwaps MM2 scanner with verified Supreme Values calculations."""
import csv
from dataclasses import dataclass, field as dataclass_field
import html as html_lib
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
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlsplit, urlunsplit, parse_qs

import rblxvalue_guard
from tools.scanner_config import CONFIG
from tools.telegram_transport import TELEGRAM_OPENER, telegram_alert_delay
from tools.telegram_delivery import (
    TelegramDeliveryUncertain as TelegramDeliveryUncertain,
    TelegramHTML as TelegramHTML,
    send_telegram as _send_telegram,
)
from tools.listing_identity import (
    identity_items as _identity_items_impl,
    listing_identifier as _listing_identifier,
    legacy_alert_state as _legacy_alert_state,
    legacy_stable_listing_id as _legacy_stable_listing_id,
    stable_listing_id as _stable_listing_id,
)
from tools.item_parser import (
    ARABIC_DIGITS as ARABIC_DIGITS,
    MAX_ITEM_QUANTITY as MAX_ITEM_QUANTITY,
    _explicit_stack_quantities as _explicit_stack_quantities,
    _explicit_stack_quantity as _explicit_stack_quantity,
    _has_unreadable_quantity_hint as _has_unreadable_quantity_hint,
    _item_quantity_details as _item_quantity_details,
    _item_wrapper as _item_wrapper,
    _listed_price as _listed_price,
    _normalize_quantity_text as _normalize_quantity_text,
    _quantity_marker as _quantity_marker,
    _semantic_badge_quantity as _semantic_badge_quantity,
    _side_marker_count as _side_marker_count,
    _structured_quantity_from_nodes as _structured_quantity_from_nodes,
    clean_item_name as clean_item_name,
    collect_images_in_order as collect_images_in_order,
    collect_ordered_events as collect_ordered_events,
    extract_side_items as extract_side_items,
    has_addition_marker as has_addition_marker,
    quantity_from_image_node as quantity_from_image_node,
    requested_wildcard_kind as requested_wildcard_kind,
    split_item_label as split_item_label,
)
from tools.html_tree import (
    Node,  # noqa: F401 - compatibility export
    PageTree,
    has_trade_sides,
    node_text,
    smallest_matching_nodes,
    walk_nodes,
)
from tools.metrics import update_metrics
from tools.alert_policy import rank_and_split
from tools.cash_market import calculate_cash_side, cash_profit_bounds, normalize_cash_name, cash_price_is_fresh, strict_quantity
from tools.robase_identity import build_robase_bloxswaps_map
from tools.daily_chart_shadow import build_daily_chart_shadow_catalog
from tools.rblxvalue_value_profit_shadow import load_values_from_snapshot
from tools.supreme_telegram_info import load_telegram_market_info
from tools.scan_loop import BloxSwapsRateLimitError, run_scan_loop as _run_scan_loop
from tools.state_store import atomic_write_text
from tools.http_retry import request_with_retry as _request_with_retry
from tools.bloxswaps_api import (
    api_post_is_single_sided as _api_post_is_single_sided_impl,
    api_trade_post_to_listing as _api_trade_post_to_listing_impl,
)
from tools.redaction import (
    RedactingStream as _RedactingStream,
    install_redacting_streams as _install_redacting_streams,
    redact as _redact,
)
from tools.opportunity_policy import qualifies as _qualifies_impl
from tools.feed_checkpoint import (
    parse_feed_timestamp as _parse_feed_timestamp_impl,
    scan_checkpoint_requires_cold_start as _scan_checkpoint_requires_cold_start_impl,
)
from tools.telegram_quarantine import reconcile_uncertain_quarantine
from tools.scan_modes import ScanModes, resolve_scan_modes
from tools.scan_loop_policy import (
    minimum_cycle_start_window as _minimum_cycle_start_window,
    should_run_backlog_recheck as _should_run_backlog_recheck,
    should_start_scan_cycle as _should_start_scan_cycle,
)


def load_cash_price_catalog(*args, **kwargs):
    """Lazily import the cash catalogue loader only when cash mode is enabled."""
    from tools.cash_market import load_cash_price_catalog as loader
    return loader(*args, **kwargs)

def load_robase_cash_price_catalog(*args, **kwargs):
    """Lazily load ROBASE prices; legacy fallback is restricted to isolated tests."""
    from tools.cash_market import load_robase_price_catalog as loader
    try:
        return loader(*args, **kwargs)
    except RuntimeError:
        if os.environ.get("MM2_TESTING", "").strip().casefold() == "true":
            # Existing scanner tests exercise legacy fixture catalogues. Production
            # workflows never set MM2_TESTING and therefore fail closed without ROBASE.
            return load_cash_price_catalog(CASH_PRICE_FILE)
        raise


BLOXSWAPS_URL = CONFIG.bloxswaps_url
RBLXVALUE_API_BASE = "https://api.rblxvalue.com/v2"
BLOXSWAPS_API_BASE = "https://api.bloxswaps.com"
RBLXVALUE_API_KEY = CONFIG.rblxvalue_api_key
BOT_TOKEN = CONFIG.telegram_bot_token
CHAT_ID = CONFIG.telegram_chat_id
MIN_DIFF = CONFIG.min_value_difference
MIN_PROFIT_PERCENT = CONFIG.min_profit_percent
MIN_RECEIVE_DEMAND_SCORE = CONFIG.min_receive_demand_score
MIN_RECEIVE_STABILITY_SCORE = CONFIG.min_receive_stability_score
FAILURE_THRESHOLD = CONFIG.failure_alert_threshold
MAX_ALERTS_PER_RUN = CONFIG.max_alerts_per_run
_SESSION_ALERTS_SENT = 0
MAX_ALERTS_PER_SCAN = CONFIG.max_alerts_per_scan
MAX_ALERTS_PER_SESSION = CONFIG.max_alerts_per_session
SCAN_DURATION_SECONDS = CONFIG.scan_duration_seconds
SCAN_INTERVAL_SECONDS = CONFIG.scan_interval_seconds
_SESSION_ALERT_LIMIT = MAX_ALERTS_PER_SESSION
TELEGRAM_CHAT_TYPE = CONFIG.telegram_chat_type


ALERT_DELAY_SECONDS = telegram_alert_delay(
    TELEGRAM_CHAT_TYPE, CONFIG.alert_delay_seconds
)
VALUES_FILE = Path("data/supreme_values.json")
VALUES_AUDIT_FILE = Path("data/supreme_values_audit.json")
RBLXVALUE_KEY_HEALTH_FILE = Path("data/rblxvalue_key_health.json")
RBLXVALUE_KEY_HEALTH_INTERVAL_SECONDS = 6 * 60 * 60
RBLXVALUE_KEY_HEALTH_RETRY_SECONDS = 3 * 60 * 60
FAILURE_FILE = Path("data/failure_state.json")
HISTORY_FILE = Path("data/history.csv")
MAX_SEEN_ADS = 50000
MAX_ALERTED = MAX_SEEN_ADS
ALERTED_FILE = Path("data/alerted.json")
UNCERTAIN_FILE = Path("data/telegram_uncertain.json")
UNCERTAIN_CONTEXT_FILE = Path("data/telegram_uncertain_context.json")
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
MAX_PENDING_RECHECK_PER_SCAN = CONFIG.max_pending_recheck_per_scan
MAX_BACKLOG_RECHECK_PAGES = CONFIG.max_backlog_recheck_pages
BACKLOG_RECHECK_EVERY_CYCLES = CONFIG.backlog_recheck_every_cycles
FIRST_SEEN_FILE = Path("data/first_seen_ads.json")
LAST_SUCCESS_FILE = Path("data/last_successful_scan.json")
WATCH_FILE = Path("data/watch_ads.json")
WATCH_TTL_SECONDS = 24 * 60 * 60
MAX_WATCHED_ADS = MAX_SEEN_ADS
ALIASES_FILE = Path("aliases.json")
UNMATCHED_FILE = Path("data/unmatched_names.json")
MAINTENANCE_FILE = Path("data/maintenance_alerts.json")
BLOXSWAPS_PAGE_SIZE = 20
MAX_FEED_PAGES = CONFIG.max_feed_pages
VALUES_REVALIDATION_INTERVAL_SECONDS = 24 * 60 * 60
_CASH_FILTER_REQUESTED = CONFIG.cash_filter
_DEFAULT_VALUES_TTL_SECONDS = CONFIG.default_values_ttl_seconds
RBLXVALUE_META_POLL_INTERVAL_SECONDS = 2 * 60 * 60
RBLXVALUE_META_RETRY_SECONDS = 2 * 60 * 60
RBLXVALUE_META_FILE = Path("data/rblxvalue_meta.json")
RBLXVALUE_DETAIL_FILE = Path("data/rblxvalue_detail_enrichment.json")
RBLXVALUE_MAX_DETAIL_LOOKUPS_PER_DAY = CONFIG.rblxvalue_max_detail_lookups_per_day
RBLXVALUE_DETAIL_RETRY_SECONDS = 6 * 60 * 60
RBLXVALUE_META_POLL_ENABLED = CONFIG.rblxvalue_meta_poll_enabled
VALUES_TTL_SECONDS = CONFIG.values_ttl_seconds
BLOXSWAPS_ITEMS_TTL_SECONDS = CONFIG.bloxswaps_items_ttl_seconds
PENDING_TTL_SECONDS = CONFIG.pending_ttl_seconds
MAX_SUCCESS_GAP_SECONDS = CONFIG.max_success_gap_seconds
MAX_UNCERTAIN_DELIVERIES = 500
UNCERTAIN_REVIEW_AFTER_SECONDS = 24 * 60 * 60
BLOXSWAPS_ITEMS_FILE = Path("data/bloxswaps_items.json")
SUSPICIOUS_PROFIT_PERCENT = CONFIG.suspicious_profit_percent


def cash_filter_enabled(value=None):
    raw = os.environ.get("CASH_FILTER", "false") if value is None else value
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


CASH_FILTER = CONFIG.cash_filter
CASH_MIN_PROFIT_USD = CONFIG.cash_min_profit_usd
CASH_MIN_PROFIT_USD_SMALL = CONFIG.cash_min_profit_usd_small
CASH_MIN_VERIFIED_ROWS = CONFIG.cash_min_verified_rows
CASH_MIN_MARGIN_PERCENT = CONFIG.cash_min_margin_percent
CASH_MAX_AGE_DAYS = CONFIG.cash_max_age_days
CASH_MAX_LISTING_AGE_SECONDS = CONFIG.cash_max_listing_age_seconds
CASH_PRICE_FILE = Path("data/cash_prices.csv")
CASH_PRICE_SNAPSHOT_FILE = Path("data/cash_price_snapshot.json")
TELEGRAM_MAX_CHARS = 4000
_LAST_TELEGRAM_SEND_AT = 0.0
_LAST_ITEMS_FORCE_REFRESH_AT = 0.0

# Items without a numeric Supreme value are excluded from valuation.
IGNORED_VALUE_ITEMS = {"brew", "drool"}


def redact(text):
    """Compatibility wrapper that supplies the scanner's configured secrets."""
    return _redact(
        text,
        secrets=(BOT_TOKEN, CHAT_ID, RBLXVALUE_API_KEY),
        cash_filter=CASH_FILTER,
        environ=os.environ,
    )


class RedactingStream(_RedactingStream):
    """Compatibility stream wrapper bound to the scanner's redactor."""

    def __init__(self, stream):
        super().__init__(stream, redact)


def install_redacting_streams():
    """Install secret-safe stdout/stderr filters once, before scanner output."""
    _install_redacting_streams(RedactingStream)
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
    """Compatibility wrapper for the extracted listing identity helper."""
    return _identity_items_impl(items, normalize_name)


def listing_identifier(node):
    """Compatibility wrapper for stable source-card identifiers."""
    return _listing_identifier(node)


def legacy_stable_listing_id(owner_url, receive_items, give_items, listing_url="", listing_key_hint=""):
    return _legacy_stable_listing_id(
        owner_url, receive_items, give_items, listing_url, listing_key_hint,
        normalize_name=normalize_name,
    )


def legacy_alert_state(listing, alerted_ids, uncertain_ids):
    return _legacy_alert_state(listing, alerted_ids, uncertain_ids)


def stable_listing_id(owner_url, receive_items, give_items, listing_url="", listing_key_hint=""):
    return _stable_listing_id(
        owner_url, receive_items, give_items, listing_url, listing_key_hint,
        normalize_name=normalize_name,
    )


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
            if quantity is None or not 1 <= quantity <= MAX_ITEM_QUANTITY:
                _uncertain_quantity(item, 'invalid structured quantity')
            elif html_source == 'uncertain':
                _uncertain_quantity(item, 'HTML quantity marker is ambiguous; structured data cannot override the conflict')
            elif html_quantity != quantity:
                known = [int(v) for v in (html_quantity, quantity) if v is not None and str(v).isdigit() and 1 <= int(v) <= MAX_ITEM_QUANTITY]
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
    offers_excluded = 0
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
        wildcard_kind = requested_wildcard_kind((structured or {}).get("requestedWildcards") or [])
        # Check the single-card context before attempting side parsing. Never
        # inspect the whole feed container for an Ajout marker.
        if has_addition_marker(context) or wildcard_kind == "ajout":
            additions += 1
            if source_ad_id:
                addition_ids.append(source_ad_id)
            continue
        if wildcard_kind == "offres":
            offers_excluded += 1
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
        "offers_excluded_count": offers_excluded,
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


RBLXVALUE_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
BLOXSWAPS_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RETRY_AFTER_SECONDS = 120


class _RejectCredentialRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the RBLXValue API key to a redirect target."""

    handler_order = 100

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(
            req.full_url,
            code,
            "Redirect refused for credential-bearing RBLXValue request",
            headers,
            fp,
        )


def _open_rblxvalue_url(req, timeout):
    # urllib's default redirect handler can copy custom headers to a new host.
    # RBLXValue requests carry X-Api-Key, so reject redirects before following.
    opener = urllib.request.build_opener(_RejectCredentialRedirects())
    return opener.open(req, timeout=timeout)


def request_with_retry(req, timeout, label, attempts=4, return_headers=False, allow_not_modified=False):
    """Compatibility wrapper for the extracted bounded HTTP retry policy."""
    return _request_with_retry(
        req,
        timeout,
        label,
        attempts=attempts,
        return_headers=return_headers,
        allow_not_modified=allow_not_modified,
        rblxvalue_guard=rblxvalue_guard,
        rblxvalue_api_key=RBLXVALUE_API_KEY,
        rblxvalue_max_response_bytes=RBLXVALUE_MAX_RESPONSE_BYTES,
        bloxswaps_max_response_bytes=BLOXSWAPS_MAX_RESPONSE_BYTES,
        open_rblxvalue_url=_open_rblxvalue_url,
        redact=redact,
        max_retry_after_seconds=MAX_RETRY_AFTER_SECONDS,
        rate_limit_error=BloxSwapsRateLimitError,
    )
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


def fetch_pending_posts_by_id(pending_ids):
    """Resolve a bounded pending batch directly instead of crawling old feed cursors."""
    posts = []
    missing_ids = []
    pending_batch = sorted({str(value) for value in (pending_ids or ())})
    for index, pending_id in enumerate(pending_batch):
        if index:
            # Keep bounded detail lookups gentle on the public BloxSwaps API.
            time.sleep(0.2)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pending_id):
            raise RuntimeError("[pending] Refusing an invalid pending listing ID.")
        try:
            payload = bloxswaps_api_get_json(f"/trade-posts/{pending_id}")
        except RuntimeError as exc:
            if "HTTP 404:" in str(exc):
                missing_ids.append(pending_id)
                continue
            raise
        if not isinstance(payload, dict) or str(payload.get("id") or "") != pending_id:
            print(
                f"[pending] Direct lookup returned an invalid or mismatched record for {pending_id}; "
                "retaining it for a later bounded direct-ID retry."
            )
            continue
        posts.append(payload)
    return posts, missing_ids


BLOXSWAPS_IDENTITY_FIELDS = (
    "id", "slug", "displayName", "chroma", "rarity", "type", "tradable", "year",
)


def _bloxswaps_identity_record(item):
    """Keep only fields required for item identity and tradability decisions."""
    if not isinstance(item, dict):
        return {}
    return {field: item[field] for field in BLOXSWAPS_IDENTITY_FIELDS if field in item}


def fetch_bloxswaps_items_map(force_refresh=False):
    global _LAST_ITEMS_FORCE_REFRESH_AT
    force_allowed = True
    if force_refresh:
        now = time.time()
        force_allowed = now - _LAST_ITEMS_FORCE_REFRESH_AT >= 600
        if force_allowed:
            _LAST_ITEMS_FORCE_REFRESH_AT = now
        else:
            print("[bloxswaps] Unknown item ID refresh is cooling down (10 minutes); retaining safe unmatched status")
    if BLOXSWAPS_ITEMS_FILE.exists():
        try:
            cache = json.loads(BLOXSWAPS_ITEMS_FILE.read_text(encoding="utf-8"))
            age = max(0, int(time.time() - float(cache.get("fetched_at", 0))))
            raw_cached = cache.get("items")
            cached = (
                {str(item_id): _bloxswaps_identity_record(item)
                 for item_id, item in raw_cached.items() if _bloxswaps_identity_record(item).get("displayName")}
                if isinstance(raw_cached, dict) else {}
            )
            if cached and cached != raw_cached:
                atomic_write_text(BLOXSWAPS_ITEMS_FILE, json.dumps({
                    "fetched_at": cache.get("fetched_at", time.time()), "items": cached,
                }, ensure_ascii=False) + "\n")
                print("[bloxswaps] Removed non-identity fields from cached catalogue")
            if cached and force_refresh and not force_allowed:
                print(f"[bloxswaps] Catalogue force-refresh suppressed by 10-minute cooldown; reusing {len(cached)} identity records")
                return cached
            if not force_refresh and age <= BLOXSWAPS_ITEMS_TTL_SECONDS and cached:
                print(f"[bloxswaps] Using fresh item identity catalogue ({len(cached)} records; age {age}s)")
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
            mapping[str(item["id"])] = _bloxswaps_identity_record(item)
    if not mapping:
        raise RuntimeError("[bloxswaps] Item catalogue has no id/displayName mapping.")
    BLOXSWAPS_ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(BLOXSWAPS_ITEMS_FILE, json.dumps({
        "fetched_at": time.time(), "items": mapping,
    }, ensure_ascii=False) + "\n")
    print(f"[bloxswaps] Refreshed {len(mapping)} identity-only records from site catalogue")
    return mapping


def _api_post_is_single_sided(post):
    """Compatibility wrapper for structured BloxSwaps side classification."""
    return _api_post_is_single_sided_impl(post)


def _api_trade_post_to_listing(post, item_map):
    """Compatibility wrapper for structured BloxSwaps post adaptation."""
    return _api_trade_post_to_listing_impl(
        post,
        item_map,
        strict_quantity=strict_quantity,
        stable_listing_id=stable_listing_id,
        legacy_stable_listing_id=legacy_stable_listing_id,
    )


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
                known = [int(v) for v in (html_quantity, api_quantity) if v is not None and str(v).isdigit() and 1 <= int(v) <= MAX_ITEM_QUANTITY]
                _uncertain_quantity(api_item, f'HTML quantity {html_quantity} disagrees with RSC quantity {api_quantity}')
                api_item['quantity_min'] = min(known) if known else 1
                api_item['quantity_max'] = max(known) if known else None
    return api_listing

def _parse_feed_timestamp(raw):
    """Compatibility wrapper for the pure feed timestamp parser."""
    return _parse_feed_timestamp_impl(raw)


def _last_successful_scan_timestamp():
    if not LAST_SUCCESS_FILE.exists():
        return None
    try:
        state = json.loads(LAST_SUCCESS_FILE.read_text(encoding="utf-8"))
        return _parse_feed_timestamp(state.get("completed_at_utc"))
    except (OSError, ValueError, TypeError):
        raise RuntimeError("[state] Last-success checkpoint is unreadable; refusing unsafe pagination boundary.")


def scan_checkpoint_requires_cold_start(last_success_ts, now=None):
    """Compatibility wrapper for the pure feed checkpoint policy."""
    return _scan_checkpoint_requires_cold_start_impl(
        last_success_ts, now=now, max_success_gap_seconds=MAX_SUCCESS_GAP_SECONDS
    )


@dataclass(slots=True)
class InitialBloxSwapsFeed:
    html: str | None
    initial_page: dict | None
    initial_item_map: dict
    raw_first_items: list
    single_sided_ids: list
    single_sided_count: int
    first_listings: list
    stats: dict


def _refresh_item_catalogue_for_unknown_ids(unknown_ids, item_map):
    """Refresh missing BloxSwaps item IDs without ever guessing item names."""
    if not unknown_ids:
        return item_map
    try:
        refreshed_map = fetch_bloxswaps_items_map(force_refresh=True)
        if refreshed_map:
            item_map = refreshed_map
        still_unknown = sorted(unknown_ids - set(item_map.keys()))
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
    return item_map


def _refresh_item_catalogue_for_post(post, item_map):
    """Refresh the catalogue when a post references item IDs absent from the cache."""
    unknown_ids = {
        str(row.get("itemDetailsId") or "")
        for row in (post.get("tradePostItems") or [])
        if isinstance(row, dict)
        and row.get("itemDetailsId")
        and str(row.get("itemDetailsId")) not in item_map
    }
    return _refresh_item_catalogue_for_unknown_ids(unknown_ids, item_map)


def _load_initial_bloxswaps_feed(api_only: bool) -> InitialBloxSwapsFeed:
    """Fetch and validate the first feed page plus its item catalogue."""
    if api_only:
        initial_page = bloxswaps_api_get_json("/trade-posts", {"pageSize": BLOXSWAPS_PAGE_SIZE})
        if not isinstance(initial_page, dict) or not isinstance(initial_page.get("items"), list):
            raise RuntimeError(
                "[bloxswaps] Initial trade-post API page is invalid; refusing an incomplete feed."
            )
        html = None
    else:
        html = fetch_bloxswaps()
        initial_page = parse_structured_initial_page(html)

    structured_rows = [
        row
        for post in (initial_page.get("items") or []) if isinstance(post, dict)
        for row in (post.get("tradePostItems") or []) if isinstance(row, dict)
    ] if initial_page else []
    needs_catalogue = any(row.get("itemDetailsId") for row in structured_rows)
    initial_item_map = fetch_bloxswaps_items_map() if needs_catalogue else {}
    raw_first_items = initial_page.get("items") or [] if initial_page else []
    single_sided_ids = []
    single_sided_count = 0
    first_page_unknown_ids = {
        str(row.get("itemDetailsId") or "")
        for post in raw_first_items if isinstance(post, dict)
        for row in (post.get("tradePostItems") or []) if isinstance(row, dict)
        if row.get("itemDetailsId")
        and str(row.get("itemDetailsId")) not in initial_item_map
    }
    if first_page_unknown_ids:
        initial_item_map = _refresh_item_catalogue_for_unknown_ids(
            first_page_unknown_ids, initial_item_map
        )

    if api_only:
        first_listings = []
        stats = {
            "pagination_pages": 1,
            "pagination_complete": False,
            "pagination_source": "BloxSwaps API only",
            "addition_ids": [],
            "unparsed_ad_ids": [],
            "html_parse_warning_ids": [],
            "malformed_count": 0,
            "malformed_samples": [],
        }
    else:
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
            raise RuntimeError(
                "[bloxswaps] RSC initialPage/cursor data missing; refusing a partial HTML-only scan."
            )

    return InitialBloxSwapsFeed(
        html=html,
        initial_page=initial_page,
        initial_item_map=initial_item_map,
        raw_first_items=raw_first_items,
        single_sided_ids=single_sided_ids,
        single_sided_count=single_sided_count,
        first_listings=first_listings,
        stats=stats,
    )


@dataclass(slots=True)
class ColdStartFeedState:
    initial_feed: InitialBloxSwapsFeed
    api_only: bool
    cutoff: float
    seed_ids: list
    recent_ids: list
    recent_listings: list
    addition_ids: list
    unparsed_ids: list
    unidentified_feed_samples: list
    additions: int
    offers_excluded: int
    malformed: int
    candidate_count: int
    parsed_candidate_count: int
    malformed_candidate_count: int
    unidentified_feed_records: int
    api_posts: int
    old_streak: int
    page_count: int
    age_boundary: bool
    complete: bool
    seen_raw_ids: set
    page_items: list
    cursor: str | None
    by_ad_id: dict


def _consume_cold_start_page(state: ColdStartFeedState) -> None:
    """Classify one cold-start page and collect only verifiably recent listings."""
    for post in state.page_items:
        if not isinstance(post, dict) or not str(post.get("id") or "").strip():
            state.malformed += 1
            state.candidate_count += 1
            state.malformed_candidate_count += 1
            state.unidentified_feed_records += 1
            state.unidentified_feed_samples.append(
                "object-without-stable-id" if isinstance(post, dict)
                else f"invalid-record-type:{type(post).__name__}"
            )
            continue
        post_id = str(post["id"])
        if post_id in state.seen_raw_ids:
            continue
        state.seen_raw_ids.add(post_id)
        refreshed_ts = _parse_feed_timestamp(
            post.get("lastRefreshedAt") or post.get("createdAt") or post.get("created_at")
        )
        is_pinned = bool(post.get("isPinned") or post.get("pinned"))
        if refreshed_ts is None:
            state.seed_ids.append(post_id)
            state.old_streak = 0
            continue
        if refreshed_ts < state.cutoff:
            state.seed_ids.append(post_id)
            state.old_streak = 0 if is_pinned else state.old_streak + 1
            if state.old_streak >= 25:
                state.age_boundary = True
                state.complete = True
                break
            continue

        state.old_streak = 0
        state.recent_ids.append(post_id)
        state.candidate_count += 1
        wildcard_kind = requested_wildcard_kind(post.get("requestedWildcards") or [])
        if wildcard_kind == "ajout":
            state.additions += 1
            state.addition_ids.append(post_id)
            continue
        if state.api_only and _api_post_is_single_sided(post):
            state.initial_feed.single_sided_count += 1
            state.initial_feed.single_sided_ids.append(post_id)
            continue
        if wildcard_kind == "offres":
            state.offers_excluded += 1
            continue

        state.initial_feed.initial_item_map = _refresh_item_catalogue_for_post(
            post, state.initial_feed.initial_item_map
        )
        listing = _api_trade_post_to_listing(
            post, state.initial_feed.initial_item_map
        )
        if listing:
            html_listing = state.by_ad_id.get(post_id)
            if html_listing:
                listing = _merge_html_quantity_diagnostics(listing, html_listing)
            state.recent_listings.append(listing)
            state.parsed_candidate_count += 1
        else:
            state.malformed += 1
            state.malformed_candidate_count += 1
            state.unparsed_ids.append(post_id)


def _fetch_cold_start_pages(state: ColdStartFeedState) -> None:
    """Follow the cursor until the verified age boundary or the safe page cap."""
    while True:
        _consume_cold_start_page(state)
        if state.age_boundary:
            break
        if not state.cursor:
            state.complete = True
            break
        if state.page_count >= MAX_FEED_PAGES:
            state.complete = False
            break
        page = bloxswaps_api_get_json(
            "/trade-posts",
            {"cursor": state.cursor, "pageSize": BLOXSWAPS_PAGE_SIZE},
        )
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise RuntimeError(
                "[bloxswaps] Cursor API returned an invalid page during cold-start discovery."
            )
        state.page_items = page.get("items") or []
        state.api_posts += len(state.page_items)
        state.page_count += 1
        next_cursor = (page.get("meta") or {}).get("cursor")
        if next_cursor and next_cursor == state.cursor:
            raise RuntimeError(
                "[bloxswaps] Cursor did not advance during cold-start discovery; "
                "refusing to seed an incomplete feed."
            )
        state.cursor = next_cursor


def _finalize_cold_start_feed(state: ColdStartFeedState):
    """Validate cold-start accounting and assemble the feed result."""
    from tools.feed_accounting import validate_candidate_accounting

    single_sided_count = state.initial_feed.single_sided_count
    validate_candidate_accounting(
        candidate_count=state.candidate_count,
        additions=state.additions,
        single_sided=single_sided_count,
        offers_excluded=state.offers_excluded,
        malformed=state.malformed_candidate_count,
        parsed=state.parsed_candidate_count,
        context="cold-start feed",
    )
    stats = state.initial_feed.stats
    stats.update({
        "parsed_candidate_count": state.parsed_candidate_count,
        "malformed_candidate_count": state.malformed_candidate_count,
        "pagination_pages": state.page_count,
        "pagination_api_posts": state.api_posts,
        "pagination_complete": state.complete,
        "pagination_source": "api-cold-start" if state.api_only else "rsc-cold-start",
        "seen_boundary_reached": state.age_boundary,
        "seen_streak": state.old_streak,
        "cold_start": True,
        "cold_start_seed_ids": state.seed_ids,
        "cold_start_recent_ids": state.recent_ids,
        "candidate_count": state.candidate_count,
        "unidentified_feed_record_count": state.unidentified_feed_records,
        "unidentified_feed_samples": state.unidentified_feed_samples[:10],
        "addition_count": state.additions,
        "offers_excluded_count": state.offers_excluded,
        "malformed_count": state.malformed,
        "addition_ids": state.addition_ids,
        "single_sided_count": single_sided_count,
        "single_sided_ids": state.initial_feed.single_sided_ids,
        "unparsed_ad_ids": state.unparsed_ids,
        "pending_remaining_ids": [],
        "watch_remaining_ids": [],
        "owner_link_count": sum(
            1 for item in state.recent_listings if item.get("owner_url")
        ),
    })
    discovered = state.seed_ids + state.recent_ids
    return state.recent_listings, stats, discovered


def _run_cold_start_feed(
    initial_feed: InitialBloxSwapsFeed, api_only: bool
):
    """Run the bounded cold-start feed discovery stage."""
    raw_first_items = initial_feed.raw_first_items
    now_ts = datetime.now(timezone.utc).timestamp()
    state = ColdStartFeedState(
        initial_feed=initial_feed,
        api_only=api_only,
        cutoff=now_ts - 3600,
        seed_ids=[],
        recent_ids=[],
        recent_listings=[],
        addition_ids=[],
        unparsed_ids=[],
        unidentified_feed_samples=[],
        additions=0,
        offers_excluded=0,
        malformed=0,
        candidate_count=0,
        parsed_candidate_count=0,
        malformed_candidate_count=0,
        unidentified_feed_records=0,
        api_posts=len(raw_first_items) if api_only else 0,
        old_streak=1,
        page_count=1,
        age_boundary=False,
        complete=False,
        seen_raw_ids=set(),
        page_items=raw_first_items,
        cursor=(initial_feed.initial_page.get("meta") or {}).get("cursor"),
        by_ad_id={
            str(item.get("source_ad_id")): item
            for item in initial_feed.first_listings
            if item.get("source_ad_id")
        },
    )
    _fetch_cold_start_pages(state)
    return _finalize_cold_start_feed(state)


@dataclass(slots=True)
class IncrementalFeedState:
    seen: set
    pending_remaining: set
    watch_ids: set
    watch_remaining: set
    by_ad_id: dict
    html_addition_ids: set
    last_success_ts: float | None
    api_posts_scanned: int
    item_map: dict | None
    single_sided_count: int
    single_sided_ids: list
    pending_found_ids: list = dataclass_field(default_factory=list)
    pending_not_found_ids: list = dataclass_field(default_factory=list)
    watch_not_found_ids: set = dataclass_field(default_factory=set)
    collected: list = dataclass_field(default_factory=list)
    discovered_ids: list = dataclass_field(default_factory=list)
    addition_ids: list = dataclass_field(default_factory=list)
    unparsed_ids: list = dataclass_field(default_factory=list)
    unparsed_samples: list = dataclass_field(default_factory=list)
    unidentified_feed_samples: list = dataclass_field(default_factory=list)
    html_parse_warning_ids: list = dataclass_field(default_factory=list)
    boundary_reached: bool = False
    seen_streak: int = 0
    seen_ids_skipped: list = dataclass_field(default_factory=list)
    additions: int = 0
    offers_excluded: int = 0
    malformed: int = 0
    api_listings_added: int = 0
    candidate_count: int = 0
    parsed_candidate_count: int = 0
    malformed_candidate_count: int = 0
    unidentified_feed_records: int = 0
    pages_scanned: int = 1
    backlog_pages_scanned: int = 0
    backlog_page_limit_reached: bool = False


def _new_incremental_feed_state(
    initial_feed: InitialBloxSwapsFeed,
    api_only: bool,
    seen_ad_ids,
    pending_ad_ids,
    watch_ad_ids,
) -> IncrementalFeedState:
    """Create isolated state for incremental feed processing."""
    watch_ids = {str(value) for value in (watch_ad_ids or [])}
    return IncrementalFeedState(
        seen={str(value) for value in seen_ad_ids},
        pending_remaining={str(value) for value in (pending_ad_ids or [])},
        watch_ids=watch_ids,
        watch_remaining=set(watch_ids),
        by_ad_id={
            str(listing.get("source_ad_id")): listing
            for listing in initial_feed.first_listings
            if listing.get("source_ad_id")
        },
        html_addition_ids=set(str(value) for value in initial_feed.stats.get("addition_ids", [])),
        last_success_ts=_last_successful_scan_timestamp(),
        api_posts_scanned=len(initial_feed.raw_first_items) if api_only else 0,
        item_map=initial_feed.initial_item_map,
        single_sided_count=initial_feed.single_sided_count,
        single_sided_ids=initial_feed.single_sided_ids,
    )


def _skip_incremental_post(
    state: IncrementalFeedState,
    post_id: str,
    is_pinned: bool,
    recently_refreshed: bool,
) -> bool:
    """Apply seen-boundary rules and report whether a post should be skipped."""
    if (
        state.boundary_reached
        and post_id not in state.pending_remaining
        and post_id not in state.watch_ids
        and not recently_refreshed
    ):
        return True
    if (
        post_id in state.seen
        and post_id not in state.pending_remaining
        and post_id not in state.watch_ids
        and not recently_refreshed
    ):
        state.seen_ids_skipped.append(post_id)
        if is_pinned:
            state.seen_streak = 0
            return True
        state.seen_streak += 1
        if state.seen_streak >= 25 and state.last_success_ts is not None:
            state.boundary_reached = True
        return True
    state.seen_streak = 0
    return False


def _consume_incremental_post(
    state: IncrementalFeedState, raw, compare_html: bool
) -> None:
    """Classify and parse one incremental feed record."""
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        state.malformed += 1
        state.candidate_count += 1
        state.malformed_candidate_count += 1
        state.unidentified_feed_records += 1
        state.unidentified_feed_samples.append(
            "object-without-stable-id" if isinstance(raw, dict)
            else f"invalid-record-type:{type(raw).__name__}"
        )
        return

    post_id = str(raw["id"])
    refreshed_ts = _parse_feed_timestamp(
        raw.get("lastRefreshedAt") or raw.get("createdAt") or raw.get("created_at")
    )
    recently_refreshed = (
        refreshed_ts is not None and state.last_success_ts is not None
        and refreshed_ts >= state.last_success_ts - 90
    )
    is_pinned = bool(raw.get("isPinned") or raw.get("pinned"))
    state.watch_remaining.discard(post_id)

    if _skip_incremental_post(state, post_id, is_pinned, recently_refreshed):
        return
    if post_id in state.pending_remaining:
        state.pending_remaining.discard(post_id)
        state.pending_found_ids.append(post_id)
    if post_id in state.discovered_ids:
        return
    state.discovered_ids.append(post_id)
    state.candidate_count += 1
    wildcard_kind = requested_wildcard_kind(raw.get("requestedWildcards") or [])
    if post_id in state.html_addition_ids or wildcard_kind == "ajout":
        state.additions += 1
        state.addition_ids.append(post_id)
        return
    if _api_post_is_single_sided(raw):
        state.single_sided_count += 1
        state.single_sided_ids.append(post_id)
        return
    if wildcard_kind == "offres":
        state.offers_excluded += 1
        return

    if state.item_map is None:
        needs_catalogue = any(
            isinstance(row, dict)
            and row.get("itemDetailsId")
            and not (row.get("displayName") or row.get("name"))
            for row in (raw.get("tradePostItems") or [])
        )
        state.item_map = fetch_bloxswaps_items_map() if needs_catalogue else {}
    state.item_map = _refresh_item_catalogue_for_post(raw, state.item_map)
    listing = _api_trade_post_to_listing(raw, state.item_map)
    if listing and compare_html:
        html_listing = state.by_ad_id.get(post_id)
        if html_listing:
            listing = _merge_html_quantity_diagnostics(listing, html_listing)
        else:
            listing["html_card_parse_warning"] = True
            state.html_parse_warning_ids.append(post_id)
    if listing:
        state.collected.append(listing)
        state.parsed_candidate_count += 1
        if not compare_html:
            state.api_listings_added += 1
    else:
        state.malformed += 1
        state.malformed_candidate_count += 1
        state.unparsed_ids.append(post_id)
        state.unparsed_samples.append(json.dumps(raw, ensure_ascii=False)[:240])


def _consume_incremental_posts(
    state: IncrementalFeedState, raw_posts, compare_html: bool = False
) -> None:
    for raw in raw_posts:
        _consume_incremental_post(state, raw, compare_html)


def _resolve_incremental_backlog(state: IncrementalFeedState, raw_first_items):
    """Resolve pending and watched IDs directly before scanning historical cursors."""
    first_page_ids = {
        str(post.get("id")) for post in raw_first_items
        if isinstance(post, dict) and post.get("id")
    }
    pending_to_fetch = state.pending_remaining - first_page_ids
    direct_pending_posts, pending_not_found_ids = fetch_pending_posts_by_id(pending_to_fetch)
    state.pending_not_found_ids = pending_not_found_ids
    state.pending_remaining.difference_update(pending_not_found_ids)
    state.watch_remaining.difference_update(pending_not_found_ids)
    watch_to_fetch = state.watch_remaining - first_page_ids - pending_to_fetch
    direct_watch_posts, watch_not_found_ids = fetch_pending_posts_by_id(watch_to_fetch)
    state.watch_not_found_ids = set(watch_not_found_ids) | (
        set(pending_not_found_ids) & state.watch_ids
    )
    state.watch_remaining.difference_update(state.watch_not_found_ids)
    if direct_pending_posts or pending_not_found_ids:
        print(
            f"[pending] Direct lookup resolved {len(direct_pending_posts)} active post(s) "
            f"and {len(pending_not_found_ids)} unavailable post(s)."
        )
    if direct_watch_posts or state.watch_not_found_ids:
        print(
            f"[watch] Direct lookup resolved {len(direct_watch_posts)} active watched post(s) "
            f"and {len(state.watch_not_found_ids)} unavailable post(s)."
        )
    return direct_pending_posts, direct_watch_posts


def _scan_incremental_cursor_pages(
    state: IncrementalFeedState, initial_page: dict
):
    """Scan cursor pages until the new-feed boundary or bounded backlog limit."""
    cursor = (initial_page.get("meta") or {}).get("cursor")
    seen_cursors = {str(cursor)} if cursor else set()
    if cursor:
        while cursor and state.pages_scanned < MAX_FEED_PAGES:
            if state.boundary_reached:
                # Direct ID lookups resolve watched posts; cursor lookahead is
                # only a fallback for unresolved pending IDs.
                if not state.pending_remaining:
                    break
                if state.backlog_pages_scanned >= MAX_BACKLOG_RECHECK_PAGES:
                    state.backlog_page_limit_reached = True
                    break
            payload = bloxswaps_api_get_json(
                "/trade-posts", {"cursor": cursor, "pageSize": BLOXSWAPS_PAGE_SIZE}
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise RuntimeError("[bloxswaps] Cursor API returned an invalid page structure.")
            page_items = payload["items"]
            state.api_posts_scanned += len(page_items)
            boundary_before_page = state.boundary_reached
            state.pages_scanned += 1
            if boundary_before_page:
                state.backlog_pages_scanned += 1
            _consume_incremental_posts(state, page_items, compare_html=False)
            if state.boundary_reached and not state.pending_remaining:
                break
            next_cursor = (payload.get("meta") or {}).get("cursor")
            if not next_cursor:
                cursor = None
                break
            if str(next_cursor) in seen_cursors:
                raise RuntimeError(
                    "[bloxswaps] Cursor repeated; refusing an incomplete or infinite pagination loop."
                )
            seen_cursors.add(str(next_cursor))
            cursor = next_cursor
    return cursor


def _finalize_incremental_feed(state: IncrementalFeedState, stats: dict, api_only: bool, cursor):
    """Validate candidate accounting and assemble incremental feed diagnostics."""
    from tools.feed_accounting import validate_candidate_accounting

    validate_candidate_accounting(
        candidate_count=state.candidate_count,
        additions=state.additions,
        single_sided=state.single_sided_count,
        offers_excluded=state.offers_excluded,
        malformed=state.malformed_candidate_count,
        parsed=state.parsed_candidate_count,
        context="incremental feed",
    )
    pagination_complete = not cursor or state.boundary_reached
    watch_search_complete = not state.watch_remaining
    unique = {listing["id"]: listing for listing in state.collected}
    result = list(unique.values())
    html_malformed_count = int(stats.get("malformed_count", 0))
    stats.update({
        "candidate_count": state.candidate_count,
        "parsed_candidate_count": state.parsed_candidate_count,
        "malformed_candidate_count": state.malformed_candidate_count,
        "unidentified_feed_record_count": state.unidentified_feed_records,
        "unidentified_feed_samples": state.unidentified_feed_samples[:10],
        "addition_count": state.additions,
        "offers_excluded_count": state.offers_excluded,
        "malformed_count": state.malformed,
        "html_malformed_count": html_malformed_count,
        "html_parse_warning_count": len(state.html_parse_warning_ids),
        "html_parse_warning_ids": state.html_parse_warning_ids,
        "owner_link_count": sum(1 for item in result if item.get("owner_url")),
        "pagination_pages": state.pages_scanned,
        "pagination_api_posts": state.api_posts_scanned,
        "pagination_complete": pagination_complete,
        "pagination_source": "BloxSwaps API only" if api_only else "RSC + cursor API",
        "seen_boundary_reached": state.boundary_reached,
        "seen_streak": state.seen_streak,
        "seen_ids_skipped": state.seen_ids_skipped,
        "watch_remaining_ids": sorted(state.watch_remaining),
        "watch_not_found_ids": sorted(state.watch_not_found_ids),
        "pending_found_ids": state.pending_found_ids,
        "pending_not_found_ids": state.pending_not_found_ids,
        "pending_remaining_ids": sorted(state.pending_remaining),
        "pending_complete": not state.pending_remaining,
        "watch_search_complete": watch_search_complete,
        "backlog_pages_scanned": state.backlog_pages_scanned,
        "backlog_page_limit_reached": state.backlog_page_limit_reached,
        "api_listings_added": state.api_listings_added,
        "addition_ids": state.addition_ids,
        "single_sided_count": state.single_sided_count,
        "single_sided_ids": state.single_sided_ids,
        "unparsed_ad_ids": state.unparsed_ids,
        "malformed_samples": (stats.get("malformed_samples", []) + state.unparsed_samples)[:5],
    })
    if not pagination_complete:
        print(f"[maintenance] Pagination stopped at MAX_FEED_PAGES={MAX_FEED_PAGES} with a cursor remaining.")
    return result, stats, state.discovered_ids


def fetch_bloxswaps_all(seen_ad_ids=None, pending_ad_ids=None, watch_ad_ids=None):
    """Fetch recent RSC/API posts and recheck unresolved IDs beyond seen posts."""
    if seen_ad_ids is None and pending_ad_ids:
        seen_ad_ids = set()
    api_only = os.environ.get("BLOXSWAPS_API_ONLY", "false").strip().casefold() in {"1", "true", "yes", "on"}
    initial_feed = _load_initial_bloxswaps_feed(api_only)
    initial_page = initial_feed.initial_page
    raw_first_items = initial_feed.raw_first_items
    stats = initial_feed.stats

    if seen_ad_ids is None:
        return _run_cold_start_feed(initial_feed, api_only)

    state = _new_incremental_feed_state(
        initial_feed,
        api_only,
        seen_ad_ids,
        pending_ad_ids,
        watch_ad_ids,
    )
    direct_pending_posts, direct_watch_posts = _resolve_incremental_backlog(
        state, raw_first_items
    )
    _consume_incremental_posts(state, raw_first_items, compare_html=not api_only)
    if direct_pending_posts:
        _consume_incremental_posts(state, direct_pending_posts, compare_html=False)
    if direct_watch_posts:
        _consume_incremental_posts(state, direct_watch_posts, compare_html=False)
    cursor = _scan_incremental_cursor_pages(state, initial_page)
    return _finalize_incremental_feed(state, stats, api_only, cursor)


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
        allowed_categories = {rarity}
        if chroma is True:
            allowed_categories.add("chroma")
        candidates = [
            row for row in candidates
            if str(row[2].get("category") or "").strip().casefold() in allowed_categories
        ]
    if item_type:
        candidates = [
            row for row in candidates
            if str(row[2].get("type") or "").strip().casefold() == item_type
        ]
    if chroma is not None:
        def is_chroma_variant(row):
            name = normalize_name(row[2].get("name") or "")
            source_slug = normalize_slug(row[2].get("slug") or "")
            return name.startswith("chroma ") or source_slug.startswith("chroma-")
        chroma_matches = [row for row in candidates if is_chroma_variant(row) is chroma]
        if chroma is True or any(is_chroma_variant(row) for row in candidates):
            candidates = chroma_matches
    if year is not None:
        year_pattern = re.compile(rf"(?<!\d){year}(?!\d)")
        year_matches = [
            row for row in candidates
            if year_pattern.search(str(row[2].get("name") or ""))
            or year_pattern.search(str(row[2].get("slug") or ""))
        ]
        # Year is only a disambiguator when the source explicitly carries it.
        if year_matches:
            candidates = year_matches
    return candidates



def _record_for_variant(variant):
    selected = dict(variant)
    selected["variants"] = [dict(variant)]
    return selected


def resolve_value_record(name, values, aliases=None, item=None):
    """Resolve exact slug/name first, then unique metadata-compatible variants."""
    if isinstance(item, dict) and item.get("unmatched_item_id"):
        return None, "unmatched", []
    name_key = normalize_name(name)
    all_variants = list(_iter_value_variants(values))
    metadata_item = dict(item) if isinstance(item, dict) else {}
    metadata_item["name"] = name
    catalog_slug, catalog_rarity, catalog_type, _, _, catalog_chroma = _catalog_value_metadata(metadata_item)
    identity_conflicts = []

    if catalog_slug:
        slug_candidates = [
            row for row in all_variants
            if normalize_slug(row[2].get("slug")) == catalog_slug
        ]
        filtered = _filter_variants_by_catalog(slug_candidates, metadata_item)
        if len(filtered) == 1:
            return _record_for_variant(filtered[0][2]), "slug", []
        if len(filtered) > 1:
            return None, "ambiguous", [
                str(row[2].get("name") or row[0]) for row in filtered[:8]
            ]
        if slug_candidates:
            identity_conflicts.extend(
                str(row[2].get("name") or row[0]) for row in slug_candidates[:8]
            )

    exact_candidates = [
        row for row in all_variants
        if normalize_name(row[2].get("name") or row[0]) == name_key
    ]
    filtered_exact = _filter_variants_by_catalog(exact_candidates, metadata_item)
    if len(filtered_exact) == 1:
        source = "metadata" if len(exact_candidates) > 1 else "exact"
        return _record_for_variant(filtered_exact[0][2]), source, []
    if len(filtered_exact) > 1:
        return None, "ambiguous", [
            str(row[2].get("name") or row[0])
            + (f" [{row[2].get('category')}/{row[2].get('type')}]" if row[2].get("category") or row[2].get("type") else "")
            for row in filtered_exact[:8]
        ]
    if exact_candidates and not filtered_exact:
        identity_conflicts.extend(
            str(row[2].get("name") or row[0]) for row in exact_candidates[:8]
        )

    prefix_names = {name_key + " "}
    if catalog_chroma is True:
        prefix_names.add("chroma " + name_key)
    prefix_candidates = [
        row for row in all_variants
        if any(
            normalize_name(row[2].get("name") or row[0]).startswith(prefix)
            for prefix in prefix_names
        )
    ]
    filtered_prefix = _filter_variants_by_catalog(prefix_candidates, metadata_item)
    if len(filtered_prefix) == 1:
        return _record_for_variant(filtered_prefix[0][2]), "metadata", []
    if len(filtered_prefix) > 1:
        return None, "ambiguous", [
            str(row[2].get("name") or row[0])
            + (f" [{row[2].get('category')}/{row[2].get('type')}]" if row[2].get("category") or row[2].get("type") else "")
            for row in filtered_prefix[:8]
        ]

    # Some BloxSwaps display names omit a source prefix (for example, "Laser"
    # for the RBLXValue "Vintage Laser"). Only accept a suffix candidate when
    # rarity, type, chroma, and year metadata reduce it to one exact variant.
    suffix_candidates = [
        row for row in all_variants
        if name_key and normalize_name(row[2].get("name") or row[0]).endswith(" " + name_key)
    ]
    filtered_suffix = _filter_variants_by_catalog(suffix_candidates, metadata_item)
    if len(filtered_suffix) == 1:
        return _record_for_variant(filtered_suffix[0][2]), "metadata", []
    if len(filtered_suffix) > 1:
        return None, "ambiguous", [
            str(row[2].get("name") or row[0])
            + (f" [{row[2].get('category')}/{row[2].get('type')}]" if row[2].get("category") or row[2].get("type") else "")
            for row in filtered_suffix[:8]
        ]

    aliases = aliases or {}
    canonical = aliases.get(name_key)
    if canonical:
        canonical_key = normalize_name(canonical)
        alias_candidates = [
            row for row in all_variants
            if normalize_name(row[2].get("name") or row[0]) == canonical_key
        ]
        filtered_alias = _filter_variants_by_catalog(alias_candidates, metadata_item)
        if len(filtered_alias) == 1:
            return _record_for_variant(filtered_alias[0][2]), "alias", []
        if len(filtered_alias) > 1:
            return None, "ambiguous", [
                str(row[2].get("name") or row[0]) for row in filtered_alias[:8]
            ]
    if identity_conflicts:
        return None, "ambiguous", sorted(set(identity_conflicts))[:8]
    return None, "unmatched", []



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
    "decreasing", "receding", "unsafe", "untradeable", "unstable", "volatile", "fluctuating",
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
                    # RBLXValue's range metadata is not a Supreme price source.
                    # Keep the trade-price bounds pinned to the Supreme scalar value.
                    variant["value_min"] = numeric_value
                    variant["value_max"] = numeric_value
                    variant["value_status"] = value_status
            published_rblxvalue = _positive_numeric_value(detail.get("value"))
            if published_rblxvalue is not None:
                variant["rblxvalue_value"] = published_rblxvalue
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
        cache_document["schema_version"] = 5
        cache_document["value_source_policy"] = "value_source_b_supreme_only_scalar_v1"
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
    """Poll the authenticated RBLXValue metadata endpoint hourly.

    The v2 API requires X-Api-Key on every data endpoint, including /meta.
    Attempts are persisted before I/O and transient failures are throttled so
    the 30-second scanner loop cannot repeatedly spend API budget.
    """
    if not RBLXVALUE_META_POLL_ENABLED:
        return None
    if not RBLXVALUE_API_KEY:
        raise RuntimeError("[rblxvalue-meta] API key is required for the authenticated /meta endpoint.")
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
    try:
        last_attempt = float(state.get("last_attempt_at_epoch", 0))
    except (TypeError, ValueError):
        last_attempt = 0
    # A forced post-pagination check may bypass a recent successful poll,
    # but never a failed/incomplete attempt. Otherwise a 30-second scan loop
    # could defeat the retry cooldown by repeatedly forcing the same endpoint.
    failed_attempt_is_recent = (
        last_attempt > checked_at and last_attempt > 0
        and 0 <= now - last_attempt < RBLXVALUE_META_RETRY_SECONDS
    )
    if failed_attempt_is_recent:
        print("[rblxvalue-meta] Retry cooldown active; preserving last verified metadata")
        return None

    # Persist the attempt before network I/O to prevent rapid repeated requests.
    attempt_state = dict(state)
    attempt_state["schema_version"] = 1
    attempt_state["last_attempt_at_epoch"] = now
    atomic_write_text(
        RBLXVALUE_META_FILE,
        json.dumps(attempt_state, ensure_ascii=False, indent=2) + "\n",
    )
    try:
        payload = api_get_json("/meta")
    except Exception as exc:
        print(
            f"[rblxvalue-meta] Authenticated metadata request failed: "
            f"{type(exc).__name__}; retry throttled for {RBLXVALUE_META_RETRY_SECONDS}s"
        )
        raise
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
        "last_attempt_at_epoch": now,
        "last_updated": payload["last_updated"],
        "items_total": item_total,
        "sets_total": int(totals.get("sets", 0) or 0),
        "update_interval_hours": payload.get("update_interval_hours"),
    }
    atomic_write_text(RBLXVALUE_META_FILE, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return updated

def _positive_numeric_value(value):
    """Return a finite positive numeric source value, or None when it is not listed."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _rblxvalue_blended_value(item):
    """Read the published RBLXValue estimate, deriving only from its documented sources for old caches."""
    if not isinstance(item, dict):
        return None
    direct = _positive_numeric_value(item.get("rblxvalue_value"))
    if direct is not None:
        return direct
    source_a = _positive_numeric_value(item.get("secondary_value"))
    source_b = _positive_numeric_value(item.get("raw_source_value"))
    if source_a is not None and source_b is not None:
        # RBLXValue's published estimate is the rounded mean when both lists contain the item.
        return float(math.floor((source_a + source_b) / 2.0 + 0.5))
    return source_a if source_a is not None else source_b


def _supreme_numeric_value(item):
    """Return scalar Supreme value from either the raw API row or its normalized cache record."""
    raw_value = item.get("value_source_b")
    if raw_value is None:
        raw_value = item.get("raw_source_value")
    # Never trust the generic "value" field here: RBLXValue uses it for its
    # blended estimate, not a source-pure Supreme Values price.
    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None, "not_numeric"
    if not math.isfinite(value) or value <= 0:
        return None, "not_listed"
    category = str(item.get("category") or "").strip().casefold()
    if category in {"common", "uncommon", "rare"} and value < 10:
        return None, "tier_item_count"
    return value, "numeric"




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
    """Read and normalize cached prices to the raw Supreme source_b scalar."""
    try:
        cached = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
        version = cached.get("schema_version")
        if version not in (3, 4, 5):
            return None
        values = cached.get("values", {})
        if not (
            isinstance(values, dict) and len(values) >= 100
            and all(isinstance(record, dict) and isinstance(record.get("variants"), list) and record["variants"] for record in values.values())
        ):
            return None

        # Raw API pages are the strongest provenance. Older normalized caches
        # also retain raw_source_value, which was populated from value_source_b.
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
        changed = version != 5 or cached.get("value_source_policy") != "value_source_b_supreme_only_scalar_v1"
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
                if raw is not None:
                    raw_source_value = raw.get("value_source_b")
                    row["raw_source_value"] = raw_source_value
                    row["stability"] = _normalize_stability(raw.get("stability"))
                    row["demand"] = _optional_bounded_int(raw.get("demand"), 0, 10)
                    row["rarity"] = _optional_bounded_int(raw.get("rarity"), 0, 10)
                    if raw.get("last_updated"):
                        row["last_updated"] = raw.get("last_updated")
                else:
                    raw_source_value = row.get("raw_source_value")

                supreme_value, value_status = _supreme_numeric_value({
                    "raw_source_value": raw_source_value,
                    "category": row.get("category"),
                })
                for field, expected in (
                    ("value", supreme_value),
                    ("value_min", supreme_value),
                    ("value_max", supreme_value),
                    ("value_status", value_status),
                ):
                    if row.get(field) != expected:
                        row[field] = expected
                        changed = True
                if row.get("raw_source_value") != raw_source_value:
                    row["raw_source_value"] = raw_source_value
                    changed = True
                variants.append(row)

            updated["variants"] = variants
            if len(variants) == 1:
                selected = variants[0]
                for field in ("value", "value_min", "value_max", "value_status", "stability"):
                    if updated.get(field) != selected.get(field):
                        updated[field] = selected.get(field)
                        changed = True
            else:
                for field in ("value", "value_min", "value_max", "secondary_value"):
                    if updated.get(field) is not None:
                        updated[field] = None
                        changed = True
            migrated[name] = updated

        cached = dict(cached)
        cached["schema_version"] = 5
        cached["value_source_policy"] = "value_source_b_supreme_only_scalar_v1"
        cached["values"] = migrated
        if changed:
            atomic_write_text(VALUES_FILE, json.dumps(cached, ensure_ascii=False, indent=2) + "\n")
            print("[supreme-cache] Normalized cached prices to Supreme value_source_b only (schema v5)")
        return cached
    except (ValueError, OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None

def _read_values_cache():
    cached = _read_values_cache_document()
    if cached is None:
        return None, None
    try:
        fetched_raw = cached.get("fetched_at")
        if isinstance(fetched_raw, bool):
            return None, None
        fetched_at = float(fetched_raw)
        current = float(time.time())
        if (
            not math.isfinite(fetched_at) or not math.isfinite(current)
            or fetched_at <= 0 or current <= 0 or fetched_at > current
        ):
            return None, None
        age = int(current - fetched_at)
        return cached["values"], age
    except (TypeError, ValueError, KeyError, OverflowError):
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


def _merge_supreme_page_items(all_values, items):
    """Merge one RBLXValue page using source-pure Supreme values and exact identities."""
    numeric_count = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        value, value_status = _supreme_numeric_value(item)
        # Only value_source_b is used as the Supreme price. Do not
        # widen it with a provider range whose source is not verified.
        value_min = value_max = value
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
            "rblxvalue_value": _positive_numeric_value(item.get("value")),
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
    return numeric_count


def load_supreme_values():
    # Never spend API budget or perform health checks for a credential already blocked.
    if RBLXVALUE_API_KEY and rblxvalue_guard.is_key_blocked(key=RBLXVALUE_API_KEY):
        raise RuntimeError(
            "[supreme] API key circuit breaker active; refusing stale-cache calculations "
            "[rblxvalue-key-blocked] no request sent"
        )
    # Schema v5 enforces source_b-only scalar prices and preserves item identity/quality metadata.
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
            source_meta = _fetch_rblxvalue_meta()
        except Exception as exc:
            raise RuntimeError(
                f"[supreme] Could not verify source version before pagination ({type(exc).__name__}); "
                "refusing an unverified catalogue refresh"
            ) from exc
        if source_meta is None:
            raise RuntimeError(
                "[supreme] Source metadata is unavailable or in retry cooldown; "
                "refusing to paginate without a verified source version."
            )
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

            numeric_count += _merge_supreme_page_items(all_values, items)
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
        "schema_version": 5,
        "value_source_policy": "value_source_b_supreme_only_scalar_v1",
        "fetched_at": time.time(),
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
    """Return the Supreme scalar only; provider ranges are not Supreme prices."""
    if not isinstance(variant, dict):
        return None, None
    try:
        base = float(variant.get("value"))
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(base) or base <= 0:
        return None, None
    return base, base


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
        if not 1 <= int(quantity) <= MAX_ITEM_QUANTITY:
            missing.append(f"{item['name']} (quantity outside supported range)")
            continue
        entry["quantity"] += int(quantity)
    for entry in grouped.values():
        item_total = entry["unit_value"] * entry["quantity"]
        total += item_total
        suffix = f" x{entry['quantity']}"
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
        if qmin < 1 or qmin > MAX_ITEM_QUANTITY or (qmax is not None and (qmax < qmin or qmax > MAX_ITEM_QUANTITY)):
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



def _wait_for_telegram_rate_limit():
    """Respect the configured minimum spacing between messages to one chat."""
    global _LAST_TELEGRAM_SEND_AT
    now = time.monotonic()
    wait = ALERT_DELAY_SECONDS - (now - _LAST_TELEGRAM_SEND_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_TELEGRAM_SEND_AT = time.monotonic()


RBLXVALUE_ATTRIBUTION = "Data from rblxvalue.com"
ROBASE_ATTRIBUTION = "Prices: chromas.gg Price (7d) via ROBASE"

def _telegram_escape(value):
    return html_lib.escape(str(value), quote=False)


def _telegram_link(url, label):
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        parsed = None
    if (
        parsed and parsed.scheme == "https"
        and parsed.hostname in {"bloxswaps.com", "www.bloxswaps.com", "discord.com", "discordapp.com"}
        and not parsed.username and not parsed.password
    ):
        return f'<a href="{html_lib.escape(raw, quote=True)}">{_telegram_escape(label)}</a>'
    return _telegram_escape(label)


def _format_telegram_item_line(line):
    raw = str(line or "").strip()
    core_parts = raw.lstrip("•").strip().split(" | ")
    core = core_parts[0]
    match = re.match(
        r"^(?P<name>.*?)\s+x(?P<quantity>\d+)\s+—\s+(?P<unit>.*?)\s+each\s+=\s+(?P<total>.+)$",
        core,
    )
    if not match:
        return f"• {_telegram_escape(raw.lstrip('•').strip())}"
    name = match.group("name").strip()
    identity = ""
    identity_match = re.match(r"^(.*?)\s+\[([^\]]+)\]$", name)
    if identity_match:
        name = identity_match.group(1).strip()
        identity = " · ".join(part.strip().title() for part in identity_match.group(2).split("/"))
    quantity = match.group("quantity")
    unit_value = match.group("unit").strip()
    total_value = match.group("total").strip()
    heading = f"• <b>{_telegram_escape(name)}</b> <code>×{quantity}</code>"
    if identity:
        heading += f" <i>{_telegram_escape(identity)}</i>"
    value_line = f"  Value: {_telegram_escape(unit_value)} each → <b>{_telegram_escape(total_value)}</b>"
    details = []
    for detail in core_parts[1:]:
        label = detail.strip()
        # Supreme and Stability have one authoritative home: the boxed
        # REFERENCE INFO ONLY section below. Older evaluation paths may still
        # append them to item lines; suppress those copies to avoid duplication.
        if label.startswith(("Rarity:", "Supreme:", "Stability:")):
            continue
        label = label.replace("Demand:", "Demand")
        if label:
            details.append(label)
    if details:
        return heading + "\n" + value_line + "\n  <i>" + _telegram_escape(" · ".join(details)) + "</i>"
    return heading + "\n" + value_line


def _format_telegram_items(items):
    return [_format_telegram_item_line(item) for item in _compact_telegram_items(items)]


def send_telegram(message):
    """Compatibility wrapper for conservative Telegram message delivery."""
    return _send_telegram(
        message,
        robase_attribution=ROBASE_ATTRIBUTION,
        rblxvalue_attribution=RBLXVALUE_ATTRIBUTION,
        max_chars=TELEGRAM_MAX_CHARS,
        bot_token=BOT_TOKEN,
        chat_id=CHAT_ID,
        opener=TELEGRAM_OPENER,
        wait_for_rate_limit=_wait_for_telegram_rate_limit,
        send_part=send_telegram,
    )
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
    ordered = sorted(set(str(value) for value in ad_ids if value))[-MAX_SEEN_ADS:]
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
    if len(normalized) > MAX_UNCERTAIN_DELIVERIES:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery quarantine exceeds its safety limit; "
            "manual reconciliation is required before scanning can continue."
        )
    if any(not ad_id or not math.isfinite(timestamp) or timestamp <= 0 for ad_id, timestamp in normalized.items()):
        raise RuntimeError("[state] Telegram uncertain-delivery state contains an invalid ID or timestamp.")
    atomic_write_text(UNCERTAIN_FILE, json.dumps({
        "version": 2,
        "ids": sorted(normalized),
        "created_at": normalized,
    }, indent=2) + "\n")


def load_uncertain_context():
    """Load source links for quarantined Telegram sends; corruption fails closed."""
    if not UNCERTAIN_CONTEXT_FILE.exists():
        return {}
    try:
        data = json.loads(UNCERTAIN_CONTEXT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery context is unreadable; refusing unsafe recovery."
        ) from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("details"), dict):
        raise RuntimeError("[state] Telegram uncertain-delivery context has an invalid structure.")
    result = {}
    removed_non_public_context = False
    allowed_hosts = {"bloxswaps.com", "www.bloxswaps.com"}
    for trade_id, entry in data["details"].items():
        if not isinstance(trade_id, str) or not trade_id or not isinstance(entry, dict):
            raise RuntimeError("[state] Telegram uncertain-delivery context entry is invalid.")
        source = entry.get("source", "")
        if not isinstance(source, str):
            raise RuntimeError("[state] Telegram uncertain-delivery context source is invalid.")
        # The state branch is public. Never retain private Discord references there.
        if source.casefold() != "bloxswaps":
            removed_non_public_context = True
            continue
        for field in ("source_ad_id", "listing_url", "owner_url", "delivery_path", "attempted_at_utc"):
            value = entry.get(field, "")
            if not isinstance(value, str):
                raise RuntimeError("[state] Telegram uncertain-delivery context field is invalid.")
        for field in ("listing_url", "owner_url"):
            value = entry.get(field, "")
            if value:
                try:
                    parsed = urlsplit(value)
                    port = parsed.port
                except ValueError as exc:
                    raise RuntimeError(
                        "[state] Telegram uncertain-delivery context contains an unsafe link."
                    ) from exc
                if (
                    parsed.scheme != "https"
                    or (parsed.hostname or "").casefold() not in allowed_hosts
                    or port not in (None, 443)
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise RuntimeError("[state] Telegram uncertain-delivery context contains an unsafe link.")
        if entry.get("delivery_path") not in {"individual", "digest", "recovered"}:
            raise RuntimeError("[state] Telegram uncertain-delivery context delivery path is invalid.")
        result[trade_id] = dict(entry)
    if removed_non_public_context:
        save_uncertain_context(result)
    return result


def save_uncertain_context(context):
    """Persist a bounded map from uncertain send IDs to their public source listings."""
    normalized = dict(context)
    if len(normalized) > 500:
        normalized = dict(sorted(
            normalized.items(),
            key=lambda row: str(row[1].get("attempted_at_utc", "")),
        )[-500:])
    atomic_write_text(UNCERTAIN_CONTEXT_FILE, json.dumps({
        "version": 1, "details": normalized,
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def record_uncertain_context(listing, trade_id, delivery_path):
    """Store only public BloxSwaps references before a non-idempotent Telegram send."""
    context = load_uncertain_context()
    allowed_hosts = {"bloxswaps.com", "www.bloxswaps.com"}

    def safe_link(value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError:
            return ""
        return (
            raw[:2048]
            if parsed.scheme == "https"
            and (parsed.hostname or "").casefold() in allowed_hosts
            and port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            else ""
        )

    source = (
        str(listing.get("source") or "")
        .strip()
        .casefold()
        .replace("-", "_")
        .replace(" ", "_")
    )
    listing_url = safe_link(listing.get("url"))
    owner_url = safe_link(listing.get("owner_url"))
    known_bloxswaps_labels = {
        "bloxswaps", "bloxswaps.com", "bloxswaps_api", "bloxswaps_api_v2",
    }
    if source not in known_bloxswaps_labels and not (listing_url or owner_url):
        # Unknown source labels are accepted only when a trusted BloxSwaps
        # HTTPS reference independently proves the source.
        context.pop(str(trade_id), None)
        save_uncertain_context(context)
        return
    source = "bloxswaps"

    context[str(trade_id)] = {
        "source_ad_id": str(listing.get("source_ad_id") or ""),
        "listing_url": listing_url,
        "owner_url": owner_url,
        "source": source[:32],
        "delivery_path": str(delivery_path),
        "attempted_at_utc": str(
            listing.get("alert_attempted_at_utc")
            or datetime.now(timezone.utc).isoformat()
        ),
    }
    save_uncertain_context(context)


def clear_uncertain_context(trade_id):
    context = load_uncertain_context()
    if str(trade_id) in context:
        context.pop(str(trade_id), None)
        save_uncertain_context(context)


def load_uncertain_state():
    """Keep uncertain sends quarantined until an operator explicitly reconciles them."""
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
    if len(set(raw_ids)) > MAX_UNCERTAIN_DELIVERIES:
        raise RuntimeError(
            "[state] Telegram uncertain-delivery quarantine exceeds its safety limit; "
            "manual reconciliation is required before scanning can continue."
        )
    active = {}
    for ad_id in raw_ids:
        if not ad_id:
            raise RuntimeError("[state] Telegram uncertain-delivery ID is empty.")
        try:
            timestamp = float(created_at.get(ad_id, now))
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError("[state] Telegram uncertain-delivery timestamp is invalid.")
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise RuntimeError("[state] Telegram uncertain-delivery timestamp is invalid.")
        active[ad_id] = timestamp
    return active, []


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
    except TelegramDeliveryUncertain as exc:
        # Keep the pre-committed cooldown: retrying a non-idempotent send could duplicate it.
        print(f"[maintenance] Alert delivery uncertain for {key}; cooldown retained ({type(exc).__name__})")
        return False
    except Exception as exc:
        # A definite rejection (for example Telegram HTTP 400/403) cannot have
        # delivered the alert. Roll back only our own reservation so the next
        # maintenance cycle can retry instead of silently losing the alert.
        if state.get(key) == now:
            state.pop(key, None)
            atomic_write_text(MAINTENANCE_FILE, json.dumps(state, indent=2) + "\n")
        print(f"[maintenance] Alert definitely failed for {key}; cooldown released ({type(exc).__name__})")
        return False


def unmatched_names_from_missing(missing, cash_mode=False):
    """Keep true identity gaps for review; suppress expected out-of-ROBASE items in cash mode."""
    names = []
    ignored_cash_reasons = (
        "no exact robase/rblxvalue weapon mapping",
        "no exact robase/bloxswaps identity mapping",
        "(not a robase weapon)",
        "(bloxswaps catalogue slug missing)",
    )
    for value in missing or []:
        text = str(value or "")
        lowered = text.casefold()
        if not text or lowered.startswith("one side has"):
            continue
        # These are evaluator rejection reasons, not item identities. Keeping
        # them in the unmatched-name registry pollutes mapping coverage metrics.
        if any(marker in lowered for marker in (
            "wildcard", "not eligible", "incomplete trade sides",
            "cash mode accepts", "invalid trade sides",
            "value-weighted demand is low", "value weighted demand is low",
            "no numeric supreme value",
        )):
            continue
        if lowered in {"quantity unknown", "quantity uncertain"} or "stability" in lowered:
            continue
        if cash_mode and any(reason in lowered for reason in ignored_cash_reasons):
            continue
        names.append(text.split(" (", 1)[0])
    return names


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
            state[key] = {"name": name, "first_seen_at": now, "last_seen_at": now, "occurrences": 1}
            new_names.append(name)
        else:
            state[key]["last_seen_at"] = now
            state[key]["occurrences"] = max(1, int(state[key].get("occurrences", 1) or 1)) + 1
    atomic_write_text(UNMATCHED_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    if new_names:
        unique = sorted(set(new_names), key=str.casefold)
        alert_key = "unmatched:" + hashlib.sha256(
            "|".join(normalize_name(name) for name in unique).encode()
        ).hexdigest()[:16]
        send_maintenance_alert(
            alert_key,
            "New item names not found in the active exact item-identity mapping (no values guessed): "
            + ", ".join(unique)
            + "\nThey are stored in the item-identity review registry, not queued as offers. "
            "A verified mapping will be applied to new listings and still-active watched listings.",
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


def _format_telegram_market_info(listing, market_info):
    """Render optional Supreme/RBLXValue reference fields; never used for eligibility."""
    if not isinstance(market_info, dict) or not market_info:
        return []
    if str(listing.get("source", "bloxswaps")).strip().casefold() not in {"bloxswaps", "bloxswaps.com"}:
        return []
    rows = []
    seen = set()
    for side, field in (("GIVE", "give_items"), ("GET", "receive_items")):
        for item in listing.get(field, []) or []:
            if not isinstance(item, dict):
                continue
            # The archive loader and this formatter use the same punctuation-free
            # slug key; matching remains exact after normalization.
            slug = re.sub(
                r"[^a-z0-9]+", "",
                str(item.get("catalog_slug") or item.get("bloxswaps_slug") or item.get("slug") or "").casefold(),
            )
            entry = market_info.get(slug)
            if not isinstance(entry, dict) or (side, slug) in seen:
                continue
            seen.add((side, slug))
            name = str(entry.get("name") or item.get("name") or "Unknown item")
            value = entry.get("supreme_value")
            stability = str(entry.get("stability") or "").strip() or "N/A"
            value_label = format_number(value) if value is not None else "N/A"
            rows.extend([
                f"│ {'🟢' if side == 'GIVE' else '🔵'} <b>{_telegram_escape(side)}</b> · {_telegram_escape(name)}",
                f"│   Supreme <code>{_telegram_escape(value_label)}</code>  ·  Stability <code>{_telegram_escape(stability)}</code>",
            ])
    if not rows:
        return []
    return [
        "┌─ 📊 <b>REFERENCE INFO ONLY</b>",
        *rows,
        "└ <i>RBLXValue · not used in cash filters or alert decisions</i>",
    ]


def format_trade_message(listing, give_total, give_lines, receive_total, receive_lines, difference, market_info=None):
    """Readable, clickable Telegram trade card with escaped item data."""
    result = "WIN" if difference > 0 else ("FAIR" if difference == 0 else "LOSS")
    status_icon = "🟢" if difference > 0 else ("🟡" if difference == 0 else "🔴")
    source = str(listing.get("source", "bloxswaps")).upper()
    owner_url = listing.get("owner_url") or listing.get("url")
    trade_url = listing.get("url")
    if source != "DISCORD" and owner_url:
        owner_url = _clean_owner_link(owner_url)
    owner_label = "Trader profile" if owner_url else "Profile unavailable"
    lines = [
        "💎 MM2 TRADE <b>ALERT</b>",
        f"{status_icon} <b>{_telegram_escape(source)} · {result}</b>",
        "",
        f"👤 {_telegram_link(owner_url, owner_label)}",
    ]
    if trade_url and trade_url != owner_url:
        trade_label = "Open trade" if source != "DISCORD" else "Open Discord message"
        lines.append(f"🔗 {_telegram_link(trade_url, trade_label)}")
    lines.extend(["", "━━━━━━━━━━━━━━━━"])
    if listing.get("valuation_mode") == "cash":
        lines.extend([
            "📤 <b>YOU GIVE</b> <i>· ROBASE price</i>",
            *_format_telegram_items(give_lines),
            f"Total: <b>{chr(36)}{format_number(give_total)}</b>",
            "",
            "📥 <b>YOU GET</b> <i>· ROBASE price</i>",
            *_format_telegram_items(receive_lines),
            f"Total: <b>{chr(36)}{format_number(receive_total)}</b>",
            "",
            *_format_telegram_market_info(listing, market_info),
            f"💵 <b>EST. CASH PROFIT (after marketplace fee; before extra costs): +{chr(36)}{format_number(difference)}</b>",
            "<i>Chroma.gg Price (7d) is seller proceeds after marketplace fees; additional costs/slippage are not included.</i>",
        ])
    else:
        lines.extend([
            "📤 <b>YOU GIVE</b>",
            *_format_telegram_items(give_lines),
            f"Total: <b>{format_number(give_total)}</b>",
            "",
            "📥 <b>YOU GET</b>",
            *_format_telegram_items(receive_lines),
            f"Total: <b>{format_number(receive_total)}</b>",
            "",
            "━━━━━━━━━━━━━━━━",
            f"💰 <b>PROFIT (game value): {'+' if difference > 0 else ''}{format_number(difference)}</b>",
            f"🏁 <b>RESULT: {result}</b>",
        ])
    return TelegramHTML("\n".join(lines))


def finalize_confirmed_telegram_delivery(
    trade_id, uncertain_ids, mark_alerted, update_watch, save_uncertain, clear_context
):
    """Persist confirmed delivery before releasing its duplicate-send quarantine."""
    mark_alerted(trade_id)
    update_watch()
    uncertain_ids.discard(trade_id)
    save_uncertain(uncertain_ids)
    clear_context(trade_id)


def deliver_digest_items_individually(items, prepare_one, send_one):
    """Send each digest offer independently so partial delivery cannot hide unsent offers."""
    outcomes = []
    for index, item in enumerate(items):
        prepare_one(item)
        try:
            send_one(item)
        except TelegramDeliveryUncertain as exc:
            outcomes.append(("uncertain", item, exc))
            continue
        except Exception as exc:
            outcomes.append(("failed", item, exc))
            outcomes.extend(("not_attempted", pending, exc) for pending in items[index + 1:])
            break
        outcomes.append(("sent", item, None))
    return outcomes


def format_opportunity_digest(opportunities, market_info=None):
    """One same-cycle digest for all qualified offers beyond the individual-alert budget."""
    cash_only = bool(opportunities) and all(
        op.get("listing", {}).get("valuation_mode") == "cash" for op in opportunities
    )
    if cash_only:
        lines = [
            "💎 MM2 ROBASE <b>OPPORTUNITIES</b>",
            f"<b>{len(opportunities)} verified offers · profit ${min(CASH_MIN_PROFIT_USD, CASH_MIN_PROFIT_USD_SMALL):.2f}+</b>",
            "Item prices are copied from ROBASE. Every offer is open now.",
            "",
        ]
    else:
        lines = [
            "💎 MM2 TRADE <b>DIGEST</b>",
            f"<b>{len(opportunities)} additional verified opportunities</b>",
            "Ranked by conservative profit. Every offer is available now; none is deferred.",
            "",
        ]
    for index, op in enumerate(opportunities, start=1):
        listing = op.get("listing", {})
        source = str(listing.get("source", "bloxswaps")).upper()
        trade_url = str(listing.get("url") or "")
        if trade_url.startswith("/"):
            trade_url = "https://bloxswaps.com" + trade_url
        link = _telegram_link(trade_url, "Open trade" if source != "DISCORD" else "Open Discord message")
        give_summary = "\n".join(_format_telegram_items(op.get("give_lines", []))) or "—"
        receive_summary = "\n".join(_format_telegram_items(op.get("receive_lines", []))) or "—"
        if listing.get("valuation_mode") == "cash":
            low = float(op.get("cash_profit_worst", op.get("difference", 0)) or 0)
            profit_label = f"EST. +{chr(36)}{low:.2f}"
            quality_label = "ROBASE 7d gross estimate"
            totals = f"{chr(36)}{float(op.get('give_total', 0) or 0):.2f} → {chr(36)}{float(op.get('receive_total', 0) or 0):.2f}"
        else:
            conservative = float(op.get("profit_worst", op.get("difference", 0)) or 0)
            profit_label = f"{'+' if conservative > 0 else ''}{format_number(conservative)}"
            quality_label = f"MARGIN: {float(op.get('margin', 0) or 0):.2f}%"
            totals = f"{format_number(op.get('give_total', 0))} → {format_number(op.get('receive_total', 0))}"
        warning = "⚠️ " if op.get("manual_review") else ""
        lines.append(
            f"{index}. {warning}<b>{_telegram_escape(profit_label)}</b> · "
            f"{_telegram_escape(quality_label)} · {link}"
        )
        lines.append(f"   GIVE:\n{give_summary}")
        lines.append(f"   GET:\n{receive_summary}")
        lines.append(f"   TOTAL: {_telegram_escape(totals)}")
        reference_info = _format_telegram_market_info(listing, market_info)
        if reference_info:
            lines.append("")
            lines.extend(reference_info)
        lines.append("")
    return TelegramHTML("\n".join(lines).rstrip())


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
    if demand_score < MIN_RECEIVE_DEMAND_SCORE:
        issues.append(f"received-side value-weighted Demand is low ({demand_score:.2f}/10; minimum {MIN_RECEIVE_DEMAND_SCORE:.2f})")
    if stability_score < MIN_RECEIVE_STABILITY_SCORE:
        issues.append(f"received-side value-weighted Stability is weak ({stability_score:.2f}/9; minimum {MIN_RECEIVE_STABILITY_SCORE:.2f})")
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
    """Compatibility wrapper for the pure conservative qualification gate."""
    return _qualifies_impl(
        result,
        MIN_DIFF if min_diff is None else min_diff,
        MIN_PROFIT_PERCENT if min_percent is None else min_percent,
    )


ROBASE_CATEGORY_TO_RBLX = {"classic": "vintage"}


def _robase_base_identity(row):
    name = str(row.get("name") or "").strip()
    category = str(row.get("robase_category") or "").strip().casefold()
    chroma = category == "chroma" or name.casefold().startswith("chroma ")
    if name.casefold().startswith("chroma "):
        name = name[7:].strip()
    return normalize_name(name), chroma


def _robase_image_hints(row):
    image_slug = normalize_slug(row.get("og_image_slug") or row.get("bloxswaps_slug") or "")
    item_type = None
    year = None
    if image_slug:
        if re.search(r"(?:^|[-_])(?:g|gun)(?:[-_]|$)", image_slug):
            item_type = "gun"
        elif re.search(r"(?:^|[-_])(?:k|knife)(?:[-_]|$)", image_slug) or "knife" in image_slug:
            item_type = "knife"
        year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", image_slug)
        if year_match:
            year = int(year_match.group(1))
    return image_slug, item_type, year


def _match_robase_row_to_bloxswaps(row, item_map):
    """Match a ROBASE page to one BloxSwaps weapon using its image ID or unique metadata."""
    base_name, chroma = _robase_base_identity(row)
    category = str(row.get("robase_category") or "").strip().casefold()
    expected_rarity = ROBASE_CATEGORY_TO_RBLX.get(category, category)
    image_slug, image_type, image_year = _robase_image_hints(row)
    direct_slugs = {slug for slug in (image_slug, normalize_slug(row.get("robase_slug") or "")) if slug}
    for details in (item_map or {}).values():
        if not isinstance(details, dict) or normalize_slug(details.get("slug") or "") not in direct_slugs:
            continue
        direct_name = normalize_name(details.get("displayName") or "")
        direct_rarity = str(details.get("rarity") or "").strip().casefold()
        direct_chroma = details.get("chroma")
        if (
            direct_name == base_name
            and ((chroma and direct_chroma is True) or (not chroma and direct_chroma is not True and direct_rarity == expected_rarity))
        ):
            source = "robase_og_image_slug" if normalize_slug(details.get("slug") or "") == image_slug else "robase_item_slug"
            return details, source
    candidates = []
    for details in (item_map or {}).values():
        if not isinstance(details, dict):
            continue
        slug = normalize_slug(details.get("slug") or "")
        display_name = normalize_name(details.get("displayName") or "")
        item_type = str(details.get("type") or "").strip().casefold()
        rarity = str(details.get("rarity") or "").strip().casefold()
        item_chroma = details.get("chroma")
        if not slug or display_name != base_name or item_type not in {"knife", "gun"}:
            continue
        if chroma:
            if item_chroma is not True:
                continue
        else:
            if item_chroma is True or rarity != expected_rarity:
                continue
        if image_type and item_type != image_type:
            continue
        if image_year is not None and details.get("year") is not None:
            try:
                if int(details.get("year")) != image_year:
                    continue
            except (TypeError, ValueError):
                continue
        candidates.append(details)

    if image_slug:
        exact_image = [
            details for details in candidates
            if normalize_slug(details.get("slug") or "") == image_slug
        ]
        if len(exact_image) == 1:
            return exact_image[0], "robase_og_image_slug"
    robase_slug = normalize_slug(row.get("robase_slug") or "")
    direct_slug = [
        details for details in candidates
        if normalize_slug(details.get("slug") or "") == robase_slug
    ]
    if len(direct_slug) == 1:
        return direct_slug[0], "robase_item_slug"
    if len(candidates) == 1:
        return candidates[0], "unique_name_rarity_type"
    return None, "ambiguous" if candidates else "missing"


def _resolve_robase_cash_record(catalog_slug, cash_catalog):
    """Resolve cash by the audited BloxSwaps slug -> ROBASE source-slug crosswalk."""
    slug = normalize_slug(catalog_slug)
    mapping_index = (cash_catalog or {}).get("bloxswaps_exact_map")
    if not isinstance(mapping_index, dict) and os.environ.get("MM2_TESTING", "").strip().casefold() == "true":
        mapping_index = (cash_catalog or {}).get("rblx_exact_map", {})
    mapping = (mapping_index or {}).get(slug)
    if not isinstance(mapping, dict):
        return None, "missing"
    row = (cash_catalog or {}).get("items_by_slug", {}).get(mapping.get("robase_slug"))
    return (row, mapping.get("robase_identity_source", "exact")) if row else (None, "missing")


def audit_robase_rblxvalue_mapping(values, aliases, cash_catalog, item_map):
    """Build exact ROBASE -> BloxSwaps -> RBLXValue crosswalk for weapon variants."""
    robase_rows = list((cash_catalog or {}).get("items_by_slug", {}).values())
    weapon_rows = []
    non_weapons = []
    missing_robase = []
    ambiguous_robase = []
    seen_blox_slugs = {}
    for row in robase_rows:
        matched, identity_source = _match_robase_row_to_bloxswaps(row, item_map)
        if not matched:
            if identity_source == "ambiguous":
                ambiguous_robase.append(row.get("name") or row.get("robase_slug"))
            else:
                missing_robase.append(row.get("name") or row.get("robase_slug"))
            continue
        item_type = str(matched.get("type") or "").strip().casefold()
        if item_type not in {"knife", "gun"}:
            non_weapons.append(row.get("name") or row.get("robase_slug"))
            continue
        catalog_slug = normalize_slug(matched.get("slug") or "")
        if catalog_slug in seen_blox_slugs:
            ambiguous_robase.extend([
                seen_blox_slugs[catalog_slug].get("name") or seen_blox_slugs[catalog_slug].get("robase_slug"),
                row.get("name") or row.get("robase_slug"),
            ])
            continue
        seen_blox_slugs[catalog_slug] = row
        weapon_rows.append((row, matched, identity_source))

    exact_map = {}
    rblx_ambiguous = []
    rblx_missing = []
    for row, details, identity_source in weapon_rows:
        name = str(details.get("displayName") or "").strip()
        catalog_slug = normalize_slug(details.get("slug") or "")
        item = {
            "name": name,
            "catalog_slug": catalog_slug,
            "catalog_rarity": details.get("rarity"),
            "catalog_type": details.get("type"),
            "catalog_chroma": details.get("chroma"),
            "catalog_year": details.get("year"),
        }
        record, source, candidates = resolve_value_record(name, values, aliases, item=item)
        variants = record.get("variants") or [record] if record else []
        if not record or source not in {"slug", "metadata", "exact"} or len(variants) != 1:
            label = f"{row.get('name')} [{catalog_slug}]"
            if source == "ambiguous" or candidates:
                rblx_ambiguous.append(label)
            else:
                rblx_missing.append(label)
            continue
        selected = variants[0]
        selected_slug = normalize_slug(selected.get("slug") or "")
        selected_type = str(selected.get("type") or "").strip().casefold()
        selected_category = str(selected.get("category") or "").strip().casefold()
        expected_type = str(details.get("type") or "").strip().casefold()
        expected_rarity = str(details.get("rarity") or "").strip().casefold()
        chroma = details.get("chroma") is True
        expected_category = "chroma" if chroma else ROBASE_CATEGORY_TO_RBLX.get(expected_rarity, expected_rarity)
        if (
            not selected_slug or selected_type != expected_type
            or selected_category != expected_category
        ):
            rblx_missing.append(f"{row.get('name')} [{catalog_slug}: metadata mismatch]")
            continue
        exact_map[catalog_slug] = {
            "bloxswaps_slug": catalog_slug,
            "robase_slug": normalize_slug(row.get("robase_slug") or ""),
            "robase_name": row.get("name"),
            "robase_category": row.get("robase_category"),
            "robase_identity_source": identity_source,
            "rblx_slug": selected_slug,
            "rblx_name": selected.get("name"),
            "rblx_match_source": source,
            "value": _supreme_numeric_value(selected)[0],
            "value_status": _supreme_numeric_value(selected)[1],
            "rblxvalue_value": _rblxvalue_blended_value(selected),
            "mm2values_value": _positive_numeric_value(selected.get("secondary_value")),
            "supreme_value": _positive_numeric_value(selected.get("raw_source_value")),
            "stability": _normalize_stability(selected.get("stability")),
            "demand": _optional_bounded_int(selected.get("demand"), 0, 10),
            "category": selected.get("category"),
            "type": selected.get("type"),
        }

    # A matching immutable ROBASE/RBLXValue slug is itself a safe direct
    # crosswalk when the BloxSwaps metadata cache is temporarily unavailable.
    # This fallback is deliberately exact-slug only; it never uses fuzzy names.
    value_variants = list(_iter_value_variants(values))
    for row in robase_rows:
        robase_slug = normalize_slug(row.get("robase_slug") or "")
        if not robase_slug or robase_slug in exact_map:
            continue
        direct_values = [
            variant for variant in value_variants
            if normalize_slug(variant[2].get("slug") or "") == robase_slug
        ]
        if len(direct_values) != 1:
            continue
        selected = direct_values[0][2]
        selected_type = str(selected.get("type") or "").strip().casefold()
        selected_category = str(selected.get("category") or "").strip().casefold()
        row_category = str(row.get("robase_category") or "").strip().casefold()
        expected_category = "chroma" if row_category == "chroma" else ROBASE_CATEGORY_TO_RBLX.get(row_category, row_category)
        selected_name = normalize_name(selected.get("name") or "")
        selected_slug = normalize_slug(selected.get("slug") or "")
        selected_chroma = selected_name.startswith("chroma ") or selected_slug.startswith("chroma-")
        if (
            selected_type not in {"knife", "gun"}
            or selected_category != expected_category
            or selected_chroma != (row_category == "chroma")
        ):
            continue
        exact_map[robase_slug] = {
            "bloxswaps_slug": robase_slug,
            "robase_slug": robase_slug,
            "robase_name": row.get("name"),
            "robase_category": row.get("robase_category"),
            "robase_identity_source": "robase_rblx_exact_slug",
            "rblx_slug": selected_slug,
            "rblx_name": selected.get("name"),
            "rblx_match_source": "slug",
            "value": _supreme_numeric_value(selected)[0],
            "value_status": _supreme_numeric_value(selected)[1],
            "stability": _normalize_stability(selected.get("stability")),
            "demand": _optional_bounded_int(selected.get("demand"), 0, 10),
            "category": selected.get("category"),
            "type": selected.get("type"),
        }
        missing_robase = [
            name for name in missing_robase
            if normalize_name(name) != normalize_name(row.get("name") or "")
        ]

    expected_weapons = len(weapon_rows) + len(ambiguous_robase) + len(missing_robase)
    numeric = sum(row["value"] is not None for row in exact_map.values())
    missing_value = sum(row["value"] is None for row in exact_map.values())
    missing_stability = sum(row["stability"] is None for row in exact_map.values())
    missing_demand = sum(row["demand"] is None for row in exact_map.values())
    cash_catalog["rblx_exact_map"] = exact_map
    print(
        f"[robase-rblxvalue] weapons={expected_weapons} exact={len(exact_map)} "
        f"numeric={numeric} value_missing={missing_value} stability_missing={missing_stability} "
        f"demand_missing={missing_demand} rblx_ambiguous={len(rblx_ambiguous)} "
        f"rblx_missing={len(rblx_missing)} robase_ambiguous={len(ambiguous_robase)} "
        f"robase_missing={len(missing_robase)} non_weapons={len(non_weapons)}"
    )
    if ambiguous_robase:
        print("[robase-rblxvalue] robase_ambiguous=" + ", ".join(sorted(set(ambiguous_robase))[:20]))
    if missing_robase:
        print("[robase-rblxvalue] robase_missing=" + ", ".join(sorted(set(missing_robase))[:20]))
    if rblx_ambiguous:
        print("[robase-rblxvalue] rblx_ambiguous=" + ", ".join(sorted(set(rblx_ambiguous))[:20]))
    if rblx_missing:
        print("[robase-rblxvalue] rblx_missing=" + ", ".join(sorted(set(rblx_missing))[:20]))
    if non_weapons:
        print("[robase-rblxvalue] non_weapons=" + ", ".join(sorted(set(non_weapons))[:20]))
    return {
        "robase_total": len(robase_rows),
        "expected": expected_weapons,
        "exact": len(exact_map),
        "numeric": numeric,
        "missing_value": missing_value,
        "missing_stability": missing_stability,
        "missing_demand": missing_demand,
        "ambiguous": sorted(set(ambiguous_robase + rblx_ambiguous)),
        "missing": sorted(set(missing_robase + rblx_missing)),
        "non_weapons": sorted(set(non_weapons)),
    }




def evaluate_cash_listing_robase_only(listing, cash_catalog):
    """Evaluate cash profit from verified ROBASE prices and BloxSwaps identity only."""
    if str(listing.get("source") or "bloxswaps").strip().casefold() not in {"bloxswaps", "bloxswaps.com"}:
        return None, ["cash mode accepts BloxSwaps offers only"]
    wildcard_kind = requested_wildcard_kind(listing.get("requested_wildcards") or [])
    if wildcard_kind in {"ajout", "offres"} or not listing.get("give_items") or not listing.get("receive_items"):
        return None, ["Ajouts/Offres wildcards or incomplete trade sides are not eligible"]
    exact_map = (cash_catalog or {}).get("bloxswaps_exact_map")
    items_by_slug = (cash_catalog or {}).get("items_by_slug", {})
    if not isinstance(exact_map, dict) or not exact_map:
        return None, ["ROBASE/BloxSwaps identity audit is unavailable"]
    prepared = {"give": [], "receive": []}
    missing = []
    for side, field in (("give", "give_items"), ("receive", "receive_items")):
        for item in listing.get(field, []):
            name = str(item.get("name") or "Unknown")
            slug = normalize_slug(item.get("catalog_slug") or "")
            kind = str(item.get("catalog_type") or "").strip().casefold()
            mapping = exact_map.get(slug)
            if not isinstance(mapping, dict):
                missing.append(f"{name} (no exact ROBASE/BloxSwaps identity mapping)")
                continue
            if kind not in {"knife", "gun"} or kind != mapping.get("bloxswaps_type"):
                missing.append(f"{name} (BloxSwaps weapon type mismatch)")
                continue
            if mapping.get("bloxswaps_tradable") is not True:
                missing.append(f"{name} (BloxSwaps does not confirm this item is tradable)")
                continue
            rarity = str(item.get("catalog_rarity") or "").strip().casefold()
            if rarity and rarity != mapping.get("bloxswaps_rarity"):
                missing.append(f"{name} (BloxSwaps rarity mismatch)")
                continue
            if bool(item.get("catalog_chroma")) != bool(mapping.get("bloxswaps_chroma")):
                missing.append(f"{name} (Chroma identity mismatch)")
                continue
            robase_slug = str(mapping.get("robase_slug") or "").strip().casefold()
            row = items_by_slug.get(robase_slug)
            if not row:
                missing.append(f"{name} (ROBASE source slug missing)")
                continue
            canonical = str(mapping.get("robase_name") or row.get("name") or "").strip()
            if not canonical:
                missing.append(f"{name} (ROBASE canonical name missing)")
                continue
            if row.get("status") != "verified" or not cash_price_is_fresh(row, max_age_days=CASH_MAX_AGE_DAYS):
                missing.append(f"{canonical} (ROBASE price not freshly verified)")
                continue
            quantity = item.get("quantity")
            qmin = item.get("quantity_min")
            qmax = item.get("quantity_max")
            quantity_source = item.get("quantity_source", "implicit_single")
            try:
                qmin = strict_quantity(qmin if qmin is not None else (quantity if quantity is not None else 1))
                if qmax is None and quantity is not None and quantity_source != "uncertain":
                    qmax = quantity
                qmax = strict_quantity(qmax) if qmax is not None else None
            except (TypeError, ValueError):
                qmin, qmax = 1, None
            if quantity_source == "uncertain" or qmax is None or qmax != qmin:
                missing.append(f"{canonical} (quantity not confirmed exactly)")
                continue
            prepared[side].append({"name": canonical, "canonical_name": canonical, "display_name": canonical,
                                   "robase_slug": robase_slug, "quantity_min": qmin, "quantity_max": qmax})
    if missing:
        return None, missing
    if not prepared["give"] or not prepared["receive"]:
        return None, ["one side has no ROBASE-priced BloxSwaps weapons"]
    give_min, give_max, give_lines, give_missing = calculate_cash_side(
        prepared["give"], cash_catalog, "give", 0, max_age_days=CASH_MAX_AGE_DAYS,
        allow_unverified_estimates=False)
    receive_min, receive_max, receive_lines, receive_missing = calculate_cash_side(
        prepared["receive"], cash_catalog, "receive", 0, max_age_days=CASH_MAX_AGE_DAYS,
        allow_unverified_estimates=False, sell_haircut_tiers=None)
    if give_missing or receive_missing:
        return None, give_missing + receive_missing
    worst, best = cash_profit_bounds((give_min, give_max), (receive_min, receive_max))
    worst = math.floor((worst + 1e-9) * 100) / 100
    best = math.floor((best + 1e-9) * 100) / 100
    margin = worst / give_max * 100.0 if give_max > 0 else 0.0
    listing["valuation_mode"] = "cash"
    return {"listing": listing, "give_total": give_max, "give_total_min": give_min, "give_total_max": give_max,
            "give_lines": give_lines, "receive_total": receive_min, "receive_total_min": receive_min,
            "receive_total_max": receive_max, "receive_lines": receive_lines, "difference": worst,
            "profit_worst": worst, "profit_best": best, "margin": margin, "manual_review": False,
            "uncertain_items": [], "cash_sell_haircut_pct": 0.0, "cash_sell_haircut_mode": "none",
            "cash_profit_worst": worst, "cash_profit_best": best,
            "cash_price_basis": "chromas.gg Price (7d)", "cash_estimate_only": False,
            "receive_demand_score": None, "receive_stability_score": None}, []


def _prepare_cash_listing_item(item, values, aliases, exact_map, items_by_slug):
    """Validate one item against exact RBLXValue identity and a fresh ROBASE price."""
    raw_name = str(item.get("name") or "Unknown")
    catalog_slug = normalize_slug(item.get("catalog_slug") or "")
    catalog_type = str(item.get("catalog_type") or "").strip().casefold()
    if not catalog_slug:
        return None, f"{raw_name} (BloxSwaps catalogue slug missing)"
    if catalog_type not in {"knife", "gun"}:
        return None, f"{raw_name} (not a ROBASE weapon)"

    mapping = exact_map.get(catalog_slug)
    if not isinstance(mapping, dict):
        return None, f"{raw_name} (no exact ROBASE/RBLXValue weapon mapping)"
    record, match_source, candidates = resolve_value_record(
        raw_name, values, aliases, item=item
    )
    variants = record.get("variants") or [record] if record else []
    if (
        not record or match_source not in {"slug", "metadata", "exact"}
        or len(variants) != 1
        or normalize_slug(variants[0].get("slug") or "") != mapping.get("rblx_slug")
    ):
        suffix = f": {', '.join(candidates)}" if candidates else ""
        return None, f"{raw_name} (RBLXValue identity mismatch{suffix})"

    selected = variants[0]
    selected_type = str(selected.get("type") or "").strip().casefold()
    selected_category = str(selected.get("category") or "").strip().casefold()
    expected_type = str(mapping.get("type") or "").strip().casefold()
    expected_category = str(mapping.get("category") or "").strip().casefold()
    if selected_type not in {"knife", "gun"} or selected_type != expected_type or selected_category != expected_category:
        return None, f"{raw_name} (RBLXValue type/rarity mismatch)"

    robase_slug = str(mapping.get("robase_slug") or "").strip().casefold()
    cash_record = items_by_slug.get(robase_slug)
    if not cash_record:
        return None, f"{raw_name} (ROBASE source slug missing)"
    canonical_name = str(mapping.get("robase_name") or cash_record.get("name") or "").strip()
    if not canonical_name:
        return None, f"{raw_name} (ROBASE canonical name missing)"
    if cash_record.get("status") != "verified" or not cash_price_is_fresh(
        cash_record, max_age_days=CASH_MAX_AGE_DAYS
    ):
        return None, f"{canonical_name} (ROBASE price not freshly verified)"

    stability = _normalize_stability(selected.get("stability"))
    if stability and stability.casefold() == "untradeable":
        return None, f"{canonical_name} (RBLXValue marks item untradeable)"

    quantity = item.get("quantity")
    qmin = item.get("quantity_min")
    qmax = item.get("quantity_max")
    quantity_source = item.get("quantity_source", "implicit_single")
    try:
        qmin = strict_quantity(
            qmin if qmin is not None else (quantity if quantity is not None else 1)
        )
        if qmax is None and quantity is not None and quantity_source != "uncertain":
            qmax = quantity
        qmax = strict_quantity(qmax) if qmax is not None else None
    except (TypeError, ValueError):
        qmin, qmax = 1, None
    if quantity_source == "uncertain" or qmax is None or qmax != qmin:
        return None, f"{canonical_name} (quantity not confirmed exactly)"

    value, value_status = _supreme_numeric_value(selected)
    category = str(selected.get("category") or "").strip()
    item_type = str(selected.get("type") or "").strip()
    identity = "/".join(part for part in (category, item_type) if part)
    display_name = f"{canonical_name} [{identity}]" if identity else canonical_name
    return {
        "name": canonical_name,
        "canonical_name": canonical_name,
        "display_name": display_name,
        "robase_slug": robase_slug,
        "quantity_min": qmin,
        "quantity_max": qmax,
        "stability": stability or "N/A",
        "rblx_value": _rblxvalue_blended_value(selected),
        "mm2values_value": _positive_numeric_value(selected.get("secondary_value")),
        "supreme_value": _positive_numeric_value(selected.get("raw_source_value")),
        "rblx_value_status": value_status,
        "rblx_demand": _optional_bounded_int(selected.get("demand"), 0, 10),
        "rblx_rarity": _optional_bounded_int(selected.get("rarity"), 0, 10),
        "rblx_category": category,
        "rblx_type": item_type,
        "rblx_slug": normalize_slug(selected.get("slug") or ""),
        "robase_identity_source": mapping.get("robase_identity_source"),
        "robase_price_display": cash_record.get("price_display"),
    }, None


def evaluate_cash_listing(listing, values, aliases, cash_catalog, estimate_mode=False):
    """Evaluate only exact BloxSwaps weapon variants present in ROBASE and RBLXValue."""
    if str(listing.get("source") or "bloxswaps").strip().casefold() not in {"bloxswaps", "bloxswaps.com"}:
        return None, ["cash mode accepts BloxSwaps offers only"]
    if listing.get("requested_wildcards"):
        return None, ["wildcard requests are not exact ROBASE-only offers"]
    if not listing.get("give_items") or not listing.get("receive_items"):
        return None, ["one side has no items"]

    exact_map = (cash_catalog or {}).get("rblx_exact_map")
    if not isinstance(exact_map, dict) or not exact_map:
        return None, ["ROBASE/RBLXValue exact identity audit is unavailable"]
    items_by_slug = (cash_catalog or {}).get("items_by_slug", {})
    prepared = {"give": [], "receive": []}
    missing = []
    for side, field in (("give", "give_items"), ("receive", "receive_items")):
        for item in listing.get(field, []):
            prepared_item, missing_reason = _prepare_cash_listing_item(
                item, values, aliases, exact_map, items_by_slug
            )
            if missing_reason:
                missing.append(missing_reason)
            else:
                prepared[side].append(prepared_item)

    if missing:
        return None, missing
    if not prepared["give"] or not prepared["receive"]:
        return None, ["one side has no ROBASE-priced RBLXValue-matched weapons"]

    # ROBASE already displays seller proceeds after marketplace fees. Apply no
    # second resale haircut or tier adjustment to either side.
    give_min, give_max, give_lines, give_missing = calculate_cash_side(
        prepared["give"], cash_catalog, "give", 0,
        max_age_days=CASH_MAX_AGE_DAYS, allow_unverified_estimates=False,
    )
    receive_min, receive_max, receive_lines, receive_missing = calculate_cash_side(
        prepared["receive"], cash_catalog, "receive", 0,
        max_age_days=CASH_MAX_AGE_DAYS, allow_unverified_estimates=False,
        sell_haircut_tiers=None,
    )
    missing = give_missing + receive_missing
    if missing:
        return None, missing

    def add_rblx_details(lines, items):
        """Keep Telegram valuation concise: Supreme is the sole displayed game value.

        RBLXValue remains the authenticated transport for Supreme data and Stability,
        but its blended estimate and MM2Values are intentionally not shown as
        separate competing valuations.
        """
        enriched = []
        for line, item in zip(lines, items):
            supreme_value = item.get("supreme_value")
            details = [
                f"Supreme: {format_number(supreme_value)}"
                if supreme_value is not None else "Supreme: N/A"
            ]
            details.append(f"Stability: {item['stability']}")
            enriched.append(line + " | " + " · ".join(details))
        return enriched

    give_lines = add_rblx_details(give_lines, prepared["give"])
    receive_lines = add_rblx_details(receive_lines, prepared["receive"])
    profit_worst, profit_best = cash_profit_bounds(
        (give_min, give_max), (receive_min, receive_max)
    )
    # Round down to cents so a borderline result never crosses the $1.00 gate by rounding up.
    profit_worst = math.floor((profit_worst + 1e-9) * 100) / 100
    profit_best = math.floor((profit_best + 1e-9) * 100) / 100
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
        "manual_review": False,
        "uncertain_items": [],
        "cash_sell_haircut_pct": 0.0,
        "cash_sell_haircut_mode": "none",
        "cash_profit_worst": profit_worst,
        "cash_profit_best": profit_best,
        "cash_price_basis": "chromas.gg Price (7d)",
        "cash_estimate_only": False,
        "receive_demand_score": None,
        "receive_stability_score": None,
    }, []



def qualifies_cash(result):
    """Cash-only alert gate; missing provenance and uncertain results fail closed."""
    if not isinstance(result, dict):
        return False
    if not cash_listing_is_fresh(result.get("listing")):
        return False
    if (
        result.get("manual_review") is not False
        or result.get("cash_estimate_only") is not False
        or result.get("cash_price_basis") != "chromas.gg Price (7d)"
    ):
        return False
    if "cash_profit_worst" not in result or "margin" not in result or "give_total_max" not in result:
        return False
    raw_profit, raw_margin, raw_give = (
        result["cash_profit_worst"], result["margin"], result["give_total_max"]
    )
    if any(isinstance(value, bool) for value in (raw_profit, raw_margin, raw_give)):
        return False
    try:
        profit, margin, give_total = float(raw_profit), float(raw_margin), float(raw_give)
    except (TypeError, ValueError, OverflowError):
        return False
    threshold = max(1.0, CASH_MIN_PROFIT_USD_SMALL if give_total < 5.0 else CASH_MIN_PROFIT_USD)
    return (
        math.isfinite(profit) and math.isfinite(margin) and math.isfinite(give_total)
        and give_total >= 0
        and profit >= threshold
        and margin >= CASH_MIN_MARGIN_PERCENT
    )


def cash_listing_is_fresh(listing, now=None, max_age_seconds=None):
    """Require a recent BloxSwaps refresh before a cash opportunity can alert."""
    if not isinstance(listing, dict):
        return False
    posted_at = _parse_feed_timestamp(listing.get("posted_at"))
    if posted_at is None:
        return False
    try:
        current = time.time() if now is None else float(now)
        maximum_age = (
            CASH_MAX_LISTING_AGE_SECONDS
            if max_age_seconds is None else float(max_age_seconds)
        )
    except (TypeError, ValueError, OverflowError):
        return False
    if (
        not math.isfinite(current) or not math.isfinite(maximum_age)
        or maximum_age < 0 or posted_at > current + 60
    ):
        return False
    age = current - posted_at
    return 0 <= age <= maximum_age


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
                item.get('quantity'), item.get('quantity_min'), item.get('quantity_max'),
                listing.get("posted_at") if cash_catalog else None
            ))
            record, match_source, _ = resolve_value_record(name, values, aliases, item=item)
            unit_value = None
            unit_min = unit_max = None
            _selected = None
            if record:
                unit_value, _selected = _select_conservative_variant(record, side)
                if _selected:
                    unit_min, unit_max = _variant_numeric_bounds(_selected)
            cash_row = None
            cash_selected = _selected
            if record and cash_selected is None:
                cash_variants = record.get("variants") or [record]
                if len(cash_variants) == 1:
                    cash_selected = cash_variants[0]
            if cash_catalog:
                catalog_slug = normalize_slug(item.get("catalog_slug") or "")
                cash_row, _cash_identity_source = _resolve_robase_cash_record(catalog_slug, cash_catalog)
                cash_mapping = (cash_catalog.get("bloxswaps_exact_map") or {}).get(catalog_slug, {})
                stability = _normalize_stability((cash_selected or {}).get("stability")) if record else None
                valuation.append((
                    side, normalize_name(name), match_source,
                    cash_row.get("price_display") if cash_row else None,
                    cash_row.get("status") if cash_row else "missing",
                    (cash_selected or {}).get("value_source_b") if cash_selected else None,
                    (cash_selected or {}).get("category") if cash_selected else None,
                    (cash_selected or {}).get("type") if cash_selected else None,
                    stability,
                    cash_mapping.get("bloxswaps_type"), cash_mapping.get("bloxswaps_rarity"),
                    cash_mapping.get("bloxswaps_chroma"), cash_mapping.get("bloxswaps_tradable"),
                    cash_mapping.get("bloxswaps_year"),
                ))
            else:
                valuation.append((side, normalize_name(name), unit_value, unit_min, unit_max, match_source))
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
        if created > 0 and now - created <= WATCH_TTL_SECONDS: active[str(ad_id)] = entry
    if len(active) > MAX_WATCHED_ADS:
        raise RuntimeError(
            f"[state] Watch-window contains {len(active)} active listings, exceeding the "
            f"safe limit of {MAX_WATCHED_ADS}; refusing to evict unresolved listings."
        )
    if len(active) != len(ads): save_watch_state(active)
    return active

def save_watch_state(ads):
    now = time.time()
    active = {str(k): v for k, v in ads.items() if isinstance(v, dict) and now - float(v.get('created_at', now)) <= WATCH_TTL_SECONDS}
    if len(active) > MAX_WATCHED_ADS:
        raise RuntimeError(
            f"[state] Watch-window would contain {len(active)} active listings, exceeding the "
            f"safe limit of {MAX_WATCHED_ADS}; refusing to evict unresolved listings."
        )
    atomic_write_text(WATCH_FILE, json.dumps({'version': 1, 'ads': active}, ensure_ascii=False, indent=2) + "\n")

def select_watch_recheck_ids(watch_state, limit):
    """Select the oldest non-Discord watch entries for fair bounded direct rechecks."""
    try:
        budget = max(0, int(limit))
    except (TypeError, ValueError, OverflowError):
        return []
    if budget == 0 or not isinstance(watch_state, dict):
        return []
    candidates = []
    for ad_id, entry in watch_state.items():
        key = str(ad_id)
        if key.startswith("discord:") or not isinstance(entry, dict):
            continue
        try:
            updated = float(entry.get("updated_at", entry.get("created_at", 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            updated = 0.0
        if not math.isfinite(updated):
            updated = 0.0
        candidates.append((updated, key))
    candidates.sort()
    return [key for _updated, key in candidates[:budget]]


_CASH_PROFIT_HISTORY_CACHE_KEY = None
_CASH_PROFIT_HISTORY_CACHE = {}


def last_cash_profit_for_listing(listing_id):
    """Read cached previous cash profits once; ignore every non-cash history field."""
    global _CASH_PROFIT_HISTORY_CACHE_KEY, _CASH_PROFIT_HISTORY_CACHE
    try:
        stat = HISTORY_FILE.stat()
    except OSError:
        return None
    cache_key = (str(HISTORY_FILE), stat.st_mtime_ns, stat.st_size)
    if cache_key != _CASH_PROFIT_HISTORY_CACHE_KEY:
        latest_by_listing = {}
        try:
            with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("profit_basis") or "").casefold() != "cash_usd":
                        continue
                    raw_profit = row.get("profit_cash_usd")
                    if raw_profit in (None, ""):
                        continue
                    try:
                        profit = float(raw_profit)
                    except (TypeError, ValueError):
                        continue
                    key = str(row.get("listing_id") or "")
                    if key and math.isfinite(profit) and profit >= 0:
                        latest_by_listing[key] = profit
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            print(f"[watch-migration] Previous cash-profit history unavailable: {type(exc).__name__}")
            return None
        _CASH_PROFIT_HISTORY_CACHE = latest_by_listing
        _CASH_PROFIT_HISTORY_CACHE_KEY = cache_key
    return _CASH_PROFIT_HISTORY_CACHE.get(str(listing_id))


def watch_variant_changed(previous_watch, content_hash, values_hash, cash_filter=False):
    """Ignore one legacy value-source fingerprint change during cash-mode migration."""
    if not previous_watch:
        return False
    legacy_cash_watch = cash_filter and previous_watch.get("cash_source_version") != 2
    return (
        previous_watch.get("content_hash") != content_hash
        or (not legacy_cash_watch and previous_watch.get("values_hash") != values_hash)
    )


def update_watch_entry(watch_state, listing, values, aliases=None, cash_catalog=None):
    ad_id = str(listing.get('source_ad_id') or '')
    if not ad_id: return False
    content_hash, values_hash = listing_watch_fingerprints(listing, values, aliases, cash_catalog)
    now = time.time()
    previous = watch_state.get(ad_id, {})
    changed = bool(previous) and (previous.get('content_hash') != content_hash or previous.get('values_hash') != values_hash)
    created = now if changed or not previous else float(previous.get('created_at', now))
    watch_state[ad_id] = {'content_hash': content_hash, 'values_hash': values_hash, 'cash_source_version': 2 if CASH_FILTER else 1, 'created_at': created, 'updated_at': now}
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


def alert_cash_price_runway(cash_catalog, verified_rows, minimum_rows=CASH_MIN_VERIFIED_ROWS):
    """Send one daily counts-only notice for near-expiry or low verified-row runway."""
    expiring = int(cash_catalog.get("expiring_within_3_days", 0) or 0)
    if expiring <= 0 and verified_rows >= minimum_rows:
        return False
    message = (
        "Cash-price runway warning: "
        f"verified rows={verified_rows}; expiring within 3 days={expiring}; "
        f"minimum required={minimum_rows}. Re-verify catalogue rows."
    )
    return send_maintenance_alert(
        "cash-price-runway", message, cooldown_seconds=86400,
    )


def alert_expired_cash_prices(expired_rows, max_age_days=CASH_MAX_AGE_DAYS):
    """Send a bounded daily notice; expired rows never become eligible by alerting."""
    if not expired_rows:
        return False
    oldest = sorted(expired_rows, key=lambda row: row.get("verified_at_utc") or "")[:5]
    names = ", ".join(str(row.get("name") or "Unknown item") for row in oldest)
    return send_maintenance_alert(
        "cash-price-expired",
        f"{len(expired_rows)} verified cash prices have expired (>{max_age_days} days). "
        f"Oldest entries: {names}. Re-verify them before they can qualify.",
        cooldown_seconds=86400,
    )


def accumulate_cash_blockers(missing, counts, groups):
    """Count each missing/unverified/expired item once per skipped listing."""
    blocked = set()
    for value in missing or []:
        text = str(value)
        lowered = text.casefold()
        if not any(reason in lowered for reason in (
            "cash price missing", "cash price unverified", "cash price expired"
        )):
            continue
        name = text.split(" (cash price", 1)[0]
        key = normalize_cash_name(name)
        if key:
            blocked.add(key)
    for key in blocked:
        counts[key] = counts.get(key, 0) + 1
    if blocked:
        group = "|".join(sorted(blocked))
        groups[group] = groups.get(group, 0) + 1
    return blocked


@dataclass(slots=True)
class ScanMarketData:
    values: dict
    cash_catalog: dict | None
    cash_prices_changed: bool
    telegram_market_info: dict


@dataclass(slots=True)
class ScanHistoryData:
    values_snapshot_changed: bool
    history_store: object | None
    public_history_store: object | None


def _load_cash_market_catalog(daily_shadow_mode: bool) -> tuple[dict | None, bool]:
    """Load and report ROBASE cash-price readiness without affecting selection policy."""
    cash_catalog = None
    cash_prices_changed = False
    if CASH_FILTER:
        cash_catalog = load_robase_cash_price_catalog()
        all_cash_rows = list(cash_catalog.get("items_by_slug", {}).values())
        verified_cash_rows = sum(
            1 for row in all_cash_rows
            if row["status"] == "verified" and cash_price_is_fresh(row, CASH_MAX_AGE_DAYS)
        )
        expired_cash_rows = [
            row for row in all_cash_rows
            if row["status"] == "verified" and not cash_price_is_fresh(row, CASH_MAX_AGE_DAYS)
        ]
        expired_cash_rows.sort(key=lambda row: row.get("verified_at_utc") or "")
        unverified_cash_rows = sum(1 for row in all_cash_rows if row["status"] == "unverified")
        alert_cash_price_runway(cash_catalog, verified_cash_rows)
        previous_cash_hash = cash_catalog_snapshot_hash()
        cash_prices_changed = previous_cash_hash != cash_catalog["sha256"]
        print("[mode] mode=cash")
        if daily_shadow_mode:
            print(
                "[cash] Loaded %s/%s baseline ROBASE catalogue rows for identity only; "
                "rolling prices are replaced before shadow evaluation."
                % (
                    cash_catalog["row_count"],
                    cash_catalog.get("expected_count", cash_catalog["row_count"]),
                )
            )
        else:
            print(
                "[cash] Loaded %s/%s exact ROBASE prices; verified=%s; expired=%s; unverified=%s"
                % (
                    cash_catalog["row_count"],
                    cash_catalog.get("expected_count", cash_catalog["row_count"]),
                    verified_cash_rows,
                    len(expired_cash_rows),
                    unverified_cash_rows,
                )
            )
        if unverified_cash_rows > 0:
            send_maintenance_alert(
                "cash-price-catalog-unverified",
                f"Cash-first alerts only use verified USD prices. Current catalogue: {verified_cash_rows} verified, "
                f"{unverified_cash_rows} unverified. Offers with missing/unverified prices are held, not sent.",
            )
        if verified_cash_rows == 0:
            print(
                "[cash] No verified cash prices yet; cash-profit alerts are safely disabled "
                "until prices are manually verified."
            )
    else:
        print("[mode] mode=value")
    return cash_catalog, cash_prices_changed


def _load_scan_market_data(modes: ScanModes) -> ScanMarketData:
    """Load valuation inputs and optional reference data for one scan."""
    if modes.value_profit_shadow_mode:
        archive_path = os.environ.get(
            "RBLXVALUE_DAILY_ARCHIVE_FILE",
            "rblxvalue-archive/data/rblxvalue_latest.json",
        )
        values, archive_summary = load_values_from_snapshot(archive_path)
        print(
            "[value-profit-shadow] "
            + json.dumps(archive_summary, ensure_ascii=False, sort_keys=True)
        )
        print(
            f"[value-profit-shadow] thresholds: minimum Supreme value profit={MIN_DIFF:g}; "
            f"minimum margin={MIN_PROFIT_PERCENT:g}%"
        )
    elif CASH_FILTER and not modes.testing_mode:
        print("[mode] ROBASE cash-only: Supreme Values and RBLXValue are not loaded or queried")
        values = {}
    elif modes.testing_mode:
        values = load_supreme_values()
    else:
        raise RuntimeError("Value mode is disabled until third-party data rights are verified.")

    cash_catalog, cash_prices_changed = _load_cash_market_catalog(
        modes.daily_shadow_mode
    )

    telegram_market_info = {}
    if CASH_FILTER and not modes.testing_mode and not modes.value_profit_shadow_mode:
        info_path = os.environ.get("RBLXVALUE_INFO_FILE", "data/rblxvalue_latest.json")
        try:
            telegram_market_info, info_summary = load_telegram_market_info(info_path)
            print(
                "[telegram-market-info] "
                + json.dumps(info_summary, ensure_ascii=False, sort_keys=True)
                + "; informational_only=true"
            )
        except Exception as info_error:
            # This sidecar is display-only. A missing/stale/corrupt archive must
            # never block the cash scanner or delay its Telegram alerts.
            print(
                "[telegram-market-info] unavailable; alerts continue unchanged; "
                f"reason={type(info_error).__name__}"
            )

    return ScanMarketData(
        values=values,
        cash_catalog=cash_catalog,
        cash_prices_changed=cash_prices_changed,
        telegram_market_info=telegram_market_info,
    )


def _open_scan_history_data(modes: ScanModes, values: dict) -> ScanHistoryData:
    """Open optional history stores after valuation inputs have been validated."""
    values_snapshot_changed = False
    history_store = None
    if (CASH_FILTER and not modes.testing_mode) or modes.value_profit_shadow_mode:
        # Shadow modes do not open or mutate the legacy market-history database.
        history_store = None
    else:
        try:
            from market_history import MarketHistory
            history_store = MarketHistory()
            if history_store.record_values_snapshot(values):
                values_snapshot_changed = True
        except Exception as history_error:
            print(f"[market-history] Snapshot storage skipped safely: {type(history_error).__name__}")

    public_history_store = None
    if (
        CASH_FILTER and not modes.testing_mode
        and os.environ.get("DRY_RUN", "").strip().casefold() not in {"true", "1", "yes", "on"}
    ):
        try:
            from tools.public_dashboard_history import PublicDashboardHistory
            public_history_store = PublicDashboardHistory()
        except Exception as history_error:
            raise RuntimeError(
                "Public dashboard history could not be initialized; refusing to send alerts "
                "without a state artifact that can pass privacy validation."
            ) from history_error

    return ScanHistoryData(
        values_snapshot_changed=values_snapshot_changed,
        history_store=history_store,
        public_history_store=public_history_store,
    )


@dataclass(slots=True)
class ScanPersistentState:
    aliases: dict
    seen_ads: set | None
    pending_ids: dict
    pending_schedule: dict
    watch_state: dict
    due_pending_ids: dict
    watch_recheck_ids: list


@dataclass(slots=True)
class ScanFeedResult:
    listings: list
    stats: dict
    discovered_ids: list
    mapping_audit: dict | None


@dataclass(slots=True)
class ScanRecheckSchedule:
    seen_ads: set | None
    all_due_pending_ids: dict
    due_pending_ids: dict
    watch_state: dict
    last_success_ts: float | None
    run_backlog_rechecks: bool
    watch_recheck_ids: list
    scan_mode: str


def _retire_legacy_pending_ids(seen_ads, pending_ids, pending_schedule):
    """Close obsolete offer-review queue entries while retaining technical retries."""
    retryable_reasons = {"unparsed_feed", "telegram_failure"}
    retired_pending = []
    for pending_id in list(pending_ids):
        reason = (pending_schedule.get(pending_id) or {}).get("reason")
        if reason not in retryable_reasons:
            retired_pending.append(pending_id)
            pending_ids.pop(pending_id, None)
            pending_schedule.pop(pending_id, None)
    if retired_pending:
        if seen_ads is None:
            seen_ads = set()
        seen_ads.update(retired_pending)
        save_seen_ads(seen_ads)
        save_pending_ids(pending_ids)
        save_pending_recheck_schedule(pending_schedule)
        print(
            f"[state] Closed {len(retired_pending)} legacy review/deferred offer IDs; "
            "no offer-level manual-review backlog remains."
        )
    if seen_ads is not None:
        new_pending_seen = set(pending_ids) - seen_ads
        if new_pending_seen:
            seed_pending_ids_as_seen(seen_ads, new_pending_seen, pending_schedule)
            save_seen_ads(seen_ads)
            print(
                f"[state] Seeded {len(new_pending_seen)} pending IDs into the seen set; "
                "they remain queued for bounded rechecks."
            )
    return seen_ads


def _prepare_scan_recheck_schedule(
    seen_ads,
    pending_ids,
    pending_schedule,
    include_backlog_rechecks,
    values_snapshot_changed,
    cash_prices_changed,
) -> ScanRecheckSchedule:
    """Choose bounded pending/watch work and the scan mode for this invocation."""
    all_due_pending_ids = due_pending_ids_for_scan(
        pending_ids, pending_schedule, limit=0
    )
    run_backlog_rechecks = (
        include_backlog_rechecks or values_snapshot_changed or cash_prices_changed
    )
    due_pending_ids = (
        due_pending_ids_for_scan(
            pending_ids,
            pending_schedule,
            force=values_snapshot_changed or cash_prices_changed,
        )
        if run_backlog_rechecks else {}
    )
    watch_state = load_watch_state()
    last_success_ts = _last_successful_scan_timestamp()
    if (
        seen_ads is not None
        and not pending_ids
        and scan_checkpoint_requires_cold_start(last_success_ts)
    ):
        age_seconds = max(0.0, time.time() - last_success_ts) if last_success_ts else None
        age_label = f"{age_seconds / 3600:.1f}h" if age_seconds is not None else "unknown age"
        print(
            f"[state] Last successful scan is stale ({age_label}); "
            "switching to a bounded 60-minute cold-start window instead of replaying old listings."
        )
        seen_ads = None
    if seen_ads is None and include_backlog_rechecks and (pending_ids or watch_state):
        seen_ads = set()

    print(
        f"[state] Pending listings: {len(pending_ids)} total; "
        f"{len(all_due_pending_ids)} due; "
        f"{len(due_pending_ids)} selected for this scan (cap={MAX_PENDING_RECHECK_PER_SCAN})"
    )
    print("[stage] Fetching BloxSwaps listings and cursor pages")
    watch_recheck_budget = max(0, MAX_PENDING_RECHECK_PER_SCAN - len(due_pending_ids))
    watch_recheck_ids = (
        select_watch_recheck_ids(watch_state, watch_recheck_budget)
        if run_backlog_rechecks else []
    )
    scan_mode = (
        "values-refresh-backlog" if values_snapshot_changed
        else "cash-price-refresh-backlog" if cash_prices_changed
        else "backlog-recheck" if include_backlog_rechecks
        else "fast-new-feed"
    )
    print(
        f"[scan-mode] {scan_mode}; direct_recheck_budget={MAX_PENDING_RECHECK_PER_SCAN}; "
        f"watch_batch={len(watch_recheck_ids)}; pending_cursor_lookahead={MAX_BACKLOG_RECHECK_PAGES}"
    )
    return ScanRecheckSchedule(
        seen_ads=seen_ads,
        all_due_pending_ids=all_due_pending_ids,
        due_pending_ids=due_pending_ids,
        watch_state=watch_state,
        last_success_ts=last_success_ts,
        run_backlog_rechecks=run_backlog_rechecks,
        watch_recheck_ids=watch_recheck_ids,
        scan_mode=scan_mode,
    )


def _load_scan_persistent_state(
    include_backlog_rechecks: bool,
    values_snapshot_changed: bool,
    cash_prices_changed: bool,
) -> ScanPersistentState:
    """Load and clean durable scanner state, then prepare bounded rechecks."""
    aliases = load_aliases()
    print(f"[aliases] Loaded {len(aliases)} reviewed item aliases")
    print("[stage] Loading seen-offer and pending-recheck state")
    seen_ads = load_seen_ads()
    pending_ids = load_pending_ids()
    pending_schedule = load_pending_recheck_schedule()
    seen_ads = _retire_legacy_pending_ids(seen_ads, pending_ids, pending_schedule)
    schedule = _prepare_scan_recheck_schedule(
        seen_ads,
        pending_ids,
        pending_schedule,
        include_backlog_rechecks,
        values_snapshot_changed,
        cash_prices_changed,
    )
    return ScanPersistentState(
        aliases=aliases,
        seen_ads=schedule.seen_ads,
        pending_ids=pending_ids,
        pending_schedule=pending_schedule,
        watch_state=schedule.watch_state,
        due_pending_ids=schedule.due_pending_ids,
        watch_recheck_ids=schedule.watch_recheck_ids,
    )


def _fetch_scan_feed_and_audit(
    state: ScanPersistentState, cash_catalog: dict | None
) -> ScanFeedResult:
    """Fetch the BloxSwaps feed, rotate watch state, and audit exact identities."""
    listings, stats, discovered_ids = fetch_bloxswaps_all(
        state.seen_ads, state.due_pending_ids.keys(), state.watch_recheck_ids
    )
    watch_not_found_ids = set(stats.get("watch_not_found_ids", []))
    if state.watch_recheck_ids:
        for ad_id in state.watch_recheck_ids:
            if ad_id in watch_not_found_ids:
                state.watch_state.pop(ad_id, None)
            elif ad_id in state.watch_state:
                # Rotate every attempted ID, including unchanged or temporarily
                # unresolved records, so a bad record cannot starve later entries.
                state.watch_state[ad_id]["updated_at"] = time.time()
        save_watch_state(state.watch_state)

    mapping_audit = None
    if CASH_FILTER:
        try:
            mapping_audit = build_robase_bloxswaps_map(
                cash_catalog, fetch_bloxswaps_items_map()
            )
            cash_catalog["bloxswaps_exact_map"] = mapping_audit.get(
                "bloxswaps_exact_map", {}
            )
            mapping_gaps = mapping_audit.get("missing", []) + mapping_audit.get("ambiguous", [])
            if mapping_gaps:
                send_maintenance_alert(
                    "robase-bloxswaps-mapping-gaps",
                    "ROBASE/BloxSwaps weapon identity mapping has unresolved items: "
                    + "; ".join(str(value) for value in mapping_gaps[:20])
                    + f"\nUnresolved: {len(mapping_gaps)}. Affected listings are excluded; no identities are guessed.",
                    cooldown_seconds=6 * 60 * 60,
                )
        except Exception as audit_error:
            print(
                f"[robase-bloxswaps] coverage audit unavailable: {type(audit_error).__name__}"
            )
            send_maintenance_alert(
                "robase-bloxswaps-audit-unavailable",
                "ROBASE/BloxSwaps identity audit could not complete: "
                f"{type(audit_error).__name__}. Cash opportunities remain fail-closed.",
                cooldown_seconds=6 * 60 * 60,
            )
    return ScanFeedResult(
        listings=listings,
        stats=stats,
        discovered_ids=discovered_ids,
        mapping_audit=mapping_audit,
    )


def _load_daily_chart_shadow_catalog(cash_catalog):
    """Load the exact previous-day chart catalogue or fail closed."""
    if not CASH_FILTER or not cash_catalog or not cash_catalog.get("bloxswaps_exact_map"):
        print("[daily-shadow] FAIL-CLOSED: cash mode or exact BloxSwaps identity map is unavailable.")
        return None
    try:
        from tools.robase_daily_chart_prices import load_exact_previous_day_prices
        target_day, daily_prices = load_exact_previous_day_prices()
        daily_shadow_catalog, daily_stats = build_daily_chart_shadow_catalog(
            cash_catalog, daily_prices
        )
    except Exception as shadow_error:
        print(
            f"[daily-shadow] FAIL-CLOSED: previous-day chart archive unavailable or invalid: "
            f"{type(shadow_error).__name__}: {str(shadow_error)[:240]}"
        )
        return None
    return target_day, daily_shadow_catalog, daily_stats


def _daily_shadow_rejection_category(reason: str) -> str:
    if "(no exact ROBASE/BloxSwaps identity mapping)" in reason:
        return "unmapped identity"
    if "(ROBASE price not freshly verified)" in reason:
        return "no eligible daily price"
    if "wildcards or incomplete trade sides" in reason:
        return "wildcard/incomplete offer"
    if "quantity" in reason:
        return "uncertain quantity"
    if "mismatch" in reason or "type mismatch" in reason:
        return "identity metadata mismatch"
    return "other validation failure"


def _evaluate_daily_shadow_listings(
    listings, cash_catalog, daily_shadow_catalog, daily_stats, target_day
) -> None:
    """Evaluate the daily chart shadow and print a baseline comparison only."""
    print(
        f"[daily-shadow] date={target_day}; weapons={daily_stats['weapons_total']}; "
        f"eligible={daily_stats['eligible']}; excluded={daily_stats['unavailable_or_low_sample']}; "
        f"non_daily_catalogue_entries_disabled={daily_stats['non_daily_catalogue_entries_disabled']}"
    )
    shadow_evaluated = shadow_qualified = shadow_rejected = 0
    shadow_samples = []
    shadow_rejection_reasons = {}
    shadow_qualified_ids = set()
    shadow_baseline_qualified_ids = set()
    shadow_baseline_profit_by_id = {}
    shadow_daily_profit_by_id = {}
    for shadow_listing in listings:
        shadow_evaluated += 1
        listing_id = str(shadow_listing.get("id") or "")
        baseline_result, _baseline_missing = evaluate_cash_listing_robase_only(
            dict(shadow_listing), cash_catalog
        )
        if baseline_result is not None:
            baseline_threshold = max(
                1.0,
                CASH_MIN_PROFIT_USD_SMALL
                if baseline_result["give_total"] < 5.0
                else CASH_MIN_PROFIT_USD,
            )
            if baseline_result["cash_profit_worst"] >= baseline_threshold and listing_id:
                shadow_baseline_qualified_ids.add(listing_id)
                shadow_baseline_profit_by_id[listing_id] = baseline_result["cash_profit_worst"]

        result, missing = evaluate_cash_listing_robase_only(
            dict(shadow_listing), daily_shadow_catalog
        )
        if result is None:
            shadow_rejected += 1
            for reason in (missing or ["unspecified rejection"]):
                category = _daily_shadow_rejection_category(str(reason))
                shadow_rejection_reasons[category] = shadow_rejection_reasons.get(category, 0) + 1
            if missing and len(shadow_samples) < 8:
                shadow_samples.append(
                    f"{shadow_listing.get('id', 'unknown')}: "
                    + "; ".join(str(value) for value in missing[:3])
                )
            continue
        daily_threshold = max(
            1.0,
            CASH_MIN_PROFIT_USD_SMALL
            if result["give_total"] < 5.0
            else CASH_MIN_PROFIT_USD,
        )
        if listing_id:
            shadow_daily_profit_by_id[listing_id] = result["cash_profit_worst"]
        if result["cash_profit_worst"] >= daily_threshold:
            shadow_qualified += 1
            if listing_id:
                shadow_qualified_ids.add(listing_id)
            print(
                f"[daily-shadow] QUALIFIED listing={shadow_listing.get('id')} "
                f"profit={result['cash_profit_worst']:.2f}..{result['cash_profit_best']:.2f} USD "
                f"threshold={daily_threshold:.2f} USD date={target_day}"
            )

    print(
        f"[daily-shadow] COMPLETE date={target_day}; listings={shadow_evaluated}; "
        f"qualified_at_{CASH_MIN_PROFIT_USD:.2f}_USD_floor={shadow_qualified}; "
        f"rejected_or_ineligible={shadow_rejected}; telegram_sends=0; live_catalog_mutations=0"
    )
    comparable_ids = shadow_qualified_ids & shadow_baseline_qualified_ids
    profit_deltas = sorted(
        shadow_daily_profit_by_id[item_id] - shadow_baseline_profit_by_id[item_id]
        for item_id in comparable_ids
        if item_id in shadow_daily_profit_by_id and item_id in shadow_baseline_profit_by_id
    )
    median_profit_delta = None
    if profit_deltas:
        middle = len(profit_deltas) // 2
        median_profit_delta = (
            profit_deltas[middle] if len(profit_deltas) % 2
            else (profit_deltas[middle - 1] + profit_deltas[middle]) / 2
        )
    print(
        f"[daily-shadow] COMPARISON baseline_qualified={len(shadow_baseline_qualified_ids)} "
        f"daily_qualified={len(shadow_qualified_ids)} overlap={len(comparable_ids)} "
        f"daily_only={len(shadow_qualified_ids - shadow_baseline_qualified_ids)} "
        f"baseline_only={len(shadow_baseline_qualified_ids - shadow_qualified_ids)} "
        f"common_profit_delta_daily_minus_baseline_median={median_profit_delta}"
    )
    print(
        f"[daily-shadow] SUMMARY unique_qualified_ids={len(shadow_qualified_ids)} "
        f"rejection_reason_counts={json.dumps(shadow_rejection_reasons, ensure_ascii=False, sort_keys=True)}"
    )
    for sample in shadow_samples:
        print(f"[daily-shadow] REJECTED {sample}")


def _run_daily_chart_shadow(listings, cash_catalog) -> int:
    """Run the daily-chart shadow without sending alerts or mutating live prices."""
    context = _load_daily_chart_shadow_catalog(cash_catalog)
    if context is None:
        return 0
    target_day, daily_shadow_catalog, daily_stats = context
    _evaluate_daily_shadow_listings(
        listings, cash_catalog, daily_shadow_catalog, daily_stats, target_day
    )
    return 0


def _merge_optional_discord_listings(listings, cash_filter: bool):
    """Merge only fresh authorized collector listings outside the cash-only path."""
    if cash_filter:
        print("[discord-parser] skipped: ROBASE cash mode accepts BloxSwaps offers only")
        return listings

    # The collector writes a bounded local JSON file; keep its IDs namespaced
    # away from BloxSwaps cursors.
    from discord_parser import load_discord_listings

    try:
        discord_listings, discord_rejected = load_discord_listings()
    except Exception as discord_error:
        discord_listings, discord_rejected = [], 0
        print(
            f"[discord-parser] source skipped safely: "
            f"{type(discord_error).__name__}; BloxSwaps scan continues"
        )

    existing_ids = {str(row.get("id") or "") for row in listings}
    fresh_discord = [
        row for row in discord_listings
        if str(row.get("id") or "") not in existing_ids
    ]
    if fresh_discord:
        listings.extend(fresh_discord)
    print(
        f"[discord-parser] parsed={len(discord_listings)} "
        f"new_for_batch={len(fresh_discord)} rejected={discord_rejected}"
    )
    return listings


def _assign_first_seen_timestamps(
    listings, discovered_ids, first_seen_ads: dict, observed_at: str
) -> tuple[dict, bool]:
    """Assign stable first-seen timestamps and report whether state changed."""
    changed = False
    for ad_id in discovered_ids:
        key = str(ad_id)
        if key and key not in first_seen_ads:
            first_seen_ads[key] = observed_at
            changed = True
    for listing in listings:
        source_ad_id = str(listing.get("source_ad_id") or "")
        if listing.get("source") == "discord":
            listing["first_seen_at_utc"] = listing.get("posted_at") or observed_at
        else:
            listing["first_seen_at_utc"] = first_seen_ads.get(source_ad_id, observed_at)
    return first_seen_ads, changed


def _apply_cold_start_baseline(
    stats, discovered_ids, listings, seen_ads, pending_ids, pending_schedule,
    cash_filter: bool, cash_catalog,
) -> tuple[set, bool]:
    """Persist a cold-start baseline and signal the no-recent-listings early exit."""
    if not stats.get("cold_start"):
        return set(seen_ads or []), False

    seed_ids = set(str(value) for value in stats.get("cold_start_seed_ids", discovered_ids))
    save_seen_ads(seed_ids)
    seen_ads = seed_ids
    seed_pending_ids_as_seen(seen_ads, pending_ids, pending_schedule)
    save_seen_ads(seen_ads)
    print(
        f"[state] Cold-start baseline saved for {len(seed_ids)} historical posts; "
        f"{len(stats.get('cold_start_recent_ids', []))} posts refreshed within 60 minutes will be evaluated."
    )
    if listings:
        return seen_ads, False

    record_success_if_complete(stats)
    if cash_filter:
        save_cash_catalog_snapshot(cash_catalog["sha256"])
    write_step_summary("Cold-start baseline saved; no verifiably recent post was eligible for evaluation.")
    return seen_ads, True


def _reconcile_feed_pending_state(stats, seen_ads, pending_ids, pending_schedule):
    """Schedule technical parser retries and retire confirmed non-actionable IDs."""
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
    single_sided_ids = set(stats.get("single_sided_ids", []))
    pending_not_found_ids = set(stats.get("pending_not_found_ids", []))
    non_actionable_ids = addition_ids | single_sided_ids | pending_not_found_ids
    if non_actionable_ids:
        seen_ads.update(non_actionable_ids)
        for ad_id in non_actionable_ids:
            key = str(ad_id)
            pending_ids.pop(key, None)
            pending_schedule.pop(key, None)
        save_seen_ads(seen_ads)
        save_pending_ids(pending_ids)
        save_pending_recheck_schedule(pending_schedule)
    return seen_ads, pending_ids, pending_schedule, addition_ids, single_sided_ids, pending_not_found_ids


def _log_feed_reconciliation_diagnostics(stats) -> None:
    """Report feed items that were deliberately deferred or retired."""
    pending_not_found_ids = stats.get("pending_not_found_ids", [])
    single_sided_ids = stats.get("single_sided_ids", [])
    if pending_not_found_ids:
        print(
            f"[pending] Removed {len(pending_not_found_ids)} unavailable listing(s) "
            "after authoritative direct 404 lookups."
        )
    if single_sided_ids:
        print(
            f"[bloxswaps] Excluded {len(single_sided_ids)} single-sided posts; "
            "they are not trade opportunities and will not be retried as parser failures."
        )
    if stats.get("watch_not_found_ids"):
        print(
            f"[watch] Removed {len(stats['watch_not_found_ids'])} expired watched listing(s) "
            "after authoritative direct 404 lookups."
        )
    if stats.get("watch_remaining_ids"):
        print(
            f"[watch] {len(stats['watch_remaining_ids'])} direct lookups were inconclusive; "
            "they remain in the rotating watch queue for a later retry."
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


def _handle_empty_scan_feed(stats, listings, cash_catalog, cash_filter: bool) -> bool:
    """Handle verified empty/current feed outcomes; raise on unverifiable emptiness."""
    if listings:
        return False
    update_metrics(increments={"parse_empty": 1})
    html_issue_count = max(
        int(stats.get("html_malformed_count", 0)),
        int(stats.get("html_parse_warning_count", 0)),
    )
    if stats.get("seen_boundary_reached") and not stats.get("malformed_count") and not html_issue_count:
        print("[bloxswaps] Feed is current: first visible offers were already processed.")
        record_success_if_complete(stats)
        if cash_filter:
            save_cash_catalog_snapshot(cash_catalog["sha256"])
        write_step_summary("Feed current: no new trade posts before the seen cursor boundary.")
        return True
    if stats["candidate_count"] > 0 and stats["addition_count"] == stats["candidate_count"]:
        print(f"[bloxswaps] No eligible listings: all {stats['addition_count']} candidate cards contained Ajouts.")
        record_success_if_complete(stats)
        if cash_filter:
            save_cash_catalog_snapshot(cash_catalog["sha256"])
        write_step_summary("Successful scan: all current candidates excluded for Ajouts.")
        return True
    if stats["candidate_count"] == 0 and stats.get("pagination_source") == "html-only":
        raise RuntimeError("[bloxswaps] No trade cards parsed; page structure may have changed or feed is empty.")
    if stats["candidate_count"] == 0 and not stats.get("pagination_complete"):
        raise RuntimeError("[bloxswaps] No feed entries parsed and pagination could not be verified.")
    return False


def _report_scan_feed_quality(stats):
    """Report malformed, unidentified, and incomplete feed results without hiding them."""
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
    unidentified_feed_records = int(stats.get("unidentified_feed_record_count", 0) or 0)
    if unidentified_feed_records:
        sample_types = ", ".join(stats.get("unidentified_feed_samples", [])[:5])
        print(
            f"[maintenance] API feed records missing stable IDs: {unidentified_feed_records}; "
            f"sample_types={sample_types}; counted as malformed candidates and retried on later polls."
        )
        send_maintenance_alert(
            "feed-records-without-stable-id",
            f"BloxSwaps returned {unidentified_feed_records} feed record(s) without a stable listing ID. "
            "They were counted as malformed candidates, not silently omitted; inspect parser/API diagnostics.",
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


def _load_uncertain_delivery_state():
    """Reconcile durable Telegram uncertainty and report aged quarantines without resending."""
    alerted = load_alerted()
    alerted_set = set(alerted)
    uncertain_state, _ = load_uncertain_state()
    uncertain_context = load_uncertain_context()
    uncertain_state, uncertain_context, quarantine_reconciliation = reconcile_uncertain_quarantine(
        uncertain_state, alerted_set, uncertain_context
    )
    if quarantine_reconciliation["confirmed_quarantines_cleared"] or quarantine_reconciliation["orphan_contexts_removed"]:
        save_uncertain_state(uncertain_state)
        save_uncertain_context(uncertain_context)
        print(
            "[telegram-state] Reconciled durable delivery state: "
            f"confirmed_quarantines_cleared={len(quarantine_reconciliation['confirmed_quarantines_cleared'])} "
            f"orphan_contexts_removed={len(quarantine_reconciliation['orphan_contexts_removed'])} "
            f"unresolved_remaining={len(uncertain_state)}"
        )
    uncertain_ids = set(uncertain_state)
    now = time.time()
    aged_uncertain = [
        ad_id for ad_id, timestamp in uncertain_state.items()
        if now - timestamp >= UNCERTAIN_REVIEW_AFTER_SECONDS
    ]
    if aged_uncertain:
        review_lines = []
        for uncertain_id in aged_uncertain[:8]:
            entry = uncertain_context.get(uncertain_id, {})
            source_id = entry.get("source_ad_id") or uncertain_id
            listing_url = entry.get("listing_url")
            review_lines.append(
                f"{source_id}: {listing_url}" if listing_url else f"{source_id} (internal ID {uncertain_id})"
            )
        context_suffix = (
            "\nManual review references:\n" + "\n".join(review_lines)
            if review_lines else ""
        )
        send_maintenance_alert(
            "telegram-quarantine-review",
            f"{len(aged_uncertain)} Telegram delivery status(es) remain unresolved for at least 24 hours. "
            "They remain quarantined indefinitely; check Telegram manually before clearing any entry. "
            "No automatic resend will occur."
            + context_suffix,
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
    return alerted, alerted_set, uncertain_ids


def _build_opportunity_telegram_message(op, market_info, values):
    """Format one opportunity with explicit manual-review and valuation warnings."""
    listing = op["listing"]
    message = format_trade_message(
        listing, op["give_total"], op["give_lines"],
        op["receive_total"], op["receive_lines"], op["difference"],
        market_info=market_info,
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
    if listing.get("valuation_mode") != "cash":
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
    if listing.get("valuation_mode") != "cash":
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
    return message


def scan_once(include_backlog_rechecks=True):
    global _SESSION_ALERTS_SENT
    scan_modes = resolve_scan_modes(
        os.environ,
        cash_filter=CASH_FILTER,
        bot_token=BOT_TOKEN,
        chat_id=CHAT_ID,
    )
    daily_shadow_mode = scan_modes.daily_shadow_mode
    value_profit_shadow_mode = scan_modes.value_profit_shadow_mode
    dry_run = scan_modes.dry_run
    testing_mode = scan_modes.testing_mode
    if scan_modes.force_dry_run:
        # Shadow modes are strictly non-delivering, even if a caller misconfigures DRY_RUN.
        os.environ["DRY_RUN"] = "true"
    update_metrics(increments={"scans": 1})

    if value_profit_shadow_mode:
        print("[stage] Offline Supreme value-profit shadow from the daily RBLXValue archive; no API key or API requests")
    else:
        print("[stage] Third-party valuation sources disabled; cash eligibility uses ROBASE + BloxSwaps only")

    market_data = _load_scan_market_data(scan_modes)
    values = market_data.values
    cash_catalog = market_data.cash_catalog
    cash_prices_changed = market_data.cash_prices_changed
    telegram_market_info = market_data.telegram_market_info

    history_data = _open_scan_history_data(scan_modes, values)
    values_snapshot_changed = history_data.values_snapshot_changed
    history_store = history_data.history_store
    public_history_store = history_data.public_history_store

    persistent_state = _load_scan_persistent_state(
        include_backlog_rechecks,
        values_snapshot_changed,
        cash_prices_changed,
    )
    aliases = persistent_state.aliases
    seen_ads = persistent_state.seen_ads
    pending_ids = persistent_state.pending_ids
    pending_schedule = persistent_state.pending_schedule
    watch_state = persistent_state.watch_state
    due_pending_ids = persistent_state.due_pending_ids

    feed_result = _fetch_scan_feed_and_audit(persistent_state, cash_catalog)
    listings = feed_result.listings
    stats = feed_result.stats
    discovered_ids = feed_result.discovered_ids
    mapping_audit = feed_result.mapping_audit

    if daily_shadow_mode:
        return _run_daily_chart_shadow(listings, cash_catalog)
    pending_schedule = record_pending_recheck_attempts(
        due_pending_ids, pending_ids, pending_schedule
    )
    listings = _merge_optional_discord_listings(listings, CASH_FILTER)
    first_seen_ads = load_first_seen_ads()
    observed_at = datetime.now(timezone.utc).isoformat()
    first_seen_ads, first_seen_changed = _assign_first_seen_timestamps(
        listings, discovered_ids, first_seen_ads, observed_at
    )
    if first_seen_changed:
        save_first_seen_ads(first_seen_ads)
    seen_ads, stop_after_cold_start = _apply_cold_start_baseline(
        stats, discovered_ids, listings, seen_ads, pending_ids, pending_schedule,
        CASH_FILTER, cash_catalog,
    )
    if stop_after_cold_start:
        return
    (
        seen_ads, pending_ids, pending_schedule,
        addition_ids, single_sided_ids, pending_not_found_ids,
    ) = _reconcile_feed_pending_state(stats, seen_ads, pending_ids, pending_schedule)
    _log_feed_reconciliation_diagnostics(stats)
    if _handle_empty_scan_feed(stats, listings, cash_catalog, CASH_FILTER):
        return
    parsed_count = len(listings)
    listings = deduplicate_listings(listings)
    from tools.scan_outcomes import ScanOutcomeLedger
    outcome_ledger = ScanOutcomeLedger(listing.get("id") for listing in listings)
    if history_store is not None:
        try:
            stored = history_store.record_listings(listings)
            print(f"[market-history] Listings recorded or refreshed: {stored}")
        except Exception as history_error:
            print(f"[market-history] Listing storage skipped safely: {type(history_error).__name__}")
    if public_history_store is not None:
        try:
            stored = public_history_store.record_listings(listings)
            print(f"[public-dashboard-history] Public BloxSwaps listings recorded or refreshed: {stored}")
        except Exception as history_error:
            raise RuntimeError(
                "Public dashboard listing history could not be persisted; refusing to send alerts."
            ) from history_error
    duplicate_count = parsed_count - len(listings)
    if duplicate_count:
        print(f"[dedupe] Ignored {duplicate_count} duplicate listing row(s) in this scan batch.")
    print(
        f"[bloxswaps] candidate_cards={stats.get('candidate_count', 0)} "
        f"excluded_ajouts={stats.get('addition_count', 0)} "
        f"excluded_offres={stats.get('offers_excluded_count', 0)} "
        f"eligible_after_wildcard_filter={len(listings)}"
    )
    print(f"[bloxswaps] Direct owner profiles parsed: {stats.get('owner_link_count', 0)}/{len(listings)}")
    print(
        f"[pagination] pages={stats.get('pagination_pages', 1)} "
        f"api_posts={stats.get('pagination_api_posts', 0)} "
        f"complete={stats.get('pagination_complete', False)} "
        f"seen_boundary={stats.get('seen_boundary_reached', False)}"
    )
    _report_scan_feed_quality(stats)
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
    alerted, alerted_set, uncertain_ids = _load_uncertain_delivery_state()
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
    def deliver_opportunity(op, send=True, delivery_confirmed=False):
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
        legacy_cash_watch = CASH_FILTER and not testing_mode and (not previous_watch or previous_watch.get("cash_source_version") != 2)
        variant_changed = watch_variant_changed(
            previous_watch, current_content_hash, current_values_hash, cash_filter=(CASH_FILTER and not testing_mode)
        )
        if legacy_cash_watch and trade_id in alerted_set and not variant_changed:
            previous_cash_profit = last_cash_profit_for_listing(trade_id)
            if previous_cash_profit is not None and op.get("cash_profit_worst", 0) > previous_cash_profit + 0.009:
                variant_changed = True
        if not delivery_confirmed and ((trade_id in alerted_set and not variant_changed) or trade_id in uncertain_ids):
            if legacy_cash_watch and trade_id in alerted_set:
                # One-time source migration: update the fingerprint without replaying old alerts.
                update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
            print(f"[dedupe] Skipping unchanged or uncertain listing {trade_id} before Telegram send.")
            outcome_ledger.record(
                trade_id,
                "delivery_quarantined" if trade_id in uncertain_ids else "delivery_guard_skipped",
            )
            return
        if not delivery_confirmed and os.environ.get("DRY_RUN", "").strip().casefold() == "true":
            print(f"[dry-run] would send: {trade_id} profit={op['difference']:g}")
            outcome_ledger.record(trade_id, "dry_run_preview")
            return
        if send or not listing.get("alert_attempted_at_utc"):
            listing["alert_attempted_at_utc"] = datetime.now(timezone.utc).isoformat()
        message = _build_opportunity_telegram_message(op, telegram_market_info, values)
        trade_id = listing["id"]
        if send:
            if listing.get("valuation_mode") == "cash":
                print(
                    f"[telegram] Sending immediate cash trade id={trade_id}; "
                    f"cash_profit_usd={op['cash_profit_worst']:g}"
                )
            else:
                print(f"[telegram] Sending ranked value trade id={trade_id}; profit={op['difference']:g}")
            # Persist a delivery guard before the non-idempotent Telegram request.
            uncertain_ids.add(trade_id)
            record_uncertain_context(listing, trade_id, "individual")
            save_uncertain_ids(uncertain_ids)
            _SESSION_ALERTS_SENT += 1
            try:
                send_telegram(message)
            except TelegramDeliveryUncertain as exc:
                source_id = listing.get("source_ad_id") or "unknown"
                print(
                    f"[telegram] Delivery uncertain for listing {trade_id}; source_ad_id={source_id}; "
                    f"reason={redact(exc)[:180]}; quarantined, skipping this message."
                )
                mark_seen_ad(listing)
                outcome_ledger.record(trade_id, "delivery_quarantined")
                return
            except Exception as exc:
                uncertain_ids.discard(trade_id)
                save_uncertain_ids(uncertain_ids)
                clear_uncertain_context(trade_id)
                mark_pending(listing, reason="telegram_failure")
                print(
                    f"[telegram] Definite send failure for listing {trade_id}: "
                    f"{type(exc).__name__}; queued for a later scan and continuing the batch."
                )
                outcome_ledger.record(trade_id, "delivery_failed_queued")
                return
        else:
            print(f"[telegram] Finalizing listing {trade_id} already delivered in the digest.")
        finalize_confirmed_telegram_delivery(
            trade_id,
            uncertain_ids,
            mark_alerted,
            lambda: update_watch_entry(watch_state, listing, values, aliases, cash_catalog),
            save_uncertain_ids,
            clear_uncertain_context,
        )
        mark_seen_ad(listing)
        listing["alerted_at_utc"] = datetime.now(timezone.utc).isoformat()
        if listing.get("source") != "discord":
            append_history(listing, op)
        first_seen_ts = _parse_feed_timestamp(listing.get("first_seen_at_utc"))
        refreshed_ts = _parse_feed_timestamp(listing.get("posted_at"))
        alerted_ts = _parse_feed_timestamp(listing["alerted_at_utc"])
        detection_latency = (
            max(0.0, alerted_ts - first_seen_ts)
            if first_seen_ts is not None and alerted_ts is not None else None
        )
        refresh_latency = (
            max(0.0, alerted_ts - refreshed_ts)
            if refreshed_ts is not None and alerted_ts is not None else None
        )
        detection_label = f"{detection_latency:.2f}s" if detection_latency is not None else "unknown"
        refresh_label = f"{refresh_latency:.2f}s" if refresh_latency is not None else "unknown"
        print(
            f"[timing] listing={listing.get('source_ad_id') or trade_id} "
            f"first_seen={listing.get('first_seen_at_utc', 'unknown')} "
            f"feed_refreshed_at={listing.get('posted_at', 'unknown')} "
            f"alerted={listing['alerted_at_utc']} "
            f"scanner_first_seen_to_alert={detection_label} "
            f"feed_refresh_to_alert={refresh_label}"
        )
        sent += 1
        update_metrics(increments={"alerts_sent": 1})
        outcome_ledger.record(trade_id, "alert_sent")

    def deliver_digest(items):
        global _SESSION_ALERTS_SENT
        eligible = []
        for op in items:
            listing = op["listing"]
            trade_id = listing["id"]
            watch_id = str(listing.get("source_ad_id") or "")
            previous_watch = watch_state.get(watch_id, {})
            current_content_hash, current_values_hash = listing_watch_fingerprints(
                listing, values, aliases, cash_catalog
            )
            variant_changed = bool(previous_watch) and (
                previous_watch.get("content_hash") != current_content_hash
                or previous_watch.get("values_hash") != current_values_hash
            )
            if (trade_id in alerted_set and not variant_changed) or trade_id in uncertain_ids:
                outcome_ledger.record(
                    trade_id,
                    "delivery_quarantined" if trade_id in uncertain_ids else "delivery_guard_skipped",
                )
                continue
            eligible.append(op)
        if not eligible:
            return 0
        if os.environ.get("DRY_RUN", "").strip().casefold() == "true":
            print(f"[dry-run] would send one digest containing {len(eligible)} additional qualified offers")
            for op in eligible:
                outcome_ledger.record(op["listing"]["id"], "dry_run_preview")
            return 0
        def prepare_digest_item(op):
            listing = op["listing"]
            listing["alert_attempted_at_utc"] = datetime.now(timezone.utc).isoformat()
            uncertain_ids.add(listing["id"])
            record_uncertain_context(listing, listing["id"], "digest")
            # Persist one offer at a time before its non-idempotent send.
            save_uncertain_ids(uncertain_ids)

        outcomes = deliver_digest_items_individually(
            eligible,
            prepare_digest_item,
            lambda op: send_telegram(format_opportunity_digest([op], market_info=telegram_market_info)),
        )
        delivered = 0
        for status, op, error in outcomes:
            listing = op["listing"]
            trade_id = listing["id"]
            if status == "uncertain":
                source_id = listing.get("source_ad_id") or "unknown"
                print(
                    f"[telegram] Digest delivery uncertain for listing {trade_id}; "
                    f"source_ad_id={source_id}; reason={redact(error)[:180]}; only this offer is quarantined."
                )
                mark_seen_ad(listing)
                outcome_ledger.record(trade_id, "delivery_quarantined")
            elif status in ("failed", "not_attempted"):
                uncertain_ids.discard(trade_id)
                save_uncertain_ids(uncertain_ids)
                clear_uncertain_context(trade_id)
                mark_pending(listing, reason="telegram_failure")
                outcome_ledger.record(trade_id, "delivery_failed_queued")
                if status == "failed":
                    print(
                        f"[telegram] Digest send failed definitely for listing {trade_id}: "
                        f"{type(error).__name__}; remaining offers queued for retry."
                    )
            else:
                # Remove only from this process's in-memory set. Keep the durable
                # quarantine until the alerted/watch checkpoints are persisted.
                uncertain_ids.discard(trade_id)
                deliver_opportunity(op, send=False, delivery_confirmed=True)
                delivered += 1
        save_uncertain_ids(uncertain_ids)
        _SESSION_ALERTS_SENT += delivered
        if delivered:
            update_metrics(increments={
                "alerts_digest_messages": delivered,
                "alerts_digest_items": delivered,
            })
        return delivered

    cash_blocker_counts = {}
    cash_blocker_groups = {}
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
            outcome_ledger.record(trade_id, "already_alerted")
            continue
        if legacy_state == "uncertain":
            print(f"[state-migration] Legacy uncertain-delivery ID matched; promoting quarantine for {trade_id}.")
            uncertain_ids.add(trade_id)
            save_uncertain_ids(uncertain_ids)
            record_uncertain_context(listing, trade_id, "recovered")
            mark_seen_ad(listing)
            uncertain_skipped += 1
            outcome_ledger.record(trade_id, "delivery_quarantined")
            continue
        watch_id = str(listing.get("source_ad_id") or "")
        previous_watch = watch_state.get(watch_id, {})
        content_hash, values_hash = listing_watch_fingerprints(listing, values, aliases, cash_catalog)
        legacy_cash_watch = CASH_FILTER and not testing_mode and (
            not previous_watch or previous_watch.get("cash_source_version") != 2
        )
        fingerprint_changed = bool(previous_watch) and (
            previous_watch.get("content_hash") != content_hash
            or previous_watch.get("values_hash") != values_hash
        )
        if trade_id in alerted_set and legacy_cash_watch:
            # Evaluate each old alert once so its saved cash-profit history can decide whether it improved.
            fingerprint_changed = True
        if trade_id in alerted_set and not fingerprint_changed:
            if not previous_watch:
                update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
            already_alerted_skipped += 1
            mark_seen_ad(listing)
            outcome_ledger.record(trade_id, "already_alerted")
            continue
        if previous_watch and not fingerprint_changed and watch_id not in pending_ids:
            watch_unchanged_skipped += 1
            mark_seen_ad(listing)
            outcome_ledger.record(trade_id, "watch_unchanged")
            continue
        if trade_id in uncertain_ids:
            record_uncertain_context(listing, trade_id, "recovered")
            uncertain_skipped += 1
            mark_seen_ad(listing)
            outcome_ledger.record(trade_id, "delivery_quarantined")
            continue
        if CASH_FILTER:
            if testing_mode:
                result, missing = evaluate_cash_listing(listing, values, aliases, cash_catalog, estimate_mode=dry_run)
            else:
                result, missing = evaluate_cash_listing_robase_only(listing, cash_catalog)
        else:
            result, missing = evaluate_listing(listing, values, aliases)
            if result is not None:
                listing["valuation_mode"] = "value"
        evaluated += 1
        if result is None:
            skipped_unknown += 1
            if CASH_FILTER and missing and all("cash price" in str(value).casefold() for value in missing):
                accumulate_cash_blockers(missing, cash_blocker_counts, cash_blocker_groups)
                print(
                    "[cash] Listing held from alerts: one or more ROBASE prices are missing, "
                    "unverified, stale, or below the minimum sales sample."
                )
                update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
                mark_seen_ad(listing, clear_pending=False)
                outcome_ledger.record(trade_id, "watch_missing_cash_price")
                continue
            print(
                f"[skip-unknown] Listing rejected without offer-level deferral; missing/unvalued items: "
                f"{', '.join(missing)}"
            )
            unknown_names = unmatched_names_from_missing(missing, cash_mode=CASH_FILTER)
            if unknown_names:
                record_unmatched_names(unknown_names)
            update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
            # No listing is held for manual review. The item gap is remembered,
            # while the bounded watch index can re-evaluate it after a mapping changes.
            mark_seen_ad(listing)
            outcome_ledger.record(trade_id, "watch_unmapped")
            continue
        if CASH_FILTER and result.get("cash_estimate_only"):
            print(
                f"[dry-run-estimate] listing={trade_id} "
                f"estimated_cash_profit_range=${result['cash_profit_worst']:.2f}.."
                f"${result['cash_profit_best']:.2f}; NOT TRADE-ELIGIBLE"
            )
            for line in result.get("give_lines", []):
                print(f"[dry-run-estimate] GIVE {line}")
            for line in result.get("receive_lines", []):
                print(f"[dry-run-estimate] RECEIVE {line}")
            outcome_ledger.record(trade_id, "dry_run_estimate")
            continue
        if not (qualifies_cash(result) if CASH_FILTER else qualifies(result)):
            uncertain_items = result.get("uncertain_items") or []
            if uncertain_items:
                uncertain_names = [
                    str(item.get("name") if isinstance(item, dict) else item).strip()
                    for item in uncertain_items
                    if str(item.get("name") if isinstance(item, dict) else item).strip()
                ]
                if uncertain_names:
                    record_unmatched_names(uncertain_names)
            update_watch_entry(watch_state, listing, values, aliases, cash_catalog)
            mark_seen_ad(listing)
            outcome_ledger.record(trade_id, "watch_not_qualified")
            continue
        opportunities.append(result)
        outcome_ledger.record(trade_id, "qualified_pending_delivery")
        if history_store is not None:
            try:
                history_store.record_opportunity(result)
            except Exception as history_error:
                print(f"[market-history] Opportunity storage skipped safely: {type(history_error).__name__}")
        if public_history_store is not None:
            try:
                public_history_store.record_opportunity(result)
            except Exception as history_error:
                raise RuntimeError(
                    "Public dashboard opportunity history could not be persisted; refusing to send alerts."
                ) from history_error
        if CASH_FILTER:
            # Persist the public cash opportunity before the non-idempotent Telegram send.
            deliver_opportunity(result)
        # Non-cash delivery is deferred until all candidates are ranked.

    if CASH_FILTER and cash_blocker_counts:
        update_metrics(
            cash_blockers=cash_blocker_counts,
            cash_blocker_groups=cash_blocker_groups,
        )

    if CASH_FILTER:
        selected, digest_items, digest_sent = [], [], 0
    else:
        max_per_scan = MAX_ALERTS_PER_SCAN
        selected, digest_items = rank_and_split(opportunities, max_per_scan)
        for opportunity in selected:
            deliver_opportunity(opportunity)
        digest_sent = deliver_digest(digest_items) if digest_items else 0
    digest_not_sent = max(0, len(digest_items) - digest_sent)
    outcome_counts = outcome_ledger.finalize()
    summary = (
        f"mode={'cash' if CASH_FILTER else 'value'} | Parsed={len(listings)} | evaluated={evaluated} | unmatched={skipped_unknown} | "
        f"already_alerted={already_alerted_skipped} | watch_unchanged={watch_unchanged_skipped} | "
        f"uncertain_quarantined_current={uncertain_skipped} | uncertain_quarantined_total={len(uncertain_ids)} | "
        f"pending_recheck={len(pending_ids)} | qualified={len(opportunities)} | "
        f"alerts={sent} | digest_items={digest_sent} | digest_not_sent={digest_not_sent} | "
        f"outcomes={json.dumps(outcome_counts, sort_keys=True)}"
    )
    print(f"[summary] {summary}")
    write_step_summary(summary)
    if os.getenv("MM2_TELEGRAM_SCAN_SUMMARY", "").strip().casefold() in {"1", "true", "yes", "on"}:
        send_maintenance_alert(
            "scan-results-summary",
            "📊 MM2 SCAN RESULTS\n"
            f"Candidates: {stats.get('candidate_count', 0)}\n"
            f"Excluded Ajouts: {stats.get('addition_count', 0)}\n"
            f"Excluded Offres: {stats.get('offers_excluded_count', 0)}\n"
            f"Eligible listings: {len(listings)}\n"
            f"Evaluated: {evaluated} · Qualified: {len(opportunities)}\n"
            f"Alerts sent: {sent} · Digest sent: {digest_sent} · Digest pending: {digest_not_sent}\n"
            f"Telegram quarantine: {len(uncertain_ids)} unresolved delivery IDs (manual verification required)\n"
            "Outcome accounting: "
            + ", ".join(f"{name}={count}" for name, count in outcome_counts.items())
            + "\n"
            + f"RBLXValue API requests this run: {rblxvalue_guard.requests_this_run()}\n"
            + (
                f"ROBASE weapons mapped: {max(0, mapping_audit.get('expected', 0) - len(mapping_audit.get('missing', [])) - len(mapping_audit.get('ambiguous', [])))}/"
                f"{mapping_audit.get('expected', 0)} · non-weapons excluded: {len(mapping_audit.get('non_weapons', []))}"
                if isinstance(mapping_audit, dict) else "ROBASE mapping audit: unavailable"
            ),
            cooldown_seconds=1800,
        )
    record_success_if_complete(stats)
    if CASH_FILTER:
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
        "alerted_at_utc", "listing_id", "profit_supreme", "profit_cash_usd", "profit_basis",
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
        "profit_supreme": "" if listing.get("valuation_mode") == "cash" else round(op["difference"], 2),
        "profit_cash_usd": round(op.get("cash_profit_worst", op["difference"]), 2) if listing.get("valuation_mode") == "cash" else "",
        "profit_basis": "cash_usd" if listing.get("valuation_mode") == "cash" else "supreme_value",
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
    if not isinstance(state, dict):
        print("[failure] Failure checkpoint has an invalid top-level structure; restarting its counter safely.")
        state = {"consecutive_failures": 0}
    previous_count = state.get("consecutive_failures", 0)
    if isinstance(previous_count, bool) or not isinstance(previous_count, int) or previous_count < 0:
        print("[failure] Failure checkpoint counter is invalid; restarting its counter safely.")
        previous_count = 0
    count = previous_count + 1
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
    """Compatibility wrapper for the extracted scan-loop policy."""
    return _minimum_cycle_start_window(duration_seconds)


def should_start_scan_cycle(remaining_seconds, duration_seconds):
    """Compatibility wrapper for the extracted scan-loop policy."""
    return _should_start_scan_cycle(remaining_seconds, duration_seconds)


def should_run_backlog_recheck(cycle_number, every=None):
    """Use the configured cadence while retaining the scanner's public helper."""
    cadence = BACKLOG_RECHECK_EVERY_CYCLES if every is None else max(1, int(every))
    return _should_run_backlog_recheck(cycle_number, cadence)


def _reset_session_alert_budget():
    global _SESSION_ALERTS_SENT, _SESSION_ALERT_LIMIT
    _SESSION_ALERTS_SENT = 0
    _SESSION_ALERT_LIMIT = MAX_ALERTS_PER_SESSION


def _get_session_alert_budget():
    return _SESSION_ALERTS_SENT, _SESSION_ALERT_LIMIT


def run_scan_loop(duration_seconds=720, interval_seconds=45):
    """Compatibility wrapper around the extracted scan-loop orchestrator."""
    return _run_scan_loop(
        duration_seconds,
        interval_seconds,
        scan_once=scan_once,
        should_start_scan_cycle=should_start_scan_cycle,
        should_run_backlog_recheck=should_run_backlog_recheck,
        reset_alert_budget=_reset_session_alert_budget,
        get_alert_budget=_get_session_alert_budget,
        record_failure=record_failure,
        redact=redact,
        rblxvalue_guard=rblxvalue_guard,
        update_metrics=update_metrics,
        time_module=time,
        signal_module=signal,
    )


def main():
    install_redacting_streams()
    if BOT_TOKEN and CHAT_ID:
        print(
            f"[telegram-rate-limit] target_type={TELEGRAM_CHAT_TYPE or 'unknown'} "
            f"minimum_interval={ALERT_DELAY_SECONDS:.2f}s"
        )
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
    duration = SCAN_DURATION_SECONDS
    if duration > 0:
        return run_scan_loop(duration, SCAN_INTERVAL_SECONDS)
    global _SESSION_ALERTS_SENT, _SESSION_ALERT_LIMIT
    _SESSION_ALERTS_SENT = 0
    _SESSION_ALERT_LIMIT = MAX_ALERTS_PER_SESSION
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
