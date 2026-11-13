# MM2 Market Intelligence — free-first data and AI architecture

This extension is additive. The existing BloxSwaps scanner, Supreme valuation, Telegram delivery, state persistence, and fail-closed trade qualification remain authoritative.

## Primary data sources

### BloxSwaps
The existing production scanner continues to collect BloxSwaps listings and calculate trade values using the existing verified Supreme Values pipeline. No AI model is allowed to invent or replace these values.

### Discord (authorized read-only bot)
The repository now includes `discord_collector.py`, an official Discord bot client that reads only channel IDs explicitly configured by the owner. It does not send messages, moderate, join voice, enumerate unrelated channels, or use a personal account token/self-bot.

Configure:
- GitHub Actions Secret: `DISCORD_BOT_TOKEN` — the bot token from Discord Developer Portal.
- GitHub Actions Variable: `DISCORD_CHANNEL_IDS` — comma- or semicolon-separated numeric channel IDs to monitor.

In Discord Developer Portal, enable **Message Content Intent**. Invite the bot into each server with only **View Channels** and **Read Message History**, and ensure those permissions are granted on each selected trading channel. Do not grant Administrator, Send Messages, Manage Messages, or other write permissions.

The workflow fetches up to 100 recent messages per configured channel on each scheduled scan, stores a bounded seven-day deduplicated history in `data/discord_messages.json`, and persists it through the existing state branch. The bot reads only the selected channels. If a channel is inaccessible, the collector reports an explicit warning rather than treating it as an empty channel.

### Alternative public sources
Public RSS sources may supplement the primary sources later, but they are not a substitute for authorized Discord channel access. The Reddit RSS adapter is optional and remains separate.

## AI providers and zero-budget operation

- **Gemini:** optional automated commentary through the official Gemini Developer API free tier. Free models and quotas can change. Add `GEMINI_API_KEY` as a GitHub Actions secret only after creating a key in Google AI Studio. The adapter is advisory and can be skipped if no key or free quota is available.
- **DeepSeek:** use the free DeepSeek chat website for manual second-opinion reviews. The API is not assumed to have a permanent free tier. The repository can export a prompt with `build_deepseek_manual_prompt(payload)`; no paid API call is made.

Only public listing text, message links, and normalized trade details should be sent to an AI model. Do not send bot tokens, account credentials, private DMs, or unrelated personal data.

## Deterministic safety rules

1. Supreme Values and the existing Python engine remain the sole source of trade arithmetic.
2. AI may comment on risk, evidence gaps, liquidity, and verification steps only.
3. Model output is advisory and schema-checked; invalid output is discarded.
4. Missing values, uncertain quantities, or incomplete source scans remain unresolved; AI cannot fill them in.
5. API keys and bot tokens belong in GitHub Actions Secrets, never in code, commits, logs, or chat.
6. Do not use Discord self-bots, personal user tokens, private endpoint scraping, or permission bypasses.

## Current implementation status

The repository has a read-only Discord collector, a BloxSwaps scanner, optional Gemini free-tier commentary, a manual DeepSeek prompt exporter, and unit tests. Discord collection activates only after the owner configures the bot token secret and channel ID variable. Parsing Discord messages into standardized item/quantity offers and combining them with BloxSwaps market data is the next integration stage.
