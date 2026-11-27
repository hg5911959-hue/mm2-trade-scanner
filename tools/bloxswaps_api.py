"""Structured BloxSwaps trade-post adapter and side classification."""
from __future__ import annotations

import json
from urllib.parse import urlencode


def api_post_is_single_sided(post):
    """True when a structured post has only one side and cannot be valued as a trade."""
    rows = post.get("tradePostItems") if isinstance(post, dict) else None
    if not isinstance(rows, list) or not rows:
        return False
    roles = {
        str(row.get("tradeRole") or "").upper()
        for row in rows if isinstance(row, dict)
    }
    return not {"OFFERED", "REQUESTED"}.issubset(roles)


def api_trade_post_to_listing(
    post, item_map, *, strict_quantity, max_item_quantity,
    stable_listing_id, legacy_stable_listing_id,
):
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
    listing_url = f"https://bloxswaps.com/trade-mm2?{query}"
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
            # Do not coerce booleans or truncate fractional quantities (e.g. 1.7 -> 1).
            # A malformed stack must stay uncertain and cannot qualify for cash alerts.
            quantity = strict_quantity(row.get("quantity"))
        except (TypeError, ValueError):
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
