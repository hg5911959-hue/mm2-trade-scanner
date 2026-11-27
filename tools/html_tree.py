"""Small event-based HTML tree used by the BloxSwaps feed parser."""
from __future__ import annotations

import re
from html.parser import HTMLParser


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

