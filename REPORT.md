# MM2 Market Intelligence — Engineering Report

> **Historical record:** this report documents an earlier engineering phase and its original baseline. It is not the current release-readiness or operational status. For current behavior, see [README.md](README.md), the [operations runbook](docs/OPERATIONS.md), and the active pull requests in GitHub.

## Baseline and phase log

- Baseline main commit: `47ef8b93346d156d9af34aa53c7f84b754bd1998`
- Baseline test suite: **387 tests**, passed twice on Python 3.12 in GitHub Actions run [#43745530514](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43745530514).
- Both runs checked `git status --porcelain data`; the workflow passed.
- Standard library only; no paid services or new secrets introduced.
- Phase 0: baseline recorded. **Open operational limitation:** the available GitHub integration can create/update files and branches, but exposes no tag-object/ref creation operation. I will not create a branch named `before-v13` and misrepresent it as a tag. The required pushed tag `before-v13` remains uncreated and needs a GitHub tag-write capability or maintainer action.

## Phases

| Phase | Status | Commit |
|---|---|---|
| 0 — Safety/baseline | Baseline recorded; tag blocked by missing tag-write operation | `c16165ba07f33b20348201e4ed2729b12a08f401` (report); test assertion `ef452658e30cff20fa0a29f1bed316dc9c5f4039` |\n| 1 — Pause USD feature | Complete: default-off code audited; added integration assertions for mode summary, no catalogue/snapshot access, no maintenance notice, and value-only alert. | `9807dc54ec0ee32b2c59a68561a6527883076528`; report `e698c91970d86c1ac91b62620cb8f2ecdd380c38` |

- Phase 1 verification: **388 tests passed twice** (Python 3.12, full Ruff checks green) in Actions run [#43746330397](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43746330397).\n- Cash remains disabled by default. The Supreme/game-value path remains active and does not consult cash prices.\n\n- Phase 2 verification: **390 tests passed twice**; data-tree hashes remained identical and `git status --porcelain data` was empty in both passes. Actions run [#43746531576](https://github.com/hg5911959-hue/mm2-trade-scanner/actions/runs/43746531576).\n\n## Environment variables

- `CASH_FILTER=false` by default; accepted true values: `1`, `true`, `yes`, `on` (case-insensitive).

## Changed files

- Phase 0: `REPORT.md`, `tests/test_workflow_config.py`.\n- Phase 1: `tests/test_cash_filter_modes.py` (existing default-off integration coverage strengthened).\n- Phase 2: `.github/workflows/tests.yml`, `tests/test_no_data_pollution.py`, `tests/test_workflow_config.py`.

## Open questions / blockers

1. Who or what can create and push the annotated/lightweight Git tag `before-v13` at the baseline commit? Current integration has no tag creation operation.
2. The user requests Telegram command search with bounded freshness and a live scan process. This repository currently runs bounded GitHub Actions jobs; command responsiveness outside an active job cannot be guaranteed without changing the hosting/transport model. No alternative service will be introduced without an explicit supported architecture.

## Self-assessment

Not yet scored. Scores will be evidence-based after the corresponding phases and tests are complete.
