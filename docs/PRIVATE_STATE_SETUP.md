# Private durable state setup

The scanner source repository is public so the GitHub Pages dashboard can remain public. Runtime state must therefore live in a separate **private** repository.

## One-time setup

1. Create an empty private GitHub repository named `hg5911959-hue/mm2-trade-scanner-state`. It may have a default branch, but do not add a `state` branch manually.
2. Create a fine-grained personal access token restricted to that private repository with **Contents: Read and write**. Repository metadata read access is also required. A classic token with `repo` scope also works, but is broader.
3. In the public scanner repository, open **Settings → Secrets and variables → Actions → New repository secret**.
4. Name the secret `MM2_STATE_REPO_TOKEN` and paste the token there. Never paste the token into chat, an issue, a commit, or a workflow file.

## Automatic migration and verification

The next scanner, weekly-report, dashboard, or API-connection workflow will:

- Authenticate to the private state repository without putting the token in the remote URL or logs.
- If its `state` branch already exists, use it.
- If it does not exist, copy the current public `state` branch to the private repository once, preserving the existing runtime checkpoints and SQLite history.
- Continue restoring and persisting state in the private repository.

Verify the workflow logs contain:

- `[state-remote] Migrated the existing state snapshot to the private repository.` on the first migration, or
- `[state-remote] Private state branch is available.` on later runs.

Also verify the scan and `persist-state` jobs both complete successfully and that the private repository now has a `state` branch.

## Remove the public copy only after verification

The migration intentionally does **not** delete the public `state` branch. After confirming successful private-state restore and persistence, delete the old `state` branch from the public scanner repository's **Settings → Branches** page. Do not delete it before the private copy is verified.

Deleting a public branch does not undo prior exposure: existing clones, forks, cached objects, or copies may retain its contents. Treat previously public state as already disclosed. The scanner's API key itself must remain only in GitHub Actions secrets; the state branch stores only a truncated key fingerprint and API-use ledger, never the key.

## Safe fallback

If `MM2_STATE_REPO_TOKEN` is absent, workflows continue using the legacy public `origin/state` branch so existing production behavior is not silently broken. This is a compatibility fallback, **not the final privacy configuration**. The final secure deployment is not complete until the private repository migration has been verified and the public branch removed.
