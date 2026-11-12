#!/usr/bin/env python3
"""Suggest conservative BloxSwaps -> Supreme aliases; only explicit approvals are written."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import tempfile
from pathlib import Path

import scanner


def build_suggestions(catalogue, values):
    """Return exact matches and conservative suggestions without mutating aliases."""
    names = sorted({
        str(item.get("displayName", "")).strip()
        for item in catalogue.values()
        if isinstance(item, dict) and str(item.get("displayName", "")).strip()
    }, key=str.casefold)
    supreme_names = {
        key: str(record.get("name") or key)
        for key, record in values.items()
        if isinstance(record, dict)
    }
    report = []
    for name in names:
        key = scanner.normalize_name(name)
        if key in values:
            report.append({"bloxswaps_name": name, "status": "exact", "canonical": supreme_names[key]})
            continue
        ranked = sorted(
            (
                (difflib.SequenceMatcher(None, key, candidate_key).ratio(), candidate_key, canonical)
                for candidate_key, canonical in supreme_names.items()
            ),
            key=lambda row: (row[0], row[1]),
            reverse=True,
        )
        best = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        candidates = [
            {"name": row[2], "score": round(row[0], 4)}
            for row in ranked[:3]
        ]
        if best and best[0] >= 0.90 and (second is None or best[0] - second[0] >= 0.05):
            report.append({
                "bloxswaps_name": name, "status": "suggested_review_only",
                "candidate": best[2], "score": round(best[0], 4),
                "alternatives": candidates[1:],
            })
        elif candidates and candidates[0]["score"] >= 0.75:
            report.append({
                "bloxswaps_name": name, "status": "ambiguous_manual_review",
                "candidates": candidates,
            })
        else:
            report.append({"bloxswaps_name": name, "status": "unmatched"})
    return report


def apply_reviewed_aliases(approved, catalogue, values, aliases_path):
    """Apply only explicit alias->canonical pairs after exact source/catalogue validation."""
    if not isinstance(approved, dict):
        raise ValueError("Approval file must be a JSON object mapping BloxSwaps name to Supreme name.")
    catalogue_names = {
        scanner.normalize_name(str(item.get("displayName", "")))
        for item in catalogue.values() if isinstance(item, dict)
    }
    path = Path(aliases_path)
    if path.exists():
        try:
            raw_existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Existing aliases file is unreadable; refusing to overwrite it.") from exc
        if not isinstance(raw_existing, dict):
            raise ValueError("Existing aliases file must contain a JSON object.")
        updated = {
            scanner.normalize_name(alias): canonical
            for alias, canonical in raw_existing.items()
            if isinstance(alias, str) and isinstance(canonical, str)
        }
    else:
        updated = {}
    for alias, canonical in approved.items():
        if not isinstance(alias, str) or not isinstance(canonical, str) or not alias.strip() or not canonical.strip():
            raise ValueError("Every approved alias and canonical name must be a non-empty string.")
        alias_key = scanner.normalize_name(alias)
        canonical_key = scanner.normalize_name(canonical)
        if alias_key not in catalogue_names:
            raise ValueError(f"'{alias}' is not an exact name in the current BloxSwaps catalogue.")
        if canonical_key not in values:
            raise ValueError(f"'{canonical}' is not an exact normalized Supreme source_b name.")
        if alias_key in values:
            raise ValueError(f"'{alias}' already matches Supreme exactly; no alias is needed.")
        if alias_key in updated and scanner.normalize_name(updated[alias_key]) != canonical_key:
            raise ValueError(f"'{alias}' already maps to a different canonical item.")
        updated[alias_key] = values[canonical_key].get("name", canonical.strip())

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return len(approved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Optional JSON report path; defaults to stdout.")
    parser.add_argument(
        "--approve",
        help="Optional JSON file of manually reviewed {BloxSwaps name: exact Supreme name} pairs.",
    )
    parser.add_argument("--aliases", default=str(scanner.ALIASES_FILE), help="Aliases file to update after explicit approval.")
    args = parser.parse_args()

    catalogue = scanner.fetch_bloxswaps_items_map()
    values = scanner.load_supreme_values()
    report = build_suggestions(catalogue, values)
    result = {
        "catalogue_items": len(catalogue),
        "supreme_items": len(values),
        "summary": {
            status: sum(1 for row in report if row["status"] == status)
            for status in sorted({row["status"] for row in report})
        },
        "suggestions": report,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Wrote alias review report to {args.output}")
    else:
        print(rendered)

    if args.approve:
        approved = json.loads(Path(args.approve).read_text(encoding="utf-8"))
        count = apply_reviewed_aliases(approved, catalogue, values, args.aliases)
        print(f"Applied {count} explicitly reviewed alias(es) to {args.aliases}.")


if __name__ == "__main__":
    main()
