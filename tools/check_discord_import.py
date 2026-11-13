"""Validate a local manual Discord import JSON file; never print message bodies."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def validate(path):
    file = Path(path)
    raw = file.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("file must contain valid UTF-8 JSON") from None
    rows = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('expected an object with a "messages" array')
    if len(rows) > 2000:
        raise ValueError("maximum is 2,000 messages")
    required = ("message_id", "content", "created_at_utc")
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(not str(row.get(key) or "").strip() for key in required):
            raise ValueError(f"row {index + 1} is missing a required field")
    size_kb = len(raw) / 1024
    return len(rows), size_kb, size_kb > 45

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    args = parser.parse_args()
    try:
        count, size, warn = validate(args.file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Valid Discord import: {count} rows; {size:.1f} KB")
    if warn:
        print("WARNING: above 45 KB; GitHub Actions secrets are limited to 48 KB.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
