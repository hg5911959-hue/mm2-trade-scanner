#!/usr/bin/env bash
# Switch the workflow's state remote to a dedicated private repository.
# The public source repository remains available as "source" for one-time migration.
set -euo pipefail
set +x

state_repo="${MM2_STATE_REPO:-}"
state_token="${MM2_STATE_REPO_TOKEN:-}"
if [[ -z "$state_repo" && -z "$state_token" ]]; then
  echo "[state-remote] Private state repository is not configured; using the legacy origin/state branch."
  exit 0
fi
if [[ -z "$state_repo" || -z "$state_token" ]]; then
  echo "::error::Private state repository configuration is incomplete; refusing an ambiguous state fallback."
  exit 1
fi
if [[ ! "$state_repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "::error::MM2_STATE_REPO must be an owner/repository pair."
  exit 1
fi

if ! git remote get-url source >/dev/null 2>&1; then
  git remote rename origin source
fi
git remote set-url origin "https://github.com/$state_repo.git"

# Use a short-lived runner-local HTTP auth header rather than embedding the PAT
# in the remote URL or printing it in command output.
auth="$(printf 'x-access-token:%s' "$state_token" | base64 | tr -d '\n')"
git config http.https://github.com/.extraheader "AUTHORIZATION: basic $auth"
unset auth state_token

set +e
git ls-remote --exit-code origin refs/heads/state >/dev/null 2>/dev/null
private_status=$?
set -e
if [[ "$private_status" -eq 0 ]]; then
  echo "[state-remote] Private state branch is available."
  exit 0
fi
if [[ "$private_status" -ne 2 ]]; then
  echo "::error::Cannot access the configured private state repository. Check that it exists and the token has Contents read/write access."
  exit 1
fi

set +e
git ls-remote --exit-code source refs/heads/state >/dev/null 2>/dev/null
source_status=$?
set -e
if [[ "$source_status" -eq 2 ]]; then
  echo "[state-remote] Private repository is empty and no legacy public state branch exists; a cold start is required."
  exit 0
fi
if [[ "$source_status" -ne 0 ]]; then
  echo "::error::Cannot inspect the legacy source state branch; refusing to seed an incomplete private store."
  exit 1
fi

git fetch --no-tags source refs/heads/state:refs/remotes/source/state
git push origin refs/remotes/source/state:refs/heads/state
echo "[state-remote] Migrated the existing state snapshot to the private repository."
echo "[state-remote] The legacy public state branch has NOT been deleted; review and remove it only after verifying private-state operation."
