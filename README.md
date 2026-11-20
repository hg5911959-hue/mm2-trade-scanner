# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with concise Telegram alerts. GitHub Actions targets a scan every 10 minutes; each scan polls the feed every 30 seconds for up to 540 seconds (9 minutes), leaving time for persistence. GitHub cron can be delayed, so this is a target cadence, not a real-time guarantee.

## Cash mode is OFF by default

`CASH_FILTER` defaults to `false`. The scanner uses the value-based filter and does not load the cash-price catalogue, inspect the private cash-price secret, update cash snapshots, or emit cash-price maintenance notices while this flag is off. Cash code, data, and verification tools remain available.

Re-enable cash mode only after reliable fresh verified prices exist:

1. Verify fresh prices with `python tools/verify_cash_prices.py` and review every skipped or ambiguous entry.
2. Set `CASH_FILTER: "true"` in `.github/workflows/scan.yml` (and the matching weekly-report environment when enabling cash reporting).
3. Run the scheduler with `dry_run=true` to preview the results without sending live alerts.
4. Check the preview alerts and catalogue coverage carefully before enabling live mode.

## Trade evaluation

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded. The filter is applied to the individual card and also checks the structured `requestedWildcards` data for `ADD`.
- **Default mode = game-value profit**. When cash mode is disabled, offers are evaluated with the Supreme game-value difference and the configured `MIN_VALUE_DIFFERENCE` / `MIN_PROFIT_PERCENT` thresholds.
- Supreme Values is used for game-value comparison and item identity/stability validation. Items that cannot be matched or valued remain excluded; uncertain listings retain the existing manual-review/recheck protections.
- Optional cash mode compares verified manual resale prices, with configurable resale haircuts and conservative profit bounds. Supreme numeric values are never converted into cash prices.
- Any received item with missing/unrecognized Stability or a blocked status (Decreasing, Receding, Unsafe, Untradeable, Unstable, Volatile, Fluctuating) is excluded. The Stability field is sourced from RBLXValue and is not represented as a direct Supreme website verification.
- Cash prices must be fresh and explicitly verified before they can qualify. The current catalogue contains **190 rows**: 11 user-confirmed USD selling prices and 179 historical references that remain unverified. The 11 confirmed entries are user-attested inputs, not independent marketplace cross-checks; they expire after 14 days unless reconfirmed. In cash mode only, any listing containing an item with a missing, unverified, or expired price is held back.
- **Estimate-only dry run:** when `DRY_RUN=true`, the scanner may use unverified legacy references for exploratory calculations, applying a ±30% uncertainty band and the resale haircut. Every dry-run cash result is labelled as estimate-only and is hard-blocked from the live alert eligibility gate. This uncertainty band is an engineering assumption, not a statistically measured confidence interval or proof of current market prices. Normal production runs continue to reject unverified rows.
- The scanner never uses BloxSwaps' own Win/Loss label, never uses the visible listing price as an item value, and never invents a missing item value or quantity.
- A listing is excluded if any item on the received (`Propose`) side has a risky RBLXValue stability label: `Decreasing`, `Receding`, `Unsafe`, `Untradeable`, `Unstable`, or `Volatile`. If the RBLXValue source provides a missing or unrecognized stability label for a received item, the listing is held back rather than assumed safe. Telegram labels the field `Stability (RBLXValue)` to avoid implying direct verification against the Supreme website; exact Supreme-specific `Decreasing` verification requires an authorized data source.

### Cash-price freshness and bulk verification

Cash prices are a separate marketplace estimate, not a conversion of Supreme value. Only rows marked `verified` with a UTC timestamp no older than `CASH_MAX_AGE_DAYS` (default 14) may qualify. Future timestamps are rejected. Expired rows are held out of calculations and trigger one maintenance notice per day listing the count and up to five oldest entries. The weekly report includes the current verified-row count, rows expiring within three days, and the oldest active verification timestamp.

To update prices safely:

1. Paste a fresh marketplace list into a UTF-8 text file, one item per line.
2. Preview with `python tools/verify_cash_prices.py --input prices.txt --dry-run`, then run without `--dry-run` after reviewing the skipped-name summary. Large price changes are refused by default; use `--force` only after manual review.
3. Check the scanner's cash-price maintenance notice and the weekly report.

For a private catalogue in this public repository, add a GitHub Actions repository secret named `CASH_PRICES_CSV` containing the complete CSV text. The scan workflow validates its header and atomically uses it only for that run; the secret is redacted from logs and the CSV is excluded from state artifacts. When the secret is absent, the tracked `data/cash_prices.csv` remains the fallback. The bulk verifier changes the local/repository CSV only; after reviewing it, update the GitHub secret separately if you use the private option.\n\nThe verifier matches exact normalized names first and only then tries removing a trailing `knife`, `gun`, or `sword`. Unknown and ambiguous names are skipped. It never creates or deletes catalogue entries, never divides a multi-quantity total into a guessed unit price, and writes atomically. Keep the CSV private if marketplace prices are commercially sensitive; the repository fallback is visible to repository readers.

### Quantity handling

Every parsed item carries `quantity`, `quantity_source`, `quantity_min`, `quantity_max`, `listed_price`, and a raw text excerpt. Quantity provenance is one of:

- `structured`: quantity from BloxSwaps RSC/API data or a `data-*` quantity attribute.
- `explicit`: a readable quantity marker in the item's own HTML segment, image alt text, `aria-label`, or `title`.
- `implicit_single`: no quantity marker or malformed quantity hint exists in the isolated item segment, so the item is a single item.
- `uncertain`: quantity markers conflict, are malformed, or disagree with structured data.

The parser supports `x2`, `X2`, `×2`, `2x`, `2×`, split `x` + `2` nodes, Arabic-Indic digits, non-breaking spaces, nested badge elements, and supported `data-*` attributes. It isolates each item between adjacent weapon images and Propose/Recherche boundaries; it does not borrow badges from parent/sibling containers that include another weapon or the opposite side. A decimal listing price such as `750.0` is never interpreted as a quantity.

BloxSwaps' page includes Next.js RSC `initialPage.items` data. The scanner uses its per-item `quantity` and `tradeRole` as the primary source, then compares it with the visible HTML. A disagreement becomes `uncertain`, not a silent choice. The first live card used for regression testing is saved, with owner identifiers redacted, at `tests/fixtures/bloxswaps-live-sample.html`.

If a quantity is uncertain, the scanner computes a conservative best/worst profit interval:
- If the worst-case profit is still positive, it can send a normal trade alert.
- If only the best-case profit is positive, it is held for recheck/manual review and is not advertised as a WIN.
- If even the best-case profit is not positive, it does not send a trade alert.

When cash mode is enabled, Telegram cash alerts show owner/listing links, every item, verified USD reference price, net resale estimate, Stability label, cash replacement cost, net resale proceeds, conservative USD profit and cash ROI. Demand is informational only in cash-first mode. Uncertain quantities are calculated with worst-case bounds; a listing is not alerted if the worst-case cash profit is not positive. Parser provenance, raw excerpts, and HTML/API diagnostics stay in GitHub Actions logs.

### Manual cash-price maintenance

Edit data/cash_prices.csv in the repository. Keep each item name aligned with its canonical Supreme catalogue name. Update cash_price_usd to the cash price you have checked, set status to verified, enter an ISO-8601 UTC timestamp in verified_at_utc, and provide a non-empty source field (for example, 2026-11-14T12:00:00Z). Use status=unverified for estimates and status=disabled to suppress an item. Invalid prices, duplicate normalized names, or verified rows without a timestamp fail validation. On the next successful scan, a changed catalogue hash triggers rechecks of watched and pending offers so previously seen listings can become eligible after a price update. The initial 190 prices are preserved as historical user-provided references, not current market facts.

The default resale haircut is controlled by CASH_SELL_HAIRCUT_PCT (15%), the minimum net cash profit by CASH_MIN_PROFIT_USD ($0.50), and the minimum cash ROI by CASH_MIN_MARGIN_PERCENT (0% by default). These are conservative starting thresholds and should be calibrated after verified completed-sale data is available.

### Item names and duplicate values

- Exact normalized Supreme names are preferred.
- `aliases.json` contains reviewed manual aliases (alias → canonical Supreme name).
- Exact normalized names and reviewed aliases are supported. Automatic singular/plural and fuzzy name matching are disabled; ambiguous identities are rejected rather than guessed.
- A new unmatched name is reported through a cooldown-controlled maintenance alert and is rechecked. `python tools/suggest_aliases.py` compares BloxSwaps catalogue names with Supreme names and produces review-only candidates; it never writes suggestions automatically. Apply only a manually reviewed JSON mapping with `--approve reviewed-aliases.json`.
- If several Supreme source rows normalize to the same name, all numeric variants are retained. The scanner uses the **higher** value when you give an item and the **lower** value when you receive it, avoiding optimistic duplicate-name valuation.

Cash-first mode does not use a Supreme numeric value as a dollar proxy. An item must resolve to one exact, unambiguous Supreme catalogue identity and must have a manually verified USD cash price; missing cash prices are never treated as zero. Thus an item with no numeric Supreme value can only participate if its identity is present in Supreme and its cash price has been independently verified.

## Data sources and pagination

The scanner currently reads `value_source_b` and `stability` from RBLXValue API v2; it does not scrape Supreme Values' website. RBLXValue describes itself as an independent platform that indexes third-party sources including Supreme Values, and its API exposes `value_source_b` as the Supreme source. However, RBLXValue's terms do not grant ownership of third-party data, while [Supreme Values' Terms of Service](https://supremevalues.com/tos) expressly prohibit scraping, manual input, or copying its value-list data into applications, websites, and bots without authorization. Therefore, continued use of Supreme-derived data in this bot/dashboard must be covered by written permission from Supreme Values or a clear redistribution authorization from the data provider. Do not add a direct Supreme collector or a manual Supreme-data editor without that permission. An editor for independently sourced, user-owned values is a separate option. The bot's Telegram messages and public dashboard display the required `Data from rblxvalue.com` attribution for free-plan API usage; `value_source_a` is secondary information only and is never blended into Supreme totals.

- RBLXValue documents that its source dataset is refreshed every **12 hours**. The scanner therefore does not waste API calls re-downloading unchanged pages every few minutes. Instead, it polls the documented `/v2/meta` endpoint once per hour (24 checks/day), compares `last_updated` and the catalogue item count with the cached snapshot, and immediately refreshes all paginated item data when either changes. The full catalogue is checked for a consistent source version before and after pagination; a changing version or row-count mismatch rejects the snapshot. This gives a detection delay of up to about 1 hour **after RBLXValue publishes its new snapshot**; it cannot detect a Supreme website edit before RBLXValue itself ingests it. The expected request use is about 24 metadata checks + up to 22 item-page requests for two 12-hour source updates + 4 credential checks = about 50 of the configured 60 daily requests. At cache expiry, the scanner conditionally revalidates each cached API page with its HTTP `ETag` (`If-None-Match`); unchanged pages are reused only after a matching `304 Not Modified`. Pages without a stored ETag are fetched normally. If refresh/revalidation fails, a 304 has no matching cached page, or the API key is missing, it fails closed rather than using stale values. While that snapshot is fresh, the scanner performs a one-item credential health check every 6 hours (and immediately after a key rotation). Successful checks store only a truncated SHA-256 key fingerprint and timestamps in the private `state` branch. Temporary network/CDN failures are marked inconclusive and do not discard a still-fresh snapshot; the next check is throttled for 3 hours. Explicit credential rejection opens the existing circuit breaker and triggers a Telegram alert. All attempts use the same persistent rolling 60-request budget.
- The site item catalogue is loaded from `https://api.bloxswaps.com/items` and cached for **1 hour**.
- The first BloxSwaps page is read from its HTML/RSC payload, then every new RSC post is converted through the site's item catalogue—even if its HTML card could not be parsed. Parsed HTML is cross-checked for quantity and contributes visible-price/raw-text diagnostics. Further pages are fetched from `https://api.bloxswaps.com/trade-posts` with `cursor` and `pageSize=20`.
- The hot path scans new/refreshed posts until 25 consecutive seen posts establish the feed boundary; pinned posts do not advance that streak. Old pending/watch IDs cannot hold this boundary open. Only every fourth scan cycle runs a bounded backlog pass: at most 50 due pending IDs and up to 25 pages beyond the new-feed boundary. Unresolved IDs remain queued when the lookahead cap is reached. The 1,000-page safety cap still protects cold starts and feeds without a trusted success checkpoint.
- RSC and HTML items are matched by catalogue ID or unique normalized name, never by list position. If identity cannot be established uniquely, quantity becomes `uncertain`. A newly observed `itemDetailsId` triggers one forced catalogue refresh, rate-limited to once per minute; unknown IDs are never assigned guessed names or values.
- Cloudflare/CAPTCHA challenge pages and HTTP 403 access denials are reported explicitly rather than treated as an empty feed.

## Development setup

The scanner's core runtime uses Python 3.12 and the standard library; it has no mandatory third-party runtime dependencies. Optional tooling is pinned in small, purpose-specific manifests:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
ruff check --select F .
```

The official read-only Discord collector is optional and is installed only when its bot token is configured:

```bash
python -m pip install -r requirements-discord.txt
```

Ruff settings live in `pyproject.toml`. CI runs the full unittest suite twice, verifies that `data/` is unchanged, and runs the repository-wide correctness lint.

## Documentation map

- [Operations and deployment runbook](docs/OPERATIONS.md) — secrets, workflows, persistence, monitoring, Discord imports, and dashboard operations.
- [Market Intelligence architecture](MARKET_INTELLIGENCE.md) — source permissions, provenance, and optional research integrations.
- [Engineering history](REPORT.md) — historical implementation phases and their original evidence.


