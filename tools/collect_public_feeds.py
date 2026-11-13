"""Manual CLI to validate and collect configured public Reddit RSS feeds."""
from __future__ import annotations

import json
import os
import sys

from market_intelligence.public_sources import collect_public_feeds


def main() -> int:
    raw = os.environ.get("MM2_PUBLIC_REDDIT_FEEDS", "")
    feeds = [value.strip() for value in raw.split(";") if value.strip()]
    if not feeds:
        print("No feeds configured. Set MM2_PUBLIC_REDDIT_FEEDS to semicolon-separated public Reddit RSS URLs.", file=sys.stderr)
        return 2
    result = collect_public_feeds(feeds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
