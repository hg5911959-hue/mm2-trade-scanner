# ROBASE daily All marketplaces chart archive

This is a research-only workflow. It does not feed the scanner's cash valuation, alerts, Telegram messages, or production state.

## Source and definition

The workflow reads the public ROBASE item chart endpoint for every item linked from the public catalogue and reproduces the chart's `total` series.

For each UTC day:

- `all_marketplaces_sold` is the sum of copies sold across the marketplaces in the chart response.
- Each marketplace's raw daily price is reduced by the fee supplied by the ROBASE page.
- `all_marketplaces_price_after_fee` is the sales-weighted average of those after-fee marketplace prices, weighted by copies sold. The denominator includes only sales with a priced observation, matching ROBASE's JavaScript implementation.

No Buyer Price series or independent marketplace valuation columns are exported. Days without a priced observation have a blank price, not zero.

## Outputs

The Actions artifact `robase-chart-diagnostics` contains:

- `weapons_daily_archive.csv`: exact-mapped weapons only.
- `all_items_daily_archive.csv`: full ROBASE catalogue, including non-weapons.
- `archive_manifest.json`: item-level coverage and the exact weapon identity map.
- `report.json`: diagnostics and completeness gates.
- `tooltip_matches.json`: the known screenshot-reference check.

CSV columns:

`robase_slug,item_id,item_name,date,all_marketplaces_sold,all_marketplaces_price_after_fee`

The weapon-only export is filtered with `tools/robase_identity.py`, not fuzzy display-name matching. The workflow marks the weapon archive complete only when all catalogue items have a full 365-day response, the scan has no errors, and all weapon identities are exact and present.

## Current validated snapshot

- Range: 2025-11-26 through 2026-11-25 (UTC), inclusive.
- Catalogue: 222 items, 81,030 daily rows.
- Weapons: 214 exact matches, 78,110 daily rows.
- Non-weapons excluded: 8.
- Missing or ambiguous identity matches: 0.
- Duplicate item/date keys: 0.
- No-sales days represented as zero sold and blank price.

## Important separation and limitation

This is a historical daily chart series, not the existing rolling 7-day/30-day completed-sales cash valuation. Do not wire it into production cash pricing or alert calculations without a separate design and validation.

The diagnostic currently compares the expected screenshot tooltip (2026-11-23, 21 sold, $1.55) against all 222 item series. No exact match was found; the screenshot's item identity or date still needs to be established. This does not invalidate the formula reproduction, but it means that one visual reference has not yet been reconciled.

Actions artifacts currently have a seven-day retention. A separate persistent daily-history store and scheduled append/upsert workflow are required before this becomes a long-term archive.
