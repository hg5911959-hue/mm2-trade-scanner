"""Fail-closed parser for owner-supplied MM2 cash-market Discord posts.

This module never connects to Discord. Callers must supply text that the owner
is authorized to forward or paste. Unknown/ambiguous names and prices are
reported, never guessed.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ALIASES_FILE = Path("cash_item_aliases.json")
MAX_QUANTITY = 5000
SELL_HEADERS = ("selling", "sell", "wts", "quick sell")
BUY_HEADERS = ("buying", "buy", "wtb", "looking for")
PRICE_RE = re.compile(r"(?<![A-Za-z0-9])(?P<price>\d+(?:[.,]\d+)?)\s*(?P<currency>\$+)?\s*(?P<unit>/\s*1|each|ea\b)?", re.I)
INVERSE_PRICE_RE = re.compile(r"(?<!\d)1\s*/\s*(\d+(?:[.,]\d+)?)\s*(\$+)?", re.I)
QTY_RE = re.compile(r"(?i)(?:^|\s)(?:x\s*(\d+)|(\d+)\s*x)(?=\s|$)")
MIN_RE = re.compile(r"(?i)\bmin\s*(\d+)\b")


def normalize_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def load_aliases(path: str | Path = ALIASES_FILE) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"aliases": {}, "ambiguous": []}
    except (OSError, ValueError) as exc:
        raise ValueError("cash item alias file is unreadable; refusing name resolution") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("aliases", {}), dict):
        raise ValueError("cash item alias file has an invalid schema")
    aliases = {}
    for alias, canonical in payload.get("aliases", {}).items():
        key = normalize_key(alias)
        if not key or not isinstance(canonical, str) or not canonical.strip():
            raise ValueError("cash item alias entry is invalid")
        aliases[key] = canonical.strip()
    ambiguous = {normalize_key(value) for value in payload.get("ambiguous", [])}
    return {"aliases": aliases, "ambiguous": ambiguous}


def resolve_cash_item(raw: str, alias_data: dict[str, Any] | None = None) -> tuple[str | None, str]:
    aliases = (alias_data or load_aliases()).get("aliases", {})
    ambiguous = (alias_data or load_aliases()).get("ambiguous", set())
    key = normalize_key(raw)
    if not key or key in ambiguous:
        return None, "unresolved"
    canonical = aliases.get(key)
    if canonical:
        return canonical, "resolved"
    # Explicit Chroma prefix is identity-bearing and is never stripped.
    if key.startswith("chroma "):
        base = key[7:]
        canonical_base = aliases.get(base)
        if canonical_base:
            return "Chroma " + canonical_base, "resolved"
    if key.startswith("c "):
        chroma_alias = aliases.get("chroma " + key[2:])
        if chroma_alias:
            return chroma_alias, "resolved"
    return None, "unresolved"


def _clean_line(value: str) -> str:
    value = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", value)
    value = re.sub(r"<@&\d+>", " ", value)
    value = value.replace("**", "").replace("__", "")
    value = value.replace("—", "-").replace("–", "-")
    value = re.sub(r"^\s*[>•▪●*-]+\s*", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _detect_header(line: str) -> tuple[str | None, str]:
    # QUICK SELL is deliberately a sell-side header.
    match = re.match(r"(?i)^\\s*(quick\\s+sell|selling|sell|wts|buying|buy|wtb|looking\\s+for)\\b\\s*[:：-]?\\s*(.*)$", line)
    if not match:
        return None, line
    token, remainder = match.group(1).casefold(), match.group(2).strip()
    side = "sell" if token in {"quick sell", "selling", "sell", "wts"} else "buy"
    if re.fullmatch(r"(?i)(?:mm2|for\\s+usdt\\s*bep20|usdt\\s*bep20)?", remainder):
        return side, "usdt bep20" if "usdt" in remainder.casefold() else ""
    return side, remainder


def _parse_segment(segment: str, side: str, metadata: dict[str, Any],
                   alias_data: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    line = _clean_line(segment)
    if not line:
        return None, None, None
    if re.search(r"(?i)\b(?:usdt\s*bep20|bep20\s*usdt)\b", line):
        metadata["payment"] = "usdt_bep20"
        line = re.sub(r"(?i)\b(?:usdt\s*bep20|bep20\s*usdt)\b", " ", line).strip()
        if not line:
            return None, None, None
    min_match = MIN_RE.search(line)
    min_quantity = int(min_match.group(1)) if min_match else None
    if min_quantity is not None:
        line = MIN_RE.sub(" ", line)
    qty_match = QTY_RE.search(line)
    quantity = int((qty_match.group(1) or qty_match.group(2))) if qty_match else 1
    if qty_match:
        line = (line[:qty_match.start()] + " " + line[qty_match.end():]).strip()

    inverse = INVERSE_PRICE_RE.search(line)
    per_unit = False
    no_currency = False
    price = None
    if inverse:
        price = float(inverse.group(1).replace(",", "."))
        per_unit = True
        no_currency = not bool(inverse.group(2))
        line = line[:inverse.start()] + " " + line[inverse.end():]
    else:
        price_match = PRICE_RE.search(line)
        if price_match:
            price = float(price_match.group("price").replace(",", "."))
            per_unit = bool(price_match.group("unit"))
            no_currency = not bool(price_match.group("currency"))
            line = line[:price_match.start()] + " " + line[price_match.end():]
    if price is None:
        item_raw = re.sub(r"(?i)\b(?:dm\s*rates|taking\s+low\s+rates)\b", "", line).strip(" -:")
        if not item_raw:
            return None, None, "no_price"
        canonical, status = resolve_cash_item(item_raw, alias_data)
        if side == "buy" and status == "resolved":
            return None, {"item": canonical, "item_key": normalize_key(canonical),
                          "quantity": quantity, "author": metadata.get("author", ""),
                          "message_id": metadata.get("message_id", ""),
                          "url": metadata.get("url", ""), "posted_at": metadata.get("posted_at", "")}, None
        return None, None, "no_price" if status == "resolved" else "unresolved_name"
    if price <= 0 or quantity <= 0 or quantity > MAX_QUANTITY:
        return None, None, "invalid_price_or_quantity"
    if quantity > 1 and not per_unit:
        return None, None, "ambiguous_quantity_price"
    if not per_unit and inverse is None and "/" in segment:
        # Unrecognized slash notation is not interpreted.
        return None, None, "ambiguous_price_notation"
    canonical, status = resolve_cash_item(line.strip(" -:"), alias_data)
    if status != "resolved" or not canonical:
        return None, None, "unresolved_name"
    flags = ["no_currency_symbol"] if no_currency else []
    offer = {
        "side": side, "item": canonical, "item_key": normalize_key(canonical),
        "quantity": quantity, "unit_price_usd": round(price, 4),
        "min_quantity": min_quantity, "payment": metadata.get("payment"),
        "author": metadata.get("author", ""), "message_id": metadata.get("message_id", ""),
        "url": metadata.get("url", ""), "posted_at": metadata.get("posted_at", ""),
        "flags": flags,
    }
    return offer, None, None


def parse_cash_post(text: str, *, author: str = "", message_id: str = "",
                    url: str = "", posted_at: str = "",
                    aliases_path: str | Path = ALIASES_FILE) -> dict[str, Any]:
    """Parse a forwarded/pasted message; returns offers, demand hints and rejections."""
    body = str(text or "")
    if not body.strip() or len(body) > 20000:
        return {"offers": [], "demand_hints": [], "rejected": [{"line": 0, "reason": "empty_or_oversized_message"}]}
    alias_data = load_aliases(aliases_path)
    metadata = {"author": str(author)[:100], "message_id": str(message_id)[:128],
                "url": str(url)[:500], "posted_at": str(posted_at)[:64], "payment": None}
    offers, demand_hints, rejected = [], [], []
    side = None
    seen = set()
    for line_number, raw in enumerate(body.splitlines(), 1):
        line = _clean_line(raw)
        if not line:
            continue
        header, remainder = _detect_header(line)
        if header:
            side = header
            line = remainder
            if not line:
                continue
        # Header may prefix an item directly (e.g. "buying 10x ...").
        if side and re.match(r"(?i)^(?:buying|buy|wtb|looking for|selling|sell|wts|quick sell)\b", line):
            _, line = _detect_header(line)
            line = re.sub(r"(?i)^(?:buying|buy|wtb|looking for|selling|sell|wts|quick sell)\s*[:\-]?\s*", "", line)
        if "|" in line:
            segments = [part for part in line.split("|") if part.strip()]
        else:
            segments = [line]
        for segment in segments:
            if not side:
                continue
            offer, hint, reason = _parse_segment(segment, side, metadata, alias_data)
            if offer:
                fingerprint = (offer["side"], offer["item_key"], offer["quantity"],
                               offer["unit_price_usd"], offer["min_quantity"])
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    offers.append(offer)
                continue
            if hint:
                demand_hints.append(hint)
                continue
            if reason and reason != "no_price":
                rejected.append({"line": line_number, "text": segment[:160], "reason": reason})
    return {"offers": offers, "demand_hints": demand_hints, "rejected": rejected}
