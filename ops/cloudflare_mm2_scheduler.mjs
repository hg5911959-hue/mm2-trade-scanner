const API_ROOT = "https://api.github.com";
const WORKFLOW_FILE = "scan-scheduler.yml";
const STALE_AFTER_MS = 15 * 60 * 1000;
const DISPATCH_COOLDOWN_MS = 10 * 60 * 1000;
const FAILURE_COOLDOWN_MS = 10 * 60 * 1000;

function parseTime(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function newest(runs, predicate) {
  return runs
    .filter(predicate)
    .sort((a, b) => parseTime(b.updated_at || b.created_at) - parseTime(a.updated_at || a.created_at))[0] || null;
}

async function githubJson(fetchImpl, url, headers, options = {}) {
  const response = await fetchImpl(url, { ...options, headers });
  if (response.status === 204) return null;
  if (!response.ok) {
    throw new Error(`GitHub API returned HTTP ${response.status} for ${url.replace(API_ROOT, "")}`);
  }
  return response.json();
}

export async function dispatchIfStale(env, options = {}) {
  const fetchImpl = options.fetchImpl || fetch;
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const repository = String(env.GITHUB_REPOSITORY || "").trim();
  const token = String(env.GITHUB_TOKEN || "").trim();

  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new Error("GITHUB_REPOSITORY must be an owner/repository pair");
  }
  if (!token) throw new Error("GITHUB_TOKEN secret is missing");

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "MM2-External-Scheduler",
  };
  const workflowUrl = `${API_ROOT}/repos/${repository}/actions/workflows/${WORKFLOW_FILE}`;
  const workflow = await githubJson(fetchImpl, workflowUrl, headers);
  if (workflow?.state === "disabled_manually") {
    return { action: "skip", reason: "manually_disabled" };
  }
  if (workflow?.state === "disabled_inactivity") {
    await githubJson(fetchImpl, `${workflowUrl}/enable`, headers, { method: "PUT" });
  } else if (workflow?.state !== "active") {
    return { action: "skip", reason: "unknown_workflow_state" };
  }

  const endpoint = `${API_ROOT}/repos/${repository}/actions/workflows/${WORKFLOW_FILE}/runs?branch=main&per_page=50`;
  const payload = await githubJson(fetchImpl, endpoint, headers);
  const runs = Array.isArray(payload?.workflow_runs) ? payload.workflow_runs : [];

  const active = runs.filter(run => run.status === "queued" || run.status === "in_progress");
  if (active.length) {
    return { action: "skip", reason: "active_run", active_count: active.length };
  }

  const recentDispatch = newest(runs, run =>
    run.event === "workflow_dispatch" && now - parseTime(run.created_at) < DISPATCH_COOLDOWN_MS
  );
  if (recentDispatch) {
    return { action: "skip", reason: "recent_dispatch", run_id: recentDispatch.id };
  }

  const latestFailure = newest(runs, run =>
    run.status === "completed" && run.conclusion !== "success"
  );
  if (latestFailure && now - parseTime(latestFailure.updated_at || latestFailure.created_at) < FAILURE_COOLDOWN_MS) {
    return { action: "skip", reason: "recent_failure", run_id: latestFailure.id };
  }

  const latestSuccess = newest(runs, run =>
    run.status === "completed" && run.conclusion === "success"
  );
  const lastSuccessAt = latestSuccess
    ? parseTime(latestSuccess.updated_at || latestSuccess.created_at)
    : 0;
  const ageMs = lastSuccessAt ? Math.max(0, now - lastSuccessAt) : Number.POSITIVE_INFINITY;
  if (ageMs < STALE_AFTER_MS) {
    return {
      action: "skip",
      reason: "recent_success",
      run_id: latestSuccess.id,
      age_minutes: Math.floor(ageMs / 60000),
    };
  }

  const dispatchUrl = `${API_ROOT}/repos/${repository}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  await githubJson(fetchImpl, dispatchUrl, headers, {
    method: "POST",
    body: JSON.stringify({
      ref: "main",
      inputs: {
        dry_run: "false",
        verify_rblxvalue_key: "false",
      },
    }),
  });
  return {
    action: "dispatched",
    reason: latestSuccess ? "stale_success" : "no_successful_run",
    last_success_age_minutes: Number.isFinite(ageMs) ? Math.floor(ageMs / 60000) : null,
  };
}

export default {
  async scheduled(controller, env) {
    const result = await dispatchIfStale(env);
    console.log(JSON.stringify({
      cron: controller?.cron || "manual",
      scheduled_time: controller?.scheduledTime || null,
      ...result,
    }));
  },

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return Response.json({ ok: true, service: "mm2-external-scheduler" });
    }
    return new Response("Not found", { status: 404 });
  },
};
