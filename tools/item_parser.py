"""Pure item-name and quantity extraction from the BloxSwaps HTML tree."""
from __future__ import annotations

import re
import unicodedata

from tools.html_tree import node_text, walk_nodes

MAX_ITEM_QUANTITY = 9999

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
    match = re.search(r"(?:[x×]\s*(\d{1,4})|(\d{1,4})\s*[x×])\s*$", normalized, flags=re.I)
    if match:
        parsed = int(match.group(1) or match.group(2))
        quantity = parsed if 1 <= parsed <= MAX_ITEM_QUANTITY else None
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
    match = re.fullmatch(r"(?:x|×)\s*(\d{1,4})|(?:\d{1,4})\s*(?:x|×)", text, flags=re.I)
    if match:
        number = re.search(r"\d{1,4}", text)
        parsed = int(number.group())
        return parsed if 1 <= parsed <= MAX_ITEM_QUANTITY else None
    if re.fullmatch(r"\d{1,4}", text):
        parsed = int(text)
        return parsed if 1 <= parsed <= MAX_ITEM_QUANTITY else None
    return None


def _explicit_stack_quantities(raw):
    """Return all explicit xN/×N/Nx markers; never parse bare prices as counts."""
    text = _normalize_quantity_text(raw)
    pattern = r"(?<![A-Za-z0-9])(?:x|×)\s*(\d{1,4})(?!\d)|(?<![\d.,])(\d{1,4})\s*(?:x|×)(?![A-Za-z0-9])"
    values = []
    for match in re.finditer(pattern, text, flags=re.I):
        parsed = int(match.group(1) or match.group(2))
        if not 1 <= parsed <= MAX_ITEM_QUANTITY:
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
        if not 1 <= parsed <= MAX_ITEM_QUANTITY:
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


def requested_wildcard_kind(values):
    """Classify non-specific requested wildcard tokens without scanning free text."""
    if not isinstance(values, (list, tuple, set)):
        return ""
    normalized = {
        unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold().strip()
        for value in values
    }
    if normalized & {"add", "ajout", "ajouts"}:
        return "ajout"
    if normalized & {"offer", "offers", "offre", "offres"}:
        return "offres"
    return ""


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
