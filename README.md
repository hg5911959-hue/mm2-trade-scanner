# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with Telegram alerts. The GitHub Actions job runs a 50-minute scan loop, checking approximately every 45 seconds. The 5-minute cron and `repository_dispatch` event remain backup entry points. Concurrency prevents overlapping scanner jobs.

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
- If only the best-case profit is positive, it sends a separate **MANUAL VERIFICATION REQUIRED** alert.
- If even the best-case profit is not positive, it does not send a trade alert.

Telegram messages include quantity source, the raw item excerpt, and visible listed price for diagnostics only. A warning is added for a margin of **100% or more** or a quantity of **5 or more**.

### Item names and duplicate values

- Exact normalized Supreme names are preferred.
- `aliases.json` contains reviewed manual aliases (alias → canonical Supreme name).
- Singular/plural normalization is supported. Fuzzy matching is deliberately strict (similarity at least 0.97, with a clear gap from the next candidate); ambiguous matches are rejected.
- A new unmatched name is reported once in a maintenance summary and is rechecked on every scan. No value is guessed. Add an alias only after verifying the exact item.
- If several Supreme source rows normalize to the same name, all numeric variants are retained. The scanner uses the **higher** value when you give an item and the **lower** value when you receive it, avoiding optimistic duplicate-name valuation.

Items without a numeric Supreme value are not partially valued. Brew and Drool remain explicitly ignored in totals; an offer is excluded if either side has no remaining valued items.

## Data sources and pagination

The scanner uses RBLXValue API v2 and calculates all trade totals from `value_source_b` (Supreme Values). `value_source_a` is secondary information only and is never blended into Supreme totals.

- A successful Supreme snapshot is reused for **5 minutes**. Once expired, the scanner conditionally revalidates each cached API page with its HTTP `ETag` (`If-None-Match`); unchanged pages are reused only after a matching `304 Not Modified`. Pages without a stored ETag are fetched normally. If refresh/revalidation fails, a 304 has no matching cached page, or the API key is missing, it fails closed rather than using stale values.
- The site item catalogue is loaded from `https://api.bloxswaps.com/items` and cached for **1 hour**.
- The `BloxSwaps Source Diagnostics` workflow performs a public-feed, RSC, cursor-pagination, and quantity cross-check without Telegram sends; it stores only a redacted HTML card fixture for regression tests.
- The first BloxSwaps page is read from its HTML/RSC payload, then every new RSC post is converted through the site's item catalogue—even if its HTML card could not be parsed. Parsed HTML is cross-checked for quantity and contributes visible-price/raw-text diagnostics. Further pages are fetched from `https://api.bloxswaps.com/trade-posts` with `cursor` and `pageSize=20`.
- Pagination continues through recent pages until a previously seen ad ID is reached. The default safety cap is 50 pages; reaching it with a cursor remaining raises a maintenance warning rather than claiming complete coverage.
- Structured item IDs are mapped to BloxSwaps display names through the site catalogue. Unknown IDs are not assigned guessed names or values.
- Cloudflare/CAPTCHA challenge pages and HTTP 403 access denials are reported explicitly rather than treated as an empty feed.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share these secrets. The scanner does not print API keys or Telegram bot tokens in logs.

## Runtime, deduplication, and persistence

- Scanner job: 50-minute loop, approximately every 45 seconds, with a 55-minute job timeout.
- Backup triggers: GitHub cron every 5 minutes, manual `workflow_dispatch`, and `repository_dispatch` event type `mm2-scan`.
- Concurrency: one scanner workflow at a time; a new scheduled run does not cancel the active scan.
- The first run with missing seen-ad state creates a baseline from the current first page and does **not** replay the historical feed backlog. Subsequent scans process new posts until the seen boundary.
- `data/seen_ads.json` tracks processed BloxSwaps ad IDs. Unmatched or malformed listings remain eligible for rechecking; successful alerts and intentionally excluded Ajout posts are marked seen.
- Alerted IDs, uncertain Telegram deliveries, seen IDs, history, failure counters, Supreme snapshot, BloxSwaps item catalogue, unmatched names, and maintenance-alert throttles are stored on the dedicated **`state` branch**. That branch is updated only when state files change.
- The workflow retains a one-time legacy Actions Cache restore path for migration. The state branch becomes authoritative after it exists.
- A corrupt or unreadable state file fails closed instead of silently resetting deduplication.
- Alerted IDs retain the newest 5,000 in insertion order; writes use atomic replace operations.
- `MAX_ALERTS_PER_RUN` defaults to 10 and `ALERT_DELAY_SECONDS` defaults to 1 second. Deferred opportunities remain unalerted for a later scan.
- A definite Telegram API rejection clears its pre-send guard so the offer can be retried later. A timeout, connection failure, invalid response, or HTTP 5xx is treated as **uncertain delivery**: the listing is quarantined and is not automatically resent. Check Telegram before manually clearing its entry in `data/telegram_uncertain.json`.
- A failed Telegram message does not stop later opportunities in the same batch.
- Any unparsed or malformed card triggers a maintenance alert in long-loop mode; a persisted one-hour cooldown prevents spam even when only one card fails. Unmatched item names are summarized once per name and rechecked on later scans.
- The watchdog checks successful scan runs every 15 minutes. It recognizes an active 50-minute scan loop and suppresses false stale-success alerts while that job is running; it uses `GITHUB_REPOSITORY`, not a hard-coded repository path.
- Recommended independent monitoring: configure Healthchecks.io and store its ping URL as `HEALTHCHECKS_PING_URL`. The workflow pings when the long scan job finishes.

## Logs and manual operations

- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- External scheduler fallback: a scheduler can call GitHub's repository-dispatch API with `{"event_type":"mm2-scan"}`. Keep any scheduler token in the scheduler's secret store; never paste it into chat or commit it.
- `history.csv` records detected opportunities and calculated values; it cannot confirm that a Roblox trade was accepted or completed.

## Limitations and safety

- BloxSwaps' public RSC/API contract and HTML markup can change. The live fixture and regression tests protect known layouts, not every future layout.
- The scanner uses a maximum of 50 cursor pages per scan. If there are more than 1,000 new posts before a seen boundary, the maintenance warning indicates that the scan was capped.
- GitHub cron starts can be delayed. The long-running loop reduces missed short-lived offers but cannot guarantee detection before another trader accepts them.
- Supreme Values are estimates delivered by a third-party provider. A fresh RBLXValue response does not guarantee Supreme itself has just changed its upstream data.
- No Roblox password, browser cookie, or account session token is needed.
