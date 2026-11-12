# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with Telegram alerts. GitHub Actions uses staggered scheduled checks and also scans after scanner-code updates.

## How a trade is evaluated

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded completely.
- The scanner ignores BloxSwaps' own Win/Loss label.
- Profit = total Supreme Values of received items minus total Supreme Values of given items.
- Minimum absolute profit defaults to **0**, and minimum profit percentage is set to **0%** in the scheduled workflow, but every alert must still have strictly positive profit; fair and negative-profit trades are never reported. Qualified offers are sorted by highest absolute Supreme profit before alerts are sent.
- If any item cannot be matched to a numeric Supreme Values entry, the entire offer is silently excluded: no partial calculation, no manual-review Telegram alert, and no offer notification. It is checked again on later scans in case the value mapping becomes available. Items explicitly configured as non-valued (currently Brew and Drool) are ignored in the item totals; an offer is still excluded if either trade side has no remaining numerically valued items.

Telegram alerts include both item lists, quantities, per-item values, both totals, calculated profit, result, and source links. The **OPEN OWNER PROFILE** link is shown first when the card exposes a BloxSwaps user URL; the listing/discussion URL is secondary. The scan log reports how many parsed cards had a direct owner URL. Repeated copies of the same weapon are grouped after quantities are summed from repeated item images, explicit stack counts such as `x3` / `3x` / `×3`, counts embedded in image alt labels, and explicit overlay badges found in nearby nested item wrappers. Bare numbers are accepted only from elements with quantity/count-like semantics, so nearby listing prices are not treated as weapon quantities. If BloxSwaps changes its markup, parser behavior still requires validation against a real listing DOM. The scanner sends Telegram messages only for listings it has not successfully alerted before. A listing with an unknown Supreme item or a currently unprofitable value is **not** marked as completed: it is re-evaluated on later scans, so an API mapping/value update can still make it alert.

## Value data source

The scanner uses the documented **RBLXValue API v2** and calculates all totals using `value_source_b` (Supreme Values). If multiple API rows normalize to the same display name, it keeps the highest numeric Supreme value and the secondary value from that same row (never a lower duplicate just because it appeared later). Non-numeric tier notation such as `4T1` is not treated as a numeric item value. It also compares `value_source_a` (MM2Values) as a secondary warning only when the difference is at least 30%; it never blends the two sources or substitutes the second source for Supreme Values.

Create a free API key at https://rblxvalue.com/developer. The key must be active and normally starts with `rbx_`. RBLXValue may require email verification. The API key is sent in the `X-Api-Key` header and must remain private.

The scanner requests the full current `value_source_b` snapshot from RBLXValue on every scan (the GitHub workflow runs every 5 minutes). It does not reuse a local cached snapshot for calculations: if the API cannot refresh, the scan fails closed and sends no profit alerts based on stale values. Important: the third-party provider may itself refresh its upstream Supreme Values dataset on a slower cadence (historically around 12 hours), so this guarantees no extra local staleness, not instant publication by Supreme or RBLXValue. Free-plan attribution is included in Telegram messages: **Data from rblxvalue.com**.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key copied from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share any of these secrets. The scanner never prints API keys or the Telegram bot token in logs. Secrets are passed only through GitHub Actions `secrets.*` environment variables.

## Run and diagnostics

- Scheduled checks: GitHub cron requests a scan every 5 minutes (UTC; starts may be delayed or dropped during platform load).
- Workflow triggers: 5-minute schedule, manual `workflow_dispatch`, external `repository_dispatch` events of type `mm2-scan`, and code updates to `scanner.py`, tests, or the scan workflow.
- Concurrency: one workflow at a time; queued runs are not cancelled.
- The scanner workflow has `contents: read` only; the separate monthly keepalive workflow alone has `contents: write`.
- Alert state stores only listings whose profit alert was successfully sent. Offers with unknown values are silently skipped and re-evaluated on later scans. Existing alert history is used to migrate prior sent IDs; non-profitable and unrecognized listings remain eligible for re-evaluation.
- Alerted IDs retain the newest 5,000 in insertion order, and legacy `history.csv` IDs are backfilled without duplicates. State, values cache, failure counter, and history writes use atomic replace operations.
- Telegram flood protection: `MAX_ALERTS_PER_RUN` defaults to 10 and `ALERT_DELAY_SECONDS` defaults to 1 second. Offers are ranked first; only the first N are sent per run. Deferred offers remain unalerted and are sent on later scans.
- Brew and Drool are excluded from numeric totals because they have no numeric Supreme value. If either appears in an otherwise eligible offer, the alert explicitly notes the omitted item(s); no value is guessed.
- A separate watchdog checks the latest successful `scan.yml` run every 15 minutes and alerts if it is at least 30 minutes old, then repeats every 6 hours.
- Recommended independent monitoring: configure Healthchecks.io with a 5-minute period and 15-minute grace time, then store its ping URL as `HEALTHCHECKS_PING_URL`.
- IDs use normalized owner identity, stable card/ad identifiers when exposed, listing path/query identity, and sorted item names and quantities; relative-time labels are ignored.
- Telegram messages are capped at 4,000 characters. Calculated margins at or above 200% include a warning to manually verify item names and values.
- Failure alerts: Telegram is notified after 3 consecutive failed runs, then every 5 additional consecutive failures (8, 13, 18...). A successful run resets the counter.
- GitHub Actions schedules are recurring checks, not a permanently running server; GitHub may occasionally delay scheduled starts.
- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan; the job fails with a safe API error if Telegram rejects it.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- External scheduler fallback: the scanner accepts `repository_dispatch` event type `mm2-scan`. A scheduler such as cron-job.org can call GitHub’s repository-dispatch API every 5 minutes using a fine-grained token with Contents: read/write access to this repository. Store that token only in the scheduler service; never paste it into chat or commit it. The request body is `{"event_type":"mm2-scan"}`. This avoids depending exclusively on GitHub cron, but the external scheduler must still be configured by the repository owner.
- Optional missed-schedule monitoring: create a free check at Healthchecks.io and save its ping URL as the repository secret `HEALTHCHECKS_PING_URL`. The workflow pings on successful scans and sends `/fail` on failures; the service can alert if scheduled pings stop arriving.
- Processed IDs, Supreme cache, failure counter, and `history.csv` are persisted with GitHub Actions Cache. Runtime `data/` is git-ignored and is not tracked in `main`. A separate monthly keepalive workflow commits a tiny activity marker to reduce the risk of scheduled workflows being disabled for inactivity.
- `history.csv` records detected opportunities and their calculated values. It cannot confirm that a Roblox trade was accepted or completed.

If the API returns HTTP 403, the key is invalid, inactive, unverified, expired, or suspended. Check its status at https://rblxvalue.com/developer, create/regenerate a key if needed, then replace the GitHub secret `RBLXVALUE_API_KEY`. A GitHub secret's name must remain exactly the same.

## Safety and limitations

- Direct Supreme Values page access from GitHub Actions can be blocked; the API is used instead.
- The API is a third-party delivery source for Supreme Values data, not an official Supreme Values API. The scanner specifically selects the provider's `value_source_b` field.
- Only listings visible on the selected BloxSwaps P2P page are scanned.
- HTTP 429 and transient 5xx/network errors are retried with exponential backoff and `Retry-After` support.
- If BloxSwaps' expected trade-side markers disappear or cards stop parsing, the scanner fails loudly instead of treating the page as empty; regression tests run before each scan.
- GitHub Actions Cache is not permanent archival storage; export `history.csv` periodically if you need long-term records.
- Parsing depends on BloxSwaps' page structure; changes to its markup may require parser updates.
- Item values are estimates and do not guarantee a trade will be accepted or completed.
- No Roblox password, browser cookie, or account session token is needed.
