# MM2 weapon identity review

Generated from the scanner's state on 2026-11-21. This is a review template, not an approved price list.

## How to fill it

- Keep `bloxswaps_item_id` and `bloxswaps_slug` unchanged. They are the stable identity keys from the public BloxSwaps catalogue.
- If the matching Supreme record is already listed in `supreme_candidates`, put its exact `slug` into `approved_supreme_slug` only after checking weapon type, rarity, year and Chroma status.
- If Supreme has no numeric value, enter a verified MM2 game-value number in `manual_supreme_value` and identify the source/date in `manual_value_source`.
- These manual values are Supreme-style in-game value points, NOT USD. Do not enter BloxSwaps cash prices here.
- Leave uncertain rows blank. The scanner must not guess between base/Chroma, gun/knife, or different event years.
- `priority_count_last_full_scan` is the number of appearances in the previous full scan's missing/unvalued diagnostics; zero means it was accumulated in the longer-term unknown-name registry but not seen in that log.

The scanner will later import reviewed mappings by BloxSwaps item ID, so one correction can resolve future listings without relying on ambiguous display names.
