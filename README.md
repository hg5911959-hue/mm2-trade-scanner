# MM2 Trade Scanner

## Important: public state-storage and data-rights warning

This repository is public, so **every branch, including the legacy `state` branch, is publicly readable**. The current legacy branch contains plaintext `supreme_values.json`, `supreme_values_audit.json`, and `market_history.sqlite3`; the database schema stores Supreme-derived values. The code now supports moving runtime state into a dedicated private repository, but that protection is not active until the private repository and `MM2_STATE_REPO_TOKEN` secret are configured and a successful migration is verified. Follow [Private state setup](docs/PRIVATE_STATE_SETUP.md). Do not delete the legacy state files before the private copy is verified.

The repository's own Supreme Values compliance notes state that written permission is required before using Supreme-derived value-list data in an application or bot. I found no authorization reference in the repository files reviewed for this audit. The production workflow now fails closed unless `SUPREME_DATA_AUTHORIZED=true` is configured; set it only after obtaining written permission. Attribution alone is not permission. Removing the public branch later cannot undo copies already made by third parties.


Automated scanner for public **BloxSwaps MM2 P2P listings**, with concise Telegram alerts. GitHub Actions schedules a scan every 10 minutes; each scan polls the feed every 30 seconds for up to 540 seconds (9 minutes), leaving a short persistence window. GitHub cron can be delayed, so this is a target cadence, not a real-time guarantee.

## Production mode and alert eligibility

The production scanner workflow currently runs with `CASH_FILTER: "true"`. The weekly health report intentionally omits the cash-price summary (`CASH_FILTER: "false"`); this does not change the scanner's production mode. Do not infer the live scanner mode from the weekly-report setting.

Production cash alerts require a complete, fresh ROBASE snapshot (222 catalogue entries) and verified item prices. The workflow refreshes the public ROBASE completed-sales catalogue before scanning, accepts only a still-fresh cached snapshot if refresh fails, and requires the snapshot and each item's last check to be no older than 20 minutes; `CASH_MAX_AGE_DAYS=1` is an additional upper bound, not a relaxation of that stricter gate. The preflight requires at least eight verified rows. A listing is eligible only when all items needed for the calculation can be priced safely and the conservative cash-profit threshold is met.

Key production thresholds configured in `.github/workflows/scan.yml`:
- `CASH_MAX_AGE_DAYS=1`
- `CASH_MIN_VERIFIED_ROWS=8`
- `CASH_MIN_PROFIT_USD=1.00` (the small-offer threshold is also $1.00)
- `CASH_MIN_MARGIN_PERCENT=0`
- `MIN_PROFIT_PERCENT=5`

The scheduled scanner runs every 10 minutes as a GitHub Actions target cadence, not a real-time guarantee. Each scan polls for up to nine minutes; GitHub may delay scheduled workflows. Manual scheduler dispatch defaults to dry-run.

The tracked `data/cash_prices.csv` and its verifier are legacy/manual-catalogue support. They are not the production ROBASE price snapshot source. The weekly report remains separately configured and does not enable cash pricing by changing scanner settings.

## Trade evaluation

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** or a structured **Offre / Offres / Offer / Offers** requested wildcard is excluded before valuation. The filter reads the individual card and structured `requestedWildcards` metadata; it does not discard a concrete weapon merely because the free-text description contains the word “offers”.
- The current ROBASE snapshot contains **222 catalogue entries**: the identity audit classifies **214 weapons** and **8 non-weapons**. Non-weapons and weapons without an exact ROBASE/RBLXValue identity crosswalk are not valued or alerted.
- Supreme Values is used for game-value comparison and item identity/stability validation. Items that cannot be matched or valued remain excluded; uncertain listings retain the existing manual-review/recheck protections.
- Production cash-first mode uses the refreshed ROBASE completed-sales snapshot, with conservative profit bounds. The tracked `data/cash_prices.csv` is a legacy/manual catalogue retained for compatibility; it is not the production scanner's price source. Supreme numeric values are never converted into cash prices.
- **Value mode:** any received item with missing/unrecognized Stability or a blocked status (Decreasing, Receding, Unsafe, Untradeable, Unstable, Volatile, Fluctuating) is excluded. **Cash-first mode:** verified ROBASE cash prices determine profit; Stability labels are informational and do not veto a cash-positive offer. An explicit `Untradeable` label still blocks an item on either side. Stability is sourced from RBLXValue, not directly verified against the Supreme website.
- **Estimate-only dry run:** when `DRY_RUN=true`, the scanner may use unverified legacy references for exploratory calculations, applying a ±30% uncertainty band and the resale haircut. Every dry-run cash result is labelled as estimate-only and is hard-blocked from the live alert eligibility gate. This uncertainty band is an engineering assumption, not a statistically measured confidence interval or proof of current market prices. Normal production runs continue to reject unverified rows.
- The scanner never uses BloxSwaps' own Win/Loss label, never uses the visible listing price as an item value, and never invents a missing item value or quantity.
- In **value mode**, a listing is excluded if any item on the received (`Propose`) side has a risky RBLXValue stability label: `Decreasing`, `Receding`, `Unsafe`, `Untradeable`, `Unstable`, or `Volatile`; missing/unrecognized received-side Stability is held back. In **cash-first mode**, these market-quality labels are displayed for context but do not override profit calculated from verified ROBASE seller-proceeds prices. Explicit `Untradeable` remains excluded on either side. Telegram labels the field `Stability (RBLXValue)` to avoid implying direct verification against the Supreme website.

### Legacy manual CSV catalogue (not used by production)

The following rules apply only to the legacy `data/cash_prices.csv` and its offline verification tool. This CSV is not read by the production scanner, does not update ROBASE prices, and does not affect live alert eligibility. Production uses the complete, fresh ROBASE snapshot described above. Supreme numeric values remain separate from all cash prices.

To review the legacy CSV for an offline experiment:

1. Paste a marketplace list into a UTF-8 text file, one item per line.
2. Preview with `python tools/verify_cash_prices.py --input prices.txt --dry-run`, then run without `--dry-run` after reviewing skipped names. Large price changes are refused by default; use `--force` only after manual review.
3. Treat the result as a manual reference only; it does not change the production ROBASE snapshot.

The verifier matches exact normalized names first and only then tries removing a trailing `knife`, `gun`, or `sword`. Unknown and ambiguous names are skipped. It never creates or deletes catalogue entries, never divides a multi-quantity total into a guessed unit price, and writes atomically. The tracked CSV is visible to repository readers. The production workflows no longer consume a `CASH_PRICES_CSV` secret.

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

In production cash-first mode, Telegram cash alerts show owner/listing links, every item, verified ROBASE USD price, the Supreme value transported through the authenticated RBLXValue API, and `Stability (RBLXValue)`. They do not display the blended RBLXValue estimate, MM2Values, Demand, or Rarity as competing valuation lines. Supreme values and Stability are informational in cash mode; the totals and conservative USD cash profit are calculated only from verified ROBASE prices. An explicit `Untradeable` status still blocks the listing. Uncertain quantities are rejected; no alert is sent unless the worst-case cash profit is at least $1.00. Parser provenance, raw excerpts, and HTML/API diagnostics stay in GitHub Actions logs.

### Legacy manual cash-price catalogue

`data/cash_prices.csv` is retained for compatibility and offline/manual verification only. Editing it does not update the production ROBASE snapshot or change production alert eligibility. The live scanner refreshes `data/robase_prices.json` from ROBASE before scanning and uses only fresh, verified item prices. If you maintain the legacy CSV for a separate experiment, keep names canonical, mark unverified estimates as `unverified`, and never treat historical rows as current market facts.

The default resale haircut is controlled by CASH_SELL_HAIRCUT_PCT (15%), the minimum net cash profit by CASH_MIN_PROFIT_USD ($1.00), and the minimum cash ROI by CASH_MIN_MARGIN_PERCENT (0% by default). These are conservative starting thresholds and should be calibrated after verified completed-sale data is available.

### Item names and duplicate values

- Exact normalized Supreme names are preferred.
- `aliases.json` contains reviewed manual aliases (alias → canonical Supreme name).
- Exact normalized names and reviewed aliases are supported. Automatic singular/plural and fuzzy name matching are disabled; ambiguous identities are rejected rather than guessed.
- A new unmatched name is reported through a cooldown-controlled maintenance alert and is rechecked. `python tools/suggest_aliases.py` compares BloxSwaps catalogue names with Supreme names and produces review-only candidates; it never writes suggestions automatically. Apply only a manually reviewed JSON mapping with `--approve reviewed-aliases.json`.
- If several Supreme source rows normalize to the same name, all numeric variants are retained. The scanner uses the **higher** value when you give an item and the **lower** value when you receive it, avoiding optimistic duplicate-name valuation.

Cash-first mode does not use a Supreme numeric value as a dollar proxy. An item must resolve to one exact, unambiguous Supreme catalogue identity and must have a manually verified USD cash price; missing cash prices are never treated as zero. Thus an item with no numeric Supreme value can only participate if its identity is present in Supreme and its cash price has been independently verified.

## Data sources and pagination

The scanner reads RBLXValue API v2 and preserves its published blended `value` separately from `value_source_a` (MM2Values) and `value_source_b` (Supreme Values). The source-specific Supreme value remains the value-mode comparison field. Telegram cash alerts display the Supreme value and `Stability (RBLXValue)` as informational context; cash profit is calculated only from verified ROBASE USD prices. The scanner does not scrape Supreme Values' website. RBLXValue describes itself as an independent platform that indexes third-party sources including Supreme Values. However, RBLXValue's terms do not grant ownership of third-party data, while [Supreme Values' Terms of Service](https://supremevalues.com/tos) expressly prohibit scraping, manual input, or copying its value-list data into applications, websites, and bots without authorization. Therefore, continued use of Supreme-derived data in this bot/dashboard must be covered by written permission from Supreme Values or a clear redistribution authorization from the data provider. Do not add a direct Supreme collector or a manual Supreme-data editor without that permission. An editor for independently sourced, user-owned values is a separate option. The bot's Telegram messages and public dashboard display the required `Data from rblxvalue.com` attribution for free-plan API usage.

- RBLXValue documents that its source dataset is refreshed every **12 hours** and that all v2 data endpoints, including `/v2/meta`, require the API key. The scanner therefore polls authenticated `/v2/meta` every two hours through the same guarded request client and persistent rolling 60-request budget. This is still six times more frequent than the documented 12-hour source refresh cadence while leaving a safer API reserve. It compares `last_updated` and the catalogue item count with the cached snapshot, and refreshes all paginated item data immediately when the source version changes or when the 12-hour TTL expires. Failed metadata attempts are persisted and throttled for two hours so the 30-second scan loop cannot repeatedly spend API budget; forced post-pagination version checks bypass only a recent successful poll, never a recent failure. The full catalogue is checked for a consistent source version before and after pagination; a changing version or row-count mismatch rejects the snapshot. Expected keyed request use is up to 12 two-hour metadata checks + 22 item-page requests for two 12-hour source updates + 2 post-pagination version checks + 4 credential checks + up to 6 bounded detail enrichments = about 46 of the configured 60 daily requests, leaving 14 requests for retries and exceptional refreshes. At cache expiry, the scanner conditionally revalidates each cached API page with its HTTP `ETag` (`If-None-Match`); unchanged pages are reused only after a matching `304 Not Modified`. Pages without a stored ETag are fetched normally. If refresh/revalidation fails, a 304 has no matching cached page, or the API key is missing, it fails closed rather than using stale values. While that snapshot is fresh, the scanner performs a one-item credential health check every 6 hours (and immediately after a key rotation). Successful checks store only a truncated SHA-256 key fingerprint and timestamps in `rblxvalue_key_health.json`. The source repository remains public for GitHub Pages, so the runtime `state` branch must be moved to the dedicated private repository described in [Private state setup](docs/PRIVATE_STATE_SETUP.md). The workflow supports one-time migration and private persistence when the `MM2_STATE_REPO_TOKEN` Actions secret is configured; until that migration is verified and the legacy public `state` branch is removed, the existing cache and SQLite data remain publicly readable. Temporary network/CDN failures are marked inconclusive and do not discard a still-fresh snapshot; the next check is throttled for 3 hours. Explicit credential rejection opens the existing circuit breaker and triggers a Telegram alert. All keyed attempts use the same persistent rolling 60-request budget.
- The site item catalogue is loaded from `https://api.bloxswaps.com/items` and cached for **1 hour**.
- The first BloxSwaps page is read from its HTML/RSC payload, then every new RSC post is converted through the site's item catalogue—even if its HTML card could not be parsed. Parsed HTML is cross-checked for quantity and contributes visible-price/raw-text diagnostics. Further pages are fetched from `https://api.bloxswaps.com/trade-posts` with `cursor` and `pageSize=20`.
- The hot path scans new/refreshed posts until 25 consecutive seen posts establish the feed boundary; pinned posts do not advance that streak. Old pending/watch IDs cannot hold this boundary open. The hot feed is checked every cycle. The deeper backlog pass runs every fourth cycle (up to 50 due IDs and a 1,000-page absolute ceiling), and runs earlier when Robase cash prices or a verified value snapshot genuinely change. It stops as soon as the selected pending/watch IDs are found; unresolved IDs remain queued when the accessible feed history is exhausted.
- RSC and HTML items are matched by catalogue ID or unique normalized name, never by list position. If identity cannot be established uniquely, quantity becomes `uncertain`. A newly observed `itemDetailsId` triggers one forced catalogue refresh, rate-limited to once per minute; unknown IDs are never assigned guessed names or values.
- Cloudflare/CAPTCHA challenge pages and HTTP 403 access denials are reported explicitly rather than treated as an empty feed.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key from the RBLXValue developer dashboard (raw key only, no quotes).
- `MM2_STATE_REPO_TOKEN`: optional during compatibility mode, but required for the final private-state deployment. Use a fine-grained token limited to the private `hg5911959-hue/mm2-trade-scanner-state` repository with Contents read/write. See [Private state setup](docs/PRIVATE_STATE_SETUP.md).
- `SUPREME_DATA_AUTHORIZED`: set to the exact value `true` only after obtaining written permission from Supreme Values for this bot/application use. The production scan now fails closed if this authorization flag is absent or false.

Never commit or share these secrets. The scanner does not print API keys or Telegram bot tokens in logs.

### RBLXValue free-plan compliance and key safety

- The scanner displays the exact `Data from rblxvalue.com` credit in every Telegram message and on the public dashboard. It is shown unconditionally, so the project remains compliant if the API response requires free-plan attribution.
- RBLXValue documents one free API key per account. Do not create alternate accounts or spread requests across keys to bypass limits. If a key is compromised or needs replacement, rotate the existing key through the official developer dashboard and update the `RBLXVALUE_API_KEY` repository secret.
- HTTP 429 is a rate-limit response, not a key suspension; the scanner respects retry guidance and its own persistent rolling request budget. HTTP 401 indicates a missing/wrong/rotated key; HTTP 403 may indicate a key that is not activated, expired, or suspended.
- Preflight reads the local rolling 24-hour request ledger and sends a separate Telegram warning at 80% of the configured limit (48/60 by default). This check is local-only and does not spend an RBLXValue request; the hard limit remains enforced by the shared request guard.
- The scanner never stores the API key itself in repository state. The key-health file stores only a truncated SHA-256 fingerprint and timestamps for health checks and circuit-breaker recovery. The legacy public `state` branch remains a privacy blocker until the private-state migration is verified; see [Private state setup](docs/PRIVATE_STATE_SETUP.md).

- `data/supreme_values_audit.json` records the complete internal API snapshot with completeness counts, missing metadata and same-name identity collisions. It is retained for scanner diagnostics only; do not export or republish the Supreme-derived value list. Missing or unrecognized received-side Stability blocks value-mode offers, but does not block a cash-first offer whose identity, quantities and ROBASE prices are verified. Demand and non-tradability Stability labels are informational in cash-first mode; explicit `Untradeable` is always blocked.
- The RBLXValue API explicitly documents `value_source_b` as the Supreme Values numeric value, but documents `stability` as a general API field rather than a source-specific field. Telegram therefore labels it `Stability (RBLXValue)`; this is not a claim of direct Supreme website verification. Its `/v2/history/{slug}?source=supremevalues` endpoint can be used for source-specific historical value movement where API budget permits.
- Up to six records with missing type, or numeric-valued weapons missing Stability/Demand/Rarity, are checked per UTC day through the documented `GET /v2/items/{slug}` endpoint. The response is accepted only when ID/slug match the catalogue record; successful details fill missing metadata without overwriting known values. Attempts are persisted and unchanged records are not queried repeatedly. This keeps enrichment within the 60-request/day safety budget.
- **Manual Supreme-data editing is intentionally not enabled.** Supreme Values' Terms of Service explicitly prohibit scraping, manually inputting, or copying its value-list data into apps, websites and bots without authorization. We can build an editor for independently sourced, user-owned valuations or corrections, but not a tool for manually copying Supreme's published values unless written permission is obtained.


## Runtime, deduplication, and persistence

- **Trigger cadence:** GitHub cron starts the scanner every 10 minutes. Manual `workflow_dispatch` and `repository_dispatch` event type `mm2-scan` remain available.
- **Polling inside each job:** one process runs `scan_once()` every 30 seconds (configurable within 30–60 seconds) for 540 seconds (9 minutes). This leaves an approximately 1-minute scheduled gap; GitHub may delay scheduled runs, so this reduces but cannot eliminate missed-post risk. It handles SIGTERM/SIGINT by completing the current scan cycle before exiting.
- **Concurrency:** `cancel-in-progress: false`; a running scan is not cancelled while it is sending Telegram messages or writing state. GitHub keeps at most one pending run for the concurrency group.
- **Alert budget:** qualifying offers are ranked by conservative profit; the top 15 are sent per scan by default, and remaining offers stay eligible for the next scan.
- **Detection timing:** the scanner logs first-observed-to-alert latency for every sent trade. The 30-second poll interval is designed to keep detection near 30 seconds when a post appears between cycles; GitHub scheduling and remote API delays are outside that guarantee.
- **Cold start:** if durable seen state is missing, the scanner paginates the feed rather than baselining only the first page. It evaluates posts verifiably refreshed within the last 60 minutes and seeds older or undated posts as historical baseline. It stops after 25 consecutive timestamped posts older than the cutoff; pinned and undated posts do not advance that boundary. Reaching the 1,000-page cap leaves pagination incomplete and triggers maintenance.
- **Watch window:** the latest 6 hours or 300 watched posts (whichever is smaller) are retained. Non-profitable posts remain eligible for re-evaluation, and pagination searches for watched IDs before stopping at the seen boundary. A changed item/quantity, Stability, or verified cash-price fingerprint causes the same ad to be evaluated again; a newly profitable version can alert again without replaying an unchanged alert. Supreme numeric value changes alone do not change the cash ranking.
- **State compatibility:** existing `alerted.json`, seen-ad, pending-recheck, and uncertain-Telegram state formats are retained and migrated in place. New watch-window and last-success checkpoints are additive. A corrupt or unreadable state file fails closed instead of silently resetting deduplication.
- **Durable state source:** the `state` branch is the sole durable source. The scan job has `contents: read`; it restores the branch and uploads a state artifact. A separate `persist-state` job has `contents: write` and updates only the state branch. The legacy Actions Cache restore path has been removed.
- **State checkpoints:** state files are written atomically after each scan cycle; the final state artifact is committed after the polling job. The 2.5-minute timeout margin allows artifact upload to finish.
- **Telegram delivery:** a definite Telegram API rejection clears its pre-send guard so the offer can be retried. A timeout, connection failure, invalid response, or HTTP 5xx is treated as **uncertain delivery**: the listing is quarantined and is not automatically resent. Messages to the same chat are spaced by 1.1 seconds to respect Telegram's per-chat rate limit. An explicit 429 is retried once after `retry_after`; if it is still rejected, the listing is queued for a later scan instead of blocking the polling session with repeated long waits. Check Telegram before manually clearing its entry in the uncertain state file.
- **Maintenance alerts:** malformed/unreadable cards, missing RSC data, incomplete pagination, unmatched item names, unresolved pending ads after a complete pagination pass, and expired Telegram quarantines use persistent cooldowns. They are not gated by a loop-mode environment flag. Missing RSC data is a scan error; HTML-only partial data is never trusted.
- **Watchdog:** runs every 5 minutes, suppresses stale-success alerts while a scanner job is active, and raises a warning after 20 minutes without a successful scanner run. The repository name is read from `GITHUB_REPOSITORY`.
- **Action security:** GitHub Actions dependencies are pinned to verified immutable commit SHAs with release comments. Dependabot checks GitHub Actions weekly. The scanner job does not have repository-content write permission; only the state-persistence job can write contents.
- **External monitoring:** optionally configure Healthchecks.io and store its ping URL as `HEALTHCHECKS_PING_URL`. The workflow pings on completion and reports failed runs.

## Item identity and Supreme-value safety

- BloxSwaps catalogue metadata (slug, rarity, weapon type, and chroma flag) is carried with each item ID. A missing catalogue ID is never rescued by a similarly named weapon.
- Supreme records retain their own source slug, category, type, ID, scalar value, explicit numeric range, and published stability label. In cash-first mode, Supreme numeric values and non-tradability Stability labels are not used in profit arithmetic or alert gating; verified ROBASE prices determine cash profit, while explicit `Untradeable` labels remain blocked. In value mode, received-side Stability remains a safety gate. The cache schema is versioned so an older snapshot without stability is refreshed before it can be used. Same-display-name records are never merged into a synthetic high/low range.
- Resolution uses exact source slug, exact normalized name plus catalogue identity, or a unique base-name match constrained by rarity and type (for catalogue labels such as Latte). Reviewed aliases are allowed; automatic fuzzy and plural matching are disabled.
- If more than one Supreme identity remains possible, the item is rejected. A missing Supreme numeric value alone does not block cash evaluation when the identity is unique and a verified USD price exists.
- RBLXValue encodes some Common/Uncommon/Rare xN T1-item notation as a small integer. Values below 10 in those categories are treated as tier-item counts, not ordinary Supreme value units. Higher numeric exceptions remain eligible.
- Give/receive bounds use only the selected item's own published range. When the API has no range, the scalar value_source_b is used identically on both sides; another item with the same name is never used as a range.

## Workflow hardening and external scheduling

- **Action integrity:** every third-party GitHub Action is pinned to a verified full commit SHA with a trailing release-version comment. Dependabot checks `github-actions` weekly.
- **Test isolation:** `tests.yml` runs `python -m unittest discover -s tests` on pushes to `main` and on pull requests. The production scan job no longer runs tests, so a test failure cannot prevent a scheduled scan. The old `source-diagnostics.yml` workflow has been removed.
- **Global log redaction:** `scanner.py` redacts the exact configured Telegram bot token, Telegram chat ID, and RBLXValue API key (only when at least six characters), plus Telegram bot URLs, `rbx_` keys, and Bearer credentials. Both stdout and stderr are filtered before output reaches `scanner.log` or the public Actions artifact.
- **Durable state replacement:** when scanner data changes, `persist-state` compares staged `data/` with `origin/state` and skips an unchanged tree. Otherwise it creates `state-new` as an orphan branch and force-pushes it to `state`, leaving a single-commit state history. Empty or missing state artifacts are ignored so they cannot erase durable data. The workflow-level concurrency group covers both scan and persistence jobs, preventing overlapping state writes.
- **Faster coverage:** the scanner cron runs every 10 minutes; each job polls for 540 seconds at 30-second intervals and has a 15-minute timeout. Every scan cycle checks the hot feed; the deep backlog pass runs every fourth cycle or immediately after a genuine price/snapshot change (50 due IDs, with a 1,000-page absolute lookahead ceiling). The watchdog default stale threshold is 20 minutes.
- **Optional cron-job.org trigger:** configure a scheduled HTTP POST to `https://api.github.com/repos/hg5911959-hue/mm2-trade-scanner/dispatches` with JSON body `{"event_type":"mm2-scan"}` and GitHub's JSON accept/content headers. Use a fine-grained personal access token limited to this repository with the minimum `Contents: read and write` permission required for repository dispatch. Store the token only in cron-job.org's secret configuration; never put it in the repository, workflow YAML, URL, or logs. GitHub Actions cron remains the primary schedule.

## Logs and manual operations

- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- External scheduler fallback: cron-job.org can call GitHub's repository-dispatch API with `{"event_type":"mm2-scan"}` to trigger the existing `repository_dispatch` workflow. Create a fine-grained personal access token limited to this repository, grant the minimum repository permission required for dispatch (`Contents: read and write`), and store it only in cron-job.org's secret configuration. Never put it in the repository, workflow YAML, URL, logs, or chat. GitHub cron remains the primary schedule.
- `history.csv` records detected opportunities and calculated values; it cannot confirm that a Roblox trade was accepted or completed.

## Limitations and safety

- BloxSwaps' public RSC/API contract and HTML markup can change. The live fixture and regression tests protect known layouts, not every future layout.
- The scanner uses a maximum of 1,000 cursor pages per scan. If there are more than 1,000 new posts before a seen boundary, the maintenance warning indicates that the scan was capped.
- GitHub cron starts can be delayed. A 10-minute schedule can miss short-lived offers; it cannot guarantee detection before another trader accepts them.
- Supreme Values are estimates delivered by a third-party provider. A fresh RBLXValue response does not guarantee Supreme itself has just changed its upstream data.
- No Roblox password, browser cookie, or account session token is needed.

## Optional Market Intelligence extensions

See [MARKET_INTELLIGENCE.md](MARKET_INTELLIGENCE.md) for the standalone Discord/RSS import and advisory AI utilities. They are **not invoked by the scheduled production scanner** and do not supply prices or approve trades.

The one-time 2026-11-21 weapon identity review inputs are retained under [docs/archive/weapon-review-2026-11-21](docs/archive/weapon-review-2026-11-21/README.md). They are historical review artifacts, not live prices or production configuration.


## SQLite market history and public dashboard

The scanner keeps a local SQLite history at `data/market_history.sqlite3`. The existing `state` branch workflow restores and persists this database alongside the Supreme cache, request budget, and other durable state.

The database stores:
- Deduplicated Supreme/secondary-source value snapshots (only when the value set changes).
- Public BloxSwap listing observations, first-seen/last-seen timestamps, and item quantities.
- Qualified opportunity records, including totals, profit, margin, and listing links.
- A source-agreement label for display only: `cross_checked` when both sources are within 15%, `conflict` when they differ more, and `estimated` when only one numeric source is available. Cash-first trade calculations use verified manual USD prices only; Supreme numeric values and the secondary source are never blended into cash profit.

Retention is 90 days for listing observations and 180 days for value snapshots/opportunities. SQLite writes are additive and idempotent; history storage failures are logged without changing the existing trade-evaluation or Fail-Closed rules.

**Privacy:** private Discord messages, listings, and opportunities are deliberately excluded from this database and from the public dashboard. The state branch belongs to the public repository, so private-server content must never be written there.

### GitHub Pages dashboard

The static dashboard is in `docs/index.html`. After each successful **MM2 Trade Scanner** workflow, `.github/workflows/dashboard-pages.yml` restores the SQLite database from `state`, exports a public-safe `dashboard-data.json`, and deploys it with GitHub Pages. It shows a searchable ROBASE catalogue with verified USD prices, 7-day/30-day completed sales, BloxSwaps copies/holders where published, recent public listings, and cash-mode USD opportunities only. Supreme-derived value lists and histories are not included in the public export unless redistribution permission is explicitly obtained. If GitHub Pages is disabled and the workflow token receives HTTP 403, a repository administrator must enable it once at **Settings → Pages → Build and deployment → Source: GitHub Actions**; until then, the workflow still builds and uploads the dashboard artifact.

One-time repository setting required:
1. Open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Save. The next successful scanner run will publish the dashboard. You can also run **Actions → Publish MM2 Market Dashboard → Run workflow** manually.

The dashboard is static and uses no paid API, external analytics, or third-party JavaScript. It never includes API keys, Telegram credentials, or private Discord records.


## Discord trade-message ingestion

The scanner includes a conservative parser for messages collected from the two configured MM2 Discord servers. It accepts only explicit two-sided trade statements, normalizes offered/requested items into the scanner schema, aggregates repeated item quantities, and rejects ambiguous or malformed messages. Discord message IDs are namespaced so they cannot collide with BloxSwaps IDs or be sent into BloxSwaps cursor/pending queries.

Discord integration requires an official bot that is a member of the source server with read-only channel/history permissions and Message Content Intent. If the bot cannot be added, direct collection remains inactive. A private/manual import is supported through the GitHub Actions secrets `DISCORD_MANUAL_MESSAGES_JSON` and `DISCORD_MANUAL_MESSAGES_JSON_2`: add it under **Settings → Secrets and variables → Actions** as a JSON object containing a `messages` array (up to 2,000 rows). Example shape:

```json
{
  "messages": [
    {
      "message_id": "123456789012345",
      "guild_id": "1125462206151143464",
      "channel_id": "123456789012345",
      "author": "Trader",
      "content": "Trading: 2x Icebreaker | Looking for: Candy",
      "url": "https://discord.com/channels/1125462206151143464/123456789012345/123456789012345",
      "created_at_utc": "2026-11-13T12:00:00+00:00"
    }
  ]
}
```

The secret is merged into the temporary collector file for that run; its contents are not printed or written to the public state branch. The parser then validates each message and rejects ambiguous trades.

**Private-data boundary:** raw Discord message JSON is processed only inside the scan job and is removed from the state artifact before persistence. Discord offers and opportunities are also excluded from the public SQLite history, public CSV history, and GitHub Pages dashboard. The bot does not send messages, moderate, or use a user token/self-bot.


## Reliability controls: breaker, alert cap and dry-run

- RBLXValue credential rejections are tracked by the SHA-256 key fingerprint only. Consecutive 401/credential-specific 403 responses escalate the lockout (6h, 12h, then permanent after the third consecutive rejection); a successful API response or key rotation resets the counter. Cloudflare/CAPTCHA challenge 403 responses are not classified as invalid credentials. After three rejections, replace the key or contact RBLXValue; the scanner makes no more requests using that fingerprint.
- `MAX_ALERTS_PER_SCAN` defaults to 15 in production. In cash mode, the scanner ranks qualifying offers by conservative USD cash profit; in value mode it ranks by game-value profit. It sends the best 15 and leaves the remainder eligible for the next scan. A single Telegram notice reports how many offers were deferred. Set it to `0` only when unlimited alert volume is explicitly desired.
- Use **Actions → MM2 Trade Scanner → Run workflow → dry_run=true** to evaluate offers without sending Telegram messages or writing alerted/delivery state. The log prints only listing IDs and the profit for the active mode (game value or cash USD). The default is false. Dry-run suppresses maintenance and failure notifications too.
- Global log redaction includes Discord bot credentials and both manual-import secrets. Never paste a Discord bot token or raw message JSON into an issue, commit, or log.

## Manual Discord import: two-secret workflow

You can combine two JSON imports without a bot. Create or update repository secrets named `DISCORD_MANUAL_MESSAGES_JSON` and `DISCORD_MANUAL_MESSAGES_JSON_2`. Each secret must be a JSON object with a `messages` array; use the row schema shown above. The workflow independently validates both secrets, so one malformed secret does not discard the other. Rows older than seven days are ignored, IDs are deduplicated, and the merged import is capped at 2,000 rows. Only counts and freshness metadata are logged; raw message bodies are removed before the state artifact is uploaded and never enter the public state branch.

Before updating a secret, validate the local JSON file:

```bash
python tools/check_discord_import.py discord-messages.json
```

The checker validates the schema and row cap and reports the file size. It warns above 45 KB to leave headroom below GitHub's 48 KB secret limit. Paste the JSON file contents into either secret in **Settings → Secrets and variables → Actions**, save, and run the scanner. Keep each secret below the size limit. The workflow records only the newest-message timestamp and a stale flag; if the newest imported message is older than 48 hours, it logs `[discord-manual] import is stale` and includes that status in the weekly report without sending a per-scan alert.

## Daily metrics and weekly health report

- `data/metrics.json` stores UTC daily counters: scans, scan failures, sent/deferred alerts, RBLXValue requests, breaker duration and empty parses. It is written atomically and retains the latest 14 days. It is optional during state validation for compatibility with existing state branches.
- **MM2 Weekly Health Report** runs every Sunday at 08:00 UTC or manually from Actions. It reads the `state` branch and sends scan success percentage, alert/deferred totals, today's API request usage against the daily limit, key state, Discord manual-import freshness and a seven-day alert trend.
- The weekly workflow uses only the existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets and grants `contents: read`. No paid service or new secret is required.

## Scheduled scanning and pending rechecks

- `.github/workflows/scan-scheduler.yml` owns the 10-minute cron and calls the reusable `scan.yml` workflow. `scan.yml` remains available for manual `workflow_dispatch` runs and reusable `workflow_call` execution. The shared concurrency group prevents overlapping scanner sessions.
- Retryable technical BloxSwaps listing IDs remain in `data/pending_recheck.json` with their original first-seen/expiry timestamps. A separate `data/pending_recheck_schedule.json` controls retry timing: legacy/unclassified items retain the conservative 5 minutes, 15 minutes, 30 minutes, 1 hour, then 6 hours schedule; definite Telegram send failures retry after 30 seconds, 60 seconds, 2 minutes, then 5 minutes; unparsed feed cards retry after 1 minute, 2 minutes, 5 minutes, then 10 minutes. These shorter schedules prevent technical failures from leaving potentially eligible offers dormant for hours.
- Legacy pending state without a schedule is immediately eligible for one migration recheck. The schedule file is optional for older state branches and is validated before persistence.

- `.github/workflows/watchdog-scheduler.yml` owns the five-minute watchdog cron and invokes the reusable `watchdog.yml` workflow. The watchdog checks both queued and running scans and reports stale successful-scan timestamps; its alert reminder window is 30 minutes.

## Dashboard publication

The dashboard workflow checks GitHub Pages before deployment. If the Pages site does not exist, it attempts to initialize a workflow-based Pages site using the job-scoped `pages: write` token permission. If GitHub rejects site initialization because repository-administrator access is required, the build artifact is still retained and deployment is safely skipped with a warning.

- The scanner scheduler also starts a scan after a successful `MM2 Scanner Tests` run on `main`. Failed test runs are ignored. This provides a post-deployment verification scan in addition to the 10-minute cron and manual dispatch.

- The watchdog also runs after every completed `MM2 Scanner Scheduler` workflow, regardless of success or failure, so a finished scan is checked immediately as well as by the five-minute cron.

- The scanner scheduler checks whether its own cron workflow or the watchdog cron workflow was disabled by GitHub. With the narrowly scoped `actions: write` token, it attempts to re-enable only those two known workflows; failure is non-fatal to the scan and is logged as a warning.

- Deep pending/watch backlog rechecks run every fourth scan cycle by default (`BACKLOG_RECHECK_EVERY_CYCLES=4`), with at most 50 IDs per pass (`MAX_PENDING_RECHECK_PER_SCAN`) and a 1,000-page absolute lookahead ceiling (`MAX_BACKLOG_RECHECK_PAGES`), stopping early when target IDs are found. A genuine Robase cash-price change or verified value-snapshot change triggers an immediate deep recheck. Fast cycles continue scanning the hot feed without repeatedly crawling historical pages. Definite Telegram send failures and unparsed feed cards retain priority retry reasons and use the shorter technical retry schedules above. Legacy deferred/manual-review entries are retired into the seen set rather than retried indefinitely; uncertain Telegram deliveries remain quarantined separately to prevent duplicate sends. IDs absent from the accessible public feed history remain queued for later attempts rather than being silently discarded.


## Cash blocker intelligence

The scanner records each listing held for a missing, unverified, or expired cash price in the UTC day's `cash_blockers` map in `data/metrics.json`. Each normalized item is counted once per listing per scan. Co-occurrence groups are retained alongside item totals so the estimated unlock percentage is calculated from actual blocked-listing combinations rather than double-counting listings. Metrics retain the latest 14 UTC days and are written atomically; state validation rejects malformed blocker data.

Run `python tools/next_to_verify.py` to see the 15 most frequent blockers, their current catalogue price and verification age, and the cumulative share of blocked listings that would become evaluable if the top N items were verified. The weekly Telegram report includes the top 10. It prints only the ranked subset, never the full catalogue.

Production cash-price routine:
1. The scheduled workflow refreshes the ROBASE completed-sales snapshot automatically before scanning.
2. Confirm the preflight passes and the snapshot is complete and no older than one day.
3. Run `python tools/next_to_verify.py` while the ROBASE snapshot is fresh to rank current cash-price blockers using the same catalogue as the scanner; duplicate item names are shown as ambiguous rather than assigned a potentially wrong price.

Supreme numeric values are never converted into cash prices; the USD catalogue remains a separate, manually verified source.


### Test isolation and workflow permissions

Every unittest case uses its own temporary working directory. Tests that need catalogue data use synthetic fixtures under `tests/fixtures`; no test reads or writes the repository's real `data/` directory. CI runs `python -m unittest discover -s tests` twice and checks `git status --porcelain data` after each pass. `tests/test_no_data_pollution.py` hashes every file in the real data directory before and after a mocked scan.

Scanner and tool path defaults are module constants or explicit function arguments so tests can patch them. Workflow permissions default to read-only; write access is scoped to state persistence, schedule enablement, repository keepalive, or Pages deployment. Third-party Actions are pinned to full 40-character commit SHAs, and workflow steps must never echo secrets.
