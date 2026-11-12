# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with concise Telegram alerts. GitHub Actions starts a job every 15 minutes; each job polls the feed every 45 seconds for approximately 12 minutes, then exits cleanly before its 14-minute timeout.

## Trade evaluation

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded. The filter is applied to the individual card and also checks the structured `requestedWildcards` data for `ADD`.
- Profit = total Supreme Values of received items minus total Supreme Values of given items.
- The scheduled workflow uses a minimum absolute difference of **0** and minimum percentage of **0%**, but a Telegram trade alert still requires a strictly positive profit.
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
- If only the best-case profit is positive, it sends a separate **MANUAL CHECK** alert.
- If even the best-case profit is not positive, it does not send a trade alert.

Telegram alerts show only the owner/listing links, up to 8 item lines per side, totals, Supreme profit, and result. Uncertain quantities, large stacks, and suspiciously high profit retain short safety warnings. Parser provenance, raw excerpts, and HTML/API diagnostics stay in GitHub Actions logs.

### Item names and duplicate values

- Exact normalized Supreme names are preferred.
- `aliases.json` contains reviewed manual aliases (alias → canonical Supreme name).
- Singular/plural normalization is supported. Fuzzy matching is deliberately strict (similarity at least 0.97, with a clear gap from the next candidate); ambiguous matches are rejected.
- A new unmatched name is reported through a cooldown-controlled maintenance alert and is rechecked. `python tools/suggest_aliases.py` compares BloxSwaps catalogue names with Supreme names and produces review-only candidates; it never writes suggestions automatically. Apply only a manually reviewed JSON mapping with `--approve reviewed-aliases.json`.
- If several Supreme source rows normalize to the same name, all numeric variants are retained. The scanner uses the **higher** value when you give an item and the **lower** value when you receive it, avoiding optimistic duplicate-name valuation.

Items without a numeric Supreme value are not partially valued. Brew and Drool remain explicitly ignored in totals; an offer is excluded if either side has no remaining valued items.

## Data sources and pagination

The scanner uses RBLXValue API v2 and calculates all trade totals from `value_source_b` (Supreme Values). `value_source_a` is secondary information only and is never blended into Supreme totals.

- A successful Supreme snapshot is reused for **5 minutes**. Once expired, the scanner conditionally revalidates each cached API page with its HTTP `ETag` (`If-None-Match`); unchanged pages are reused only after a matching `304 Not Modified`. Pages without a stored ETag are fetched normally. If refresh/revalidation fails, a 304 has no matching cached page, or the API key is missing, it fails closed rather than using stale values.
- The site item catalogue is loaded from `https://api.bloxswaps.com/items` and cached for **1 hour**.
- The `BloxSwaps Source Diagnostics` workflow performs a public-feed, RSC, cursor-pagination, and quantity cross-check without Telegram sends; it stores only a redacted HTML card fixture for regression tests.
- The first BloxSwaps page is read from its HTML/RSC payload, then every new RSC post is converted through the site's item catalogue—even if its HTML card could not be parsed. Parsed HTML is cross-checked for quantity and contributes visible-price/raw-text diagnostics. Further pages are fetched from `https://api.bloxswaps.com/trade-posts` with `cursor` and `pageSize=20`.
- Pagination does not stop at the first seen post. It stops only after 25 consecutive seen posts, and recent `lastRefreshedAt` / `createdAt` entries are still examined against the last successful scan checkpoint. The default safety cap is 50 pages; an API failure or a cap reached with a cursor remaining fails the scan without marking unexamined posts as seen.
- RSC and HTML items are matched by catalogue ID or unique normalized name, never by list position. If identity cannot be established uniquely, quantity becomes `uncertain`. A newly observed `itemDetailsId` triggers one forced catalogue refresh, rate-limited to once per minute; unknown IDs are never assigned guessed names or values.
- Cloudflare/CAPTCHA challenge pages and HTTP 403 access denials are reported explicitly rather than treated as an empty feed.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share these secrets. The scanner does not print API keys or Telegram bot tokens in logs.

## Runtime, deduplication, and persistence

- **Trigger cadence:** GitHub cron starts the scanner every 15 minutes. Manual `workflow_dispatch` and `repository_dispatch` event type `mm2-scan` remain available.
- **Polling inside each job:** one process runs `scan_once()` every 45 seconds (configurable within 30–60 seconds) for about 12 minutes. It finishes with a two-minute timeout margin and handles SIGTERM/SIGINT by completing the current scan cycle before exiting.
- **Concurrency:** `cancel-in-progress: false`; a running scan is not cancelled while it is sending Telegram messages or writing state. GitHub keeps at most one pending run for the concurrency group.
- **Alert budget:** at most 3 trade alerts per 12-minute workflow session (not 3 per 45-second cycle), with a 2-second delay between messages. Positive profit is still mandatory. Excess opportunities remain pending for the next job.
- **Detection timing:** the scanner logs first-observed-to-alert latency for every sent trade. The 45-second poll interval is designed to keep detection under one minute when a post appears between cycles; GitHub scheduling and remote API delays are outside that guarantee.
- **Cold start:** if durable seen state is missing, the scanner does not mark the entire feed as seen without evaluation. It evaluates posts verifiably refreshed within the last 60 minutes and seeds older or undated posts as historical baseline.
- **Watch window:** the latest 6 hours or 300 watched posts (whichever is smaller) are retained. Non-profitable posts remain eligible for re-evaluation. A changed item/quantity fingerprint or changed Supreme value fingerprint causes the same ad to be evaluated again; a newly profitable version can alert again without replaying an unchanged alert.
- **State compatibility:** existing `alerted.json`, seen-ad, pending-recheck, and uncertain-Telegram state formats are retained and migrated in place. New watch-window and last-success checkpoints are additive. A corrupt or unreadable state file fails closed instead of silently resetting deduplication.
- **Durable state source:** the `state` branch is the sole durable source. The scan job has `contents: read`; it restores the branch and uploads a state artifact. A separate `persist-state` job has `contents: write` and updates only the state branch. The legacy Actions Cache restore path has been removed.
- **State checkpoints:** state files are written atomically after each scan cycle; the final state artifact is committed after the polling job. The two-minute timeout margin allows artifact upload and the isolated state commit to finish.
- **Telegram delivery:** a definite Telegram API rejection clears its pre-send guard so the offer can be retried. A timeout, connection failure, invalid response, or HTTP 5xx is treated as **uncertain delivery**: the listing is quarantined and is not automatically resent. Check Telegram before manually clearing its entry in the uncertain state file.
- **Maintenance alerts:** malformed/unreadable cards, missing RSC data, incomplete pagination, unmatched item names, unresolved pending ads after a complete pagination pass, and expired Telegram quarantines use persistent cooldowns. They are not gated by a loop-mode environment flag. Missing RSC data is a scan error; HTML-only partial data is never trusted.
- **Watchdog:** runs every 5 minutes, suppresses stale-success alerts while a scanner job is active, and raises a warning after 25 minutes without a successful scanner run. The repository name is read from `GITHUB_REPOSITORY`.
- **Action security:** GitHub Actions dependencies are pinned to immutable commit SHAs. The scanner job does not have repository-content write permission; only the state-persistence job can write contents.
- **External monitoring:** optionally configure Healthchecks.io and store its ping URL as `HEALTHCHECKS_PING_URL`. The workflow pings on completion and reports failed runs.

## Logs and manual operations

- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- External scheduler fallback: a scheduler can call GitHub's repository-dispatch API with `{"event_type":"mm2-scan"}`. Keep any scheduler token in the scheduler's secret store; never paste it into chat or commit it.
- `history.csv` records detected opportunities and calculated values; it cannot confirm that a Roblox trade was accepted or completed.

## Limitations and safety

- BloxSwaps' public RSC/API contract and HTML markup can change. The live fixture and regression tests protect known layouts, not every future layout.
- The scanner uses a maximum of 50 cursor pages per scan. If there are more than 1,000 new posts before a seen boundary, the maintenance warning indicates that the scan was capped.
- GitHub cron starts can be delayed. A 15-minute schedule can miss short-lived offers; it cannot guarantee detection before another trader accepts them.
- Supreme Values are estimates delivered by a third-party provider. A fresh RBLXValue response does not guarantee Supreme itself has just changed its upstream data.
- No Roblox password, browser cookie, or account session token is needed.
