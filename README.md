# MM2 Trade Scanner

Automated scanner for public **BloxSwaps MM2 P2P listings**, with Telegram alerts. GitHub Actions checks every 15 minutes.

## How a trade is evaluated

From the scanner user's perspective:

- **BloxSwaps Propose** = items you receive.
- **BloxSwaps Recherche** = items you give.
- Any listing containing **Ajout / Ajouts** is excluded completely.
- The scanner ignores BloxSwaps' own Win/Loss label.
- Profit = total Supreme Values of received items minus total Supreme Values of given items.
- Minimum profit defaults to **0**, so fair trades are included.
- If even one item cannot be matched to a numeric Supreme Values entry, the whole listing is skipped. The scanner never guesses or sends partial totals.

Telegram alerts include both item lists, quantities, per-item values, both totals, calculated profit, result, and source link.

## Value data source

The scanner uses the documented **RBLXValue API v2** and reads only `value_source_b` (Supreme Values). It does not use `value` (blended value) or `value_source_a` (MM2Values).

Create a free API key at https://rblxvalue.com/developer. The key must be active and normally starts with `rbx_`. RBLXValue may require email verification. The API key is sent in the `X-Api-Key` header and must remain private.

API values are refreshed by the provider approximately every 12 hours. The scanner caches the downloaded value list locally for four hours to reduce requests. Free-plan attribution is included in Telegram messages: **Data from rblxvalue.com**.

## Required GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_CHAT_ID`: your Telegram numeric chat ID.
- `RBLXVALUE_API_KEY`: active API key copied from the RBLXValue developer dashboard (raw key only, no quotes).

Never commit or share any of these secrets. The scanner never prints the API key in logs.

## Run and diagnostics

- Scheduled checks: every 15 minutes (GitHub Actions cron uses UTC).
- Manual run: **Actions → MM2 Trade Scanner → Run workflow**.
- Logs: open the workflow run and download the `mm2-scanner-diagnostics` artifact.
- Processed listing IDs and the Supreme cache are stored under `data/`.

If the API returns HTTP 403, the key is invalid, inactive, unverified, expired, or suspended. Check its status at https://rblxvalue.com/developer, create/regenerate a key if needed, then replace the GitHub secret `RBLXVALUE_API_KEY`. A GitHub secret's name must remain exactly the same.

## Safety and limitations

- Direct Supreme Values page access from GitHub Actions can be blocked; the API is used instead.
- The API is a third-party delivery source for Supreme Values data, not an official Supreme Values API. The scanner specifically selects the provider's `value_source_b` field.
- Only listings visible on the selected BloxSwaps P2P page are scanned.
- Parsing depends on BloxSwaps' page structure; changes to its markup may require parser updates.
- Item values are estimates and do not guarantee a trade will be accepted or completed.
- No Roblox password, browser cookie, or account session token is needed.
