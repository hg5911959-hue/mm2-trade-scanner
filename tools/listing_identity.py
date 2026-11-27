"""Stable BloxSwaps listing identity and legacy delivery-state helpers."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from tools.html_tree import walk_nodes


def identity_items(items, normalize_name):
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


def legacy_stable_listing_id(
    owner_url, receive_items, give_items, listing_url="", listing_key_hint="",
    *, normalize_name,
):
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
        "receive": identity_items(receive_items, normalize_name),
        "give": identity_items(give_items, normalize_name),
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


def stable_listing_id(
    owner_url, receive_items, give_items, listing_url="", listing_key_hint="",
    *, normalize_name,
):
    """Build a durable listing identity, preferring the source ad ID when present."""
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
    elif owner_url:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    else:
        listing_key = {"path": listing.path, "query": sorted(stable_params)}
    # A source ad ID is the durable identity of a BloxSwaps post. Item quantities
    # and card text can change while the post remains the same; including them
    # would create a fresh ID and resend an old alert.
    if ad_values or listing_key_hint:
        identity = {"owner": owner_identity, "listing_key": listing_key}
    else:
        identity = {
            "owner": owner_identity,
            "listing_key": listing_key,
            "receive": identity_items(receive_items, normalize_name),
            "give": identity_items(give_items, normalize_name),
        }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
