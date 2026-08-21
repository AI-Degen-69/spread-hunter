# Rebase and Integrate PR #63 (Deepen Live Engine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cleanly merge PR #64, rebase PR #63 (`deepen-live-engine-architecture`) on current `main`, and preserve all 5 safety/operational features from PRs #67–#74 without regression.

**Architecture:** PR #63 extracts live engine modules ([settlement.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/spread-hunter/live/engine/settlement.py), [registry_state.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/spread-hunter/live/engine/registry_state.py), [venue.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/spread-hunter/live/engine/venue.py), `quotes.py`). During rebase, the 5 features from PRs #67–#74 are ported into the new modular interfaces.

**Tech Stack:** Python 3.11, pytest, SQLite, Polymarket CLOB / EIP-712.

## Global Constraints
- `AGENTS.md` rules: Research logs must be kept in sync on live engine changes (`research/RESEARCH_LOG.md`, `research/RESEARCH_SUMMARY.md`, and Hebrew mirrors).
- Live tree isolation: `live/` must not import from root `strategy/`.
- Pre-approved closing verbs remain intact; dry-run default is preserved.

---

## Proposed Tasks

### Task 1: Merge PR #64 (ADR-0001)

**Files:**
- Doc: `docs/adr/0001-live-fleet-skips-usd-depth-gate.md`
- Comment: `live/engine/config.py`

- [ ] **Step 1: Merge PR #64 via GitHub CLI**
  `gh pr merge 64 --squash --delete-branch`
- [ ] **Step 2: Update local main**
  `git checkout main && git pull origin main`

---

### Task 2: Rebase `deepen-live-engine-architecture` on `main` & Resolve File Conflicts

**Files:**
- Modify: `live/engine/live_exec.py`
- Modify: `live/engine/live_fleet.py`
- Modify: `live/engine/order_registry.py`
- Modify: `live/engine/live_pairs.py`
- Modify: `live/dash/live_dash.py`
- Modify: `live/engine/registry_state.py`
- Modify: `live/engine/venue.py`

- [ ] **Step 1: Checkout branch and start rebase**
  `git checkout deepen-live-engine-architecture && git rebase main`
- [ ] **Step 2: Port Guardrail Watcher Child-Process Supervision into `live_exec.py`**
  Ensure `poll` loop launches and supervises `live/scripts/guardrail_watch.py` as a child process.
- [ ] **Step 3: Port Auto Naked-Leg Exit (U35 Pass) into `live_exec.py`**
  Ensure automated exit pass runs during poll loops.
- [ ] **Step 4: Port Defect Fixes & Sync Convergence into `order_registry.py` & `live_pairs.py`**
  Retire phantom rows on sync and keep `max_pair_cost` check and exit PnL persistence.
- [ ] **Step 5: Port Guardrail Alert Banner & Watcher Health Widget into `live_dash.py`**
  Integrate dashboard alert banners with the new `registry_state.summarize_state()` caller.

---

### Task 3: Test Verification and Research Log Sync

**Files:**
- Test: `live/tests/test_rc_fixes.py`
- Test: `live/tests/test_auto_pairs.py`
- Test: `live/tests/test_guardrail_watch.py`
- Test: `live/tests/test_sync_convergence.py`
- Test: `live/tests/test_registry_state.py`
- Test: `live/tests/test_settlement.py`
- Modify: `research/RESEARCH_LOG.md`
- Modify: `research/RESEARCH_SUMMARY.md`
- Modify: `research/he_RESEARCH_LOG.md`
- Modify: `research/he_RESEARCH_SUMMARY.md`

- [ ] **Step 1: Run live suite**
  `cd live && pytest -q`
- [ ] **Step 2: Run root simulation suite**
  `pytest -q`
- [ ] **Step 3: Update research logs to satisfy pre-commit hook**
  Update all 4 research logs with the rebase details.
- [ ] **Step 4: Force-push rebased branch to GitHub**
  `git push origin deepen-live-engine-architecture --force-with-lease`
- [ ] **Step 5: Merge PR #63**
  `gh pr merge 63 --squash --delete-branch`
