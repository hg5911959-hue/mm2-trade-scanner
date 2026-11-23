import test from "node:test";
import assert from "node:assert/strict";
import { dispatchIfStale } from "../ops/cloudflare_mm2_scheduler.mjs";

const now = Date.parse("2026-11-23T17:00:00Z");
const env = {
  GITHUB_REPOSITORY: "hg5911959-hue/mm2-trade-scanner",
  GITHUB_TOKEN: "test-token",
};

function response(body, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function mockFetch(runs, calls = [], workflowState = "active") {
  return async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith("/actions/workflows/scan-scheduler.yml/enable")) {
      return response(null, 204);
    }
    if (String(url).endsWith("/actions/workflows/scan-scheduler.yml")) {
      return response({ state: workflowState });
    }
    if (String(url).endsWith("/runs?branch=main&per_page=50")) {
      return response({ workflow_runs: runs });
    }
    if (String(url).endsWith("/dispatches")) {
      return response(null, 204);
    }
    throw new Error("Unexpected GitHub API request");
  };
}

test("skips dispatch while a scanner run is active", async () => {
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([{ id: 1, status: "in_progress", event: "schedule" }]),
  });
  assert.equal(result.reason, "active_run");
});

test("skips dispatch after a recent successful scan", async () => {
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([{
      id: 2, status: "completed", conclusion: "success",
      updated_at: "2026-11-23T16:55:00Z",
    }]),
  });
  assert.equal(result.reason, "recent_success");
  assert.equal(result.age_minutes, 5);
});

test("skips dispatch during a recent dispatch cooldown", async () => {
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([{
      id: 3, status: "completed", conclusion: "failure",
      event: "workflow_dispatch", created_at: "2026-11-23T16:55:00Z",
      updated_at: "2026-11-23T16:56:00Z",
    }]),
  });
  assert.equal(result.reason, "recent_dispatch");
});

test("backs off after a recent failed run", async () => {
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([{
      id: 4, status: "completed", conclusion: "failure",
      event: "schedule", updated_at: "2026-11-23T16:55:00Z",
    }]),
  });
  assert.equal(result.reason, "recent_failure");
});

test("dispatches a live run when the latest success is stale", async () => {
  const calls = [];
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([{
      id: 5, status: "completed", conclusion: "success",
      event: "schedule", updated_at: "2026-11-23T16:30:00Z",
    }], calls),
  });
  assert.equal(result.action, "dispatched");
  assert.equal(result.reason, "stale_success");
  assert.equal(result.last_success_age_minutes, 30);
  assert.equal(calls.length, 3);
  assert.equal(calls[2].options.method, "POST");
  const body = JSON.parse(calls[2].options.body);
  assert.equal(body.ref, "main");
  assert.equal(body.inputs.dry_run, "false");
  assert.equal(body.inputs.verify_rblxvalue_key, "false");
});

test("dispatches when the repository has no successful scanner run", async () => {
  const calls = [];
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([], calls),
  });
  assert.equal(result.action, "dispatched");
  assert.equal(result.reason, "no_successful_run");
  assert.equal(calls.length, 3);
});

test("respects a manually disabled scanner workflow", async () => {
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([], [], "disabled_manually"),
  });
  assert.equal(result.reason, "manually_disabled");
});

test("re-enables a workflow disabled by inactivity before dispatch", async () => {
  const calls = [];
  const result = await dispatchIfStale(env, {
    now,
    fetchImpl: mockFetch([], calls, "disabled_inactivity"),
  });
  assert.equal(result.action, "dispatched");
  assert.equal(calls[1].options.method, "PUT");
  assert.equal(calls[2].url.endsWith("/runs?branch=main&per_page=50"), true);
  assert.equal(calls[3].options.method, "POST");
});

test("refuses to run without the dedicated GitHub token", async () => {
  await assert.rejects(
    dispatchIfStale({ GITHUB_REPOSITORY: env.GITHUB_REPOSITORY }, {
      now, fetchImpl: mockFetch([]),
    }),
    /GITHUB_TOKEN secret is missing/,
  );
});
