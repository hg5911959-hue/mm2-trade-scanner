# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with Telegram alerts. GitHub Actions uses staggered scheduled checks and also scans after scanner-code updates.

## How a trade is evaluated

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded completely.
- The scanner ignores BloxSwaps' own Win/Loss label.
- Profit = total Supreme Values of received items minus total Supreme Values of given items.
- Minimum absolute profit defaults to **0**, and minimum profit percentage is set to **5%** in the scheduled workflow, filtering out fair trades. Qualified offers are sorted by highest absolute Supreme profit before alerts are sent.
- If even one item cannot be matched to a numeric Supreme Values entry, the scanner does not calculate or guess a partial total. It sends one **MANUAL VALUE CHECK** Telegram alert for that listing, then keeps retrying the listing on future scans so a later API mapping can still produce a full profit alert.

Telegram alerts include both item lists, quantities, per-item values, both totals, calculated profit, result, and source links. The **OPEN OWNER PROFILE** link is shown first when the card exposes a BloxSwaps user URL; the listing/discussion URL is secondary. The scan log reports how many parsed cards had a direct owner URL. Repeated copies of the same weapon are grouped after quantities are summed from repeated item images, explicit stack counts such as `x3` / `3x` / `×3`, and counts embedded in image alt labels. The scanner sends Telegram messages only for listings it has not successfully alerted before. A listing with an unknown Supreme item or a currently unprofitable value is **not** marked as completed: it is re-evaluated on later scans, so an API mapping/value update can still make it alert.

## Value data source

The scanner uses the documented **RBLXValue API v2** and calculates all totals using `value_source_b` (Supreme Values). It also compares `value_source_a` (MM2Values) as a secondary warning only when the difference is at least 30%; it never blends the two sources or substitutes the second source for Supreme Values.

Create a free API key at https://rblxvalue.com/developer. The key must be active and normally starts with `rbx_`. RBLXValue may require email verification. The API key is sent in the `X-Api-Key` header and must remain private.

API values are refreshed by the provider approximately every 12 hours. The scanner caches the downloaded value list locally for four hours to reduce requests. Free-plan attribution is included in Telegram messages: **Data from rblxvalue.com**.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key copied from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share any of these secrets. The scanner never prints API keys or the Telegram bot token in logs. Secrets are passed only through GitHub Actions `secrets.*` environment variables.

## Run and diagnostics

- Scheduled checks: two staggered cron schedules nominally start 2–3 minutes apart (GitHub Actions cron uses UTC; starts may be delayed or dropped during platform load). Each individual cron expression follows GitHub's 5-minute minimum.
- Workflow triggers: staggered schedules, manual `workflow_dispatch`, and code updates to `scanner.py`, tests, or the scan workflow.
- Concurrency: one workflow at a time; queued runs are not cancelled.
- The scanner workflow has `contents: read` only; the separate monthly keepalive workflow alone has `contents: write`.
- Alert state stores only listings whose profit alert was successfully sent. A separate review state suppresses repeated manual-review warnings, without preventing future profit alerts. Existing alert history is used to migrate prior sent IDs; non-profitable and unrecognized listings remain eligible for re-evaluation.
- Alerted IDs retain the newest 5,000. State, values cache, failure counter, and history writes use atomic replace operations.
- IDs use normalized owner identity, stable card/ad identifiers when exposed, listing path/query identity, and sorted item names and quantities; relative-time labels are ignored.
- Telegram messages are capped at 4,000 characters. Calculated margins at or above 200% include a warning to manually verify item names and values.
- Failure alerts: Telegram is notified after 3 consecutive failed runs, then every 5 additional consecutive failures (8, 13, 18...). A successful run resets the counter.
- GitHub Actions schedules are recurring checks, not a permanently running server; GitHub may occasionally delay scheduled starts.
- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Telegram connection test: **Actions → Telegram Connection Test → Run workflow**. This sends one explicit test message without changing scanner state or triggering a trade scan; the job fails with a safe API error if Telegram rejects it.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
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
