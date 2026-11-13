# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with concise Telegram alerts. GitHub Actions starts a job every 10 minutes; each job polls the feed every 30 seconds for 570 seconds (9.5 minutes), then exits cleanly before its 12-minute timeout.

## Trade evaluation

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded. The filter is applied to the individual card and also checks the structured `requestedWildcards` data for `ADD`.
- Profit = total Supreme Values of received items minus total Supreme Values of given items.
- The scheduled workflow uses a minimum absolute difference of **0** and minimum percentage of **5%**, but a Telegram trade alert requires a conservative positive profit and a margin of at least **5%**.
- The scanner never uses BloxSwaps' own Win/Loss label, never uses the visible listing price as an item value, and never invents a missing item value or quantity.

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

Telegram alerts show owner/listing links and every valued item line (Supreme unit value, quantity, line total, and the exact Supreme stability label such as Rising/Stable/Decreasing when the source provides it; missing labels show N/A), both side totals, profit and result. Uncertain quantities, large stacks, and suspiciously high profit retain short safety warnings. Parser provenance, raw excerpts, and HTML/API diagnostics stay in GitHub Actions logs.

### Item names and duplicate values

- Exact normalized Supreme names are preferred.
- `aliases.json` contains reviewed manual aliases (alias → canonical Supreme name).
- Exact normalized names and reviewed aliases are supported. Automatic singular/plural and fuzzy name matching are disabled; ambiguous identities are rejected rather than guessed.
- A new unmatched name is reported through a cooldown-controlled maintenance alert and is rechecked. `python tools/suggest_aliases.py` compares BloxSwaps catalogue names with Supreme names and produces review-only candidates; it never writes suggestions automatically. Apply only a manually reviewed JSON mapping with `--approve reviewed-aliases.json`.
- If several Supreme source rows normalize to the same name, all numeric variants are retained. The scanner uses the **higher** value when you give an item and the **lower** value when you receive it, avoiding optimistic duplicate-name valuation.

Items without a numeric Supreme value are never assigned zero or partially valued. Brew and Drool are not included in totals, but their presence blocks a profitable alert until a numeric Supreme value is available; this prevents unknown-value items from manufacturing a false WIN.

## Data sources and pagination

The scanner uses RBLXValue API v2 and calculates all trade totals from `value_source_b` (Supreme Values). `value_source_a` is secondary information only and is never blended into Supreme totals.

- A successful Supreme snapshot is reused for **12 hours** by the production workflow (`VALUES_TTL_SECONDS=43200`), matching the RBLXValue upstream refresh interval. Re-fetching every few minutes cannot make Supreme's source values newer, but it can create needless API dependency and stop scans during temporary credential/API issues. At expiry, the scanner conditionally revalidates each cached API page with its HTTP `ETag` (`If-None-Match`); unchanged pages are reused only after a matching `304 Not Modified`. Pages without a stored ETag are fetched normally. If refresh/revalidation fails, a 304 has no matching cached page, or the API key is missing, it fails closed rather than using stale values.
- The site item catalogue is loaded from `https://api.bloxswaps.com/items` and cached for **1 hour**.
- The first BloxSwaps page is read from its HTML/RSC payload, then every new RSC post is converted through the site's item catalogue—even if its HTML card could not be parsed. Parsed HTML is cross-checked for quantity and contributes visible-price/raw-text diagnostics. Further pages are fetched from `https://api.bloxswaps.com/trade-posts` with `cursor` and `pageSize=20`.
- Pagination does not stop at the first seen post. It stops only after 25 consecutive seen posts; pinned posts do not advance that streak, and recent `lastRefreshedAt` / `createdAt` entries are still examined against the last successful scan checkpoint. Watched and pending ad IDs are searched for before the boundary can stop pagination. The default safety cap is 1,000 pages (up to 20,000 posts at 20 per page); an API failure or a cap reached with a cursor remaining fails the scan without marking unexamined posts as seen.
- RSC and HTML items are matched by catalogue ID or unique normalized name, never by list position. If identity cannot be established uniquely, quantity becomes `uncertain`. A newly observed `itemDetailsId` triggers one forced catalogue refresh, rate-limited to once per minute; unknown IDs are never assigned guessed names or values.
- Cloudflare/CAPTCHA challenge pages and HTTP 403 access denials are reported explicitly rather than treated as an empty feed.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share these secrets. The scanner does not print API keys or Telegram bot tokens in logs.

## Runtime, deduplication, and persistence

- **Trigger cadence:** GitHub cron starts the scanner every 10 minutes. Manual `workflow_dispatch` and `repository_dispatch` event type `mm2-scan` remain available.
- **Polling inside each job:** one process runs `scan_once()` every 30 seconds (configurable within 30–60 seconds) for 570 seconds (9.5 minutes). It finishes with a 2.5-minute timeout margin and handles SIGTERM/SIGINT by completing the current scan cycle before exiting.
- **Concurrency:** `cancel-in-progress: false`; a running scan is not cancelled while it is sending Telegram messages or writing state. GitHub keeps at most one pending run for the concurrency group.
- **Alert budget:** no arbitrary per-run or per-session trade-alert cap. Every qualified opportunity is delivered to Telegram immediately after its individual valuation and qualification pass; conservative positive profit remains mandatory.
- **Detection timing:** the scanner logs first-observed-to-alert latency for every sent trade. The 30-second poll interval is designed to keep detection near 30 seconds when a post appears between cycles; GitHub scheduling and remote API delays are outside that guarantee.
- **Cold start:** if durable seen state is missing, the scanner paginates the feed rather than baselining only the first page. It evaluates posts verifiably refreshed within the last 60 minutes and seeds older or undated posts as historical baseline. It stops after 25 consecutive timestamped posts older than the cutoff; pinned and undated posts do not advance that boundary. Reaching the 1,000-page cap leaves pagination incomplete and triggers maintenance.
- **Watch window:** the latest 6 hours or 300 watched posts (whichever is smaller) are retained. Non-profitable posts remain eligible for re-evaluation, and pagination searches for watched IDs before stopping at the seen boundary. A changed item/quantity fingerprint or changed Supreme value fingerprint causes the same ad to be evaluated again; a newly profitable version can alert again without replaying an unchanged alert.
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
- Supreme records retain their own source slug, category, type, ID, scalar value, explicit numeric range, and published stability label. The stability label is display-only; it never changes the arithmetic used to calculate trade profit. The cache schema is versioned so an older snapshot without stability is refreshed before it can be used. Same-display-name records are never merged into a synthetic high/low range.
- Resolution uses exact source slug, exact normalized name plus catalogue identity, or a unique base-name match constrained by rarity and type (for catalogue labels such as Latte). Reviewed aliases are allowed; automatic fuzzy and plural matching are disabled.
- If more than one Supreme identity remains possible, or Supreme has no numeric value, the item is marked unvalued and the trade is not sent as a profitable alert.
- RBLXValue encodes some Common/Uncommon/Rare xN T1-item notation as a small integer. Values below 10 in those categories are treated as tier-item counts, not ordinary Supreme value units. Higher numeric exceptions remain eligible.
- Give/receive bounds use only the selected item's own published range. When the API has no range, the scalar value_source_b is used identically on both sides; another item with the same name is never used as a range.

## Workflow hardening and external scheduling

- **Action integrity:** every third-party GitHub Action is pinned to a verified full commit SHA with a trailing release-version comment. Dependabot checks `github-actions` weekly.
- **Test isolation:** `tests.yml` runs `python -m unittest discover -s tests` on pushes to `main` and on pull requests. The production scan job no longer runs tests, so a test failure cannot prevent a scheduled scan. The old `source-diagnostics.yml` workflow has been removed.
- **Global log redaction:** `scanner.py` redacts the exact configured Telegram bot token, Telegram chat ID, and RBLXValue API key (only when at least six characters), plus Telegram bot URLs, `rbx_` keys, and Bearer credentials. Both stdout and stderr are filtered before output reaches `scanner.log` or the public Actions artifact.
- **Durable state replacement:** when scanner data changes, `persist-state` compares staged `data/` with `origin/state` and skips an unchanged tree. Otherwise it creates `state-new` as an orphan branch and force-pushes it to `state`, leaving a single-commit state history. Empty or missing state artifacts are ignored so they cannot erase durable data. The workflow-level concurrency group covers both scan and persistence jobs, preventing overlapping state writes.
- **Faster coverage:** the scanner cron runs every 10 minutes; each job polls for 570 seconds at 30-second intervals and has a 12-minute timeout. The watchdog default stale threshold is 20 minutes.
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

## Market Intelligence extension

See [MARKET_INTELLIGENCE.md](MARKET_INTELLIGENCE.md) for the additive public-feed and optional free-AI architecture. The existing BloxSwaps scanner remains unchanged; new sources and AI review are not enabled in production until their feeds and tests are verified.
