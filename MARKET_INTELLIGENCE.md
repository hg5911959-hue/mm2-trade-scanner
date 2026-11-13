# MM2 Market Intelligence — free-first foundation

This is an additive layer. It does not replace or modify the existing BloxSwaps scanner, Supreme valuation, state persistence, Telegram delivery, or fail-closed trade qualification.

## Data sources without a Discord bot

The first alternative adapter reads public Reddit RSS feeds only. It does not join Discord servers, use Discord user tokens, scrape private Discord endpoints, or bypass server permissions. Configure feeds only when the community and feed are publicly accessible and automated access is permitted. Reddit may rate-limit or block RSS requests; a failed feed is reported as an error, never interpreted as an empty market.

Example feed URL pattern:
- https://www.reddit.com/r/MurderMystery2/new/.rss

To test a candidate feed locally, set MM2_PUBLIC_REDDIT_FEEDS to one or more semicolon-separated URLs and run python tools/collect_public_feeds.py. The command emits JSON and exits non-zero if any feed fails. A subreddit name is only an example until a live request confirms that the community exists and its feed is accessible. Other safe sources can be added later through official public APIs, RSS feeds, or user-submitted CSV/JSON exports. Arbitrary Discord message history is not generally available without an authorized Discord app/client. Do not use self-bots, user tokens, or private API scraping.

## AI providers and zero-budget operation

- **Gemini:** optional automated review through the official Gemini Developer API free tier. Free models and quotas can change; requests may be rate-limited or unavailable. Add GEMINI_API_KEY as a GitHub Actions secret only after creating a key in Google AI Studio. The adapter defaults to gemini-3.6-flash; override GEMINI_MODEL if that model is not enabled for the project. Keep the project on the free tier and do not attach paid billing if zero cost is mandatory.
- **DeepSeek:** use the free DeepSeek chat website for manual second-opinion reviews. The DeepSeek API is not assumed to have a permanent free tier, so this repository does not call it automatically. Use build_deepseek_manual_prompt(payload) to create a prompt for copy/paste. Do not paste secrets or private account data.

If Gemini's free quota is exhausted, AI review is skipped or marked unavailable and the deterministic scanner continues unchanged. No trade may be accepted, rejected, or valued solely because an AI model says so.

## Deterministic safety rules

1. Supreme Values and the existing Python engine remain the sole source of trade arithmetic.
2. AI may comment on risk, evidence gaps, liquidity, and verification steps only.
3. Model output is advisory and schema-checked; invalid output is discarded.
4. Missing values, uncertain quantities, or incomplete source scans remain unresolved; AI cannot fill them in.
5. API keys belong in GitHub Actions Secrets, never in code, commits, logs, or chat.

## Current implementation status

This branch adds a public Reddit RSS adapter, a Gemini free-tier adapter, a DeepSeek manual-prompt exporter, and offline unit tests. It does not yet enable new feeds in the production workflow or send AI-generated Telegram alerts. Those steps require validating actual public feeds and reviewing test results first.
