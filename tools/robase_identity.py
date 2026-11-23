"""Identity-only ROBASE/BloxSwaps crosswalk; no third-party value data."""
import re
import unicodedata

ALIASES = {"classic": "vintage"}

def _slug(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")

def _name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()

def match_robase_row(row, item_map):
    """Match one ROBASE row to one BloxSwaps weapon, rejecting ambiguity."""
    raw_name = str(row.get("name") or "").strip()
    category = str(row.get("robase_category") or "").strip().casefold()
    chroma = category == "chroma" or raw_name.casefold().startswith("chroma ")
    base_name = raw_name[7:].strip() if raw_name.casefold().startswith("chroma ") else raw_name
    expected_rarity = ALIASES.get(category, category)
    image_slug = _slug(row.get("og_image_slug") or row.get("bloxswaps_slug") or "")
    robase_slug = _slug(row.get("robase_slug") or "")
    hints = {v for v in (image_slug, robase_slug) if v}
    for details in (item_map or {}).values():
        if not isinstance(details, dict) or _slug(details.get("slug")) not in hints:
            continue
        is_chroma = details.get("chroma") is True
        rarity = str(details.get("rarity") or "").strip().casefold()
        if _name(details.get("displayName")) == _name(base_name) and ((chroma and is_chroma) or (not chroma and not is_chroma and rarity == expected_rarity)):
            source = "robase_image_slug" if _slug(details.get("slug")) == image_slug else "robase_item_slug"
            return details, source
    candidates = []
    for details in (item_map or {}).values():
        if not isinstance(details, dict):
            continue
        kind = str(details.get("type") or "").strip().casefold()
        rarity = str(details.get("rarity") or "").strip().casefold()
        is_chroma = details.get("chroma") is True
        if kind not in {"knife", "gun"} or _name(details.get("displayName")) != _name(base_name):
            continue
        if (chroma and not is_chroma) or (not chroma and (is_chroma or rarity != expected_rarity)):
            continue
        candidates.append(details)
    exact = [d for d in candidates if _slug(d.get("slug")) in hints]
    if len(exact) == 1:
        return exact[0], "robase_slug"
    if len(candidates) == 1:
        return candidates[0], "unique_name_rarity_type"
    return None, "ambiguous" if candidates else "missing"

def build_robase_bloxswaps_map(cash_catalog, item_map):
    """Build exact BloxSwaps slug -> ROBASE row mapping from identity metadata only."""
    rows = list((cash_catalog or {}).get("items_by_slug", {}).values())
    mapped, missing, ambiguous, non_weapons = {}, [], [], []
    collisions = set()
    for row in rows:
        label = str(row.get("name") or row.get("robase_slug") or "Unknown")
        details, source = match_robase_row(row, item_map)
        if details is None:
            (ambiguous if source == "ambiguous" else missing).append(label)
            continue
        kind = str(details.get("type") or "").strip().casefold()
        if kind not in {"knife", "gun"}:
            non_weapons.append(label)
            continue
        slug = _slug(details.get("slug"))
        if not slug:
            missing.append(label)
            continue
        if slug in mapped:
            collisions.add(slug)
            ambiguous.extend([mapped[slug]["robase_name"], label])
            mapped.pop(slug, None)
            continue
        mapped[slug] = {"bloxswaps_slug": slug, "robase_slug": _slug(row.get("robase_slug")), "robase_name": str(row.get("name") or ""), "robase_identity_source": source, "bloxswaps_type": kind, "bloxswaps_rarity": str(details.get("rarity") or "").strip().casefold(), "bloxswaps_chroma": details.get("chroma") is True, "bloxswaps_tradable": details.get("tradable") is True, "bloxswaps_year": details.get("year")}
    for slug in collisions:
        mapped.pop(slug, None)
    return {"bloxswaps_exact_map": mapped, "robase_total": len(rows), "expected": len(mapped) + len(set(missing)) + len(set(ambiguous)), "exact": len(mapped), "missing": sorted(set(missing)), "ambiguous": sorted(set(ambiguous)), "non_weapons": sorted(set(non_weapons))}
