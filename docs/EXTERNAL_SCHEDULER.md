# Independent scheduler fallback (Cloudflare Worker)

The repository's GitHub cron events have been observed arriving with multi-hour gaps even while the scanner workflow reports as active. The in-repository watchdog can recover after a scan completes, but it cannot wake itself if both scheduled events stop arriving. This Worker is an independent fallback hosted outside GitHub Actions.

## What it does

Every five minutes, the Worker reads the latest runs of `MM2 Scanner Scheduler` and:

- Does nothing if a scanner run is queued or in progress.
- Does nothing if a successful scan completed within the last 15 minutes.
- Does nothing for 10 minutes after a recent manual dispatch or failed run, preventing dispatch loops.
- Respects a manually disabled GitHub workflow, but re-enables it if GitHub reports `disabled_inactivity`.
- Otherwise dispatches one live run of `scan-scheduler.yml` with `dry_run=false` and `verify_rblxvalue_key=false`.

The Worker never reads trade listings, RBLXValue data, Telegram messages, or repository state. It only checks GitHub Actions run metadata and dispatches the existing workflow. The normal scanner concurrency group remains the final duplicate-run guard.

## Deploy in Cloudflare

1. In Cloudflare, open **Workers & Pages → Create → Worker** and create a Worker named `mm2-external-scheduler`.
2. Paste the contents of [ops/cloudflare_mm2_scheduler.mjs](../ops/cloudflare_mm2_scheduler.mjs) into the Worker editor, or deploy the checked-in entrypoint with Wrangler using [ops/cloudflare/wrangler.jsonc](../ops/cloudflare/wrangler.jsonc).
3. Under **Settings → Variables and Secrets**, add:
   - Secret `GITHUB_TOKEN`: a fine-grained GitHub token restricted to `hg5911959-hue/mm2-trade-scanner`, with **Actions: Read and write** and repository metadata read access.
   - Variable `GITHUB_REPOSITORY`: `hg5911959-hue/mm2-trade-scanner`.
4. Under **Settings → Triggers → Cron Triggers**, add `*/5 * * * *` (UTC).
5. Deploy, then inspect Worker logs. Expected outcomes are `recent_success`, `active_run`, `recent_dispatch`, or `dispatched`.
6. Visit the Worker's `/health` route to confirm it is deployed. This health route does not expose the GitHub token.

Never commit the token or paste it into chat. Do not reuse the private-state token: the scheduler token needs Actions write on the public scanner repository, while the state token should be restricted to the separate private state repository.

## Limitations

Cloudflare Cron Triggers are independent of GitHub Actions, but they are not a hard real-time guarantee. Cloudflare documents that trigger changes can take up to 15 minutes to propagate and scheduled invocations can be missed during propagation. Keep the existing GitHub schedule and watchdog as additional layers. See the [Cloudflare Cron Triggers documentation](https://developers.cloudflare.com/workers/configuration/cron-triggers/).

The Worker dispatches the existing workflow; it does not bypass the scanner's Supreme authorization gate, RBLXValue request budget, or Telegram delivery safeguards.
