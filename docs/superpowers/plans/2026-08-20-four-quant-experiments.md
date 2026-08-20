# Four Quant Experiments Implementation Plan (#27, follow-up — NOT this PR)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the four open strategy questions from `research/QUANT_QUESTIONS.md` into four bounded, replayable experiments, each with a single deciding metric and its own research-log verdict.

**Architecture:** Four phases, ordered. Phase 1 (queue haircut) must land and be calibrated before Phases 2 and 3 are judged; Phase 4 is independent but its metric is scored in the post-haircut currency. Each phase lands behind an existing seam (fills crediting path, sweep interface, time-to-resolution parameter, fetch seam) and ships a replay command, a research entry in all four `research/` files, and a keep/discard verdict.

**Tech Stack:** Python simulation tree (`strategy/`, `scripts/`), `pytest` at repo root, the existing recording harness (recorded books + trade tape).

**Scope note (user decision 2026-08-20):** #27 is NOT part of the live-visibility PR. Phase 1 already exists on three unmerged branches:
- `session-62-amendment-penalty` — `strategy/fills.py` amendment penalty + `tests/test_fills.py`
- `session-63-cancel-race-model` — cancel-race estimator + `scripts/eval_tau_sensitivity.py`
- `session-64-post-latency-penalty` — latency penalty + `scripts/eval_post_latency_sensitivity.py`

## Global Constraints

- Simulation only. No phase places a real order. No change to the allocator's water-fill or per-market concentration cap; no reward-market changes unless a replay shows impact.
- Each phase ships research entries in all four files: `research/RESEARCH_LOG.md`, `research/RESEARCH_SUMMARY.md`, `research/he_RESEARCH_LOG.md`, `research/he_RESEARCH_SUMMARY.md` (Hebrew mirrors the English).
- Verdict vocabulary: DEAD / PARKED / LIVE / OPEN. Negative results are kept.
- Each phase takes the next free session number; no new ID sequence.
- Every phase's deciding metric is produced by a replay command, not a live run.
- A phase whose verdict is LIVE archives the live DB and starts a fresh sample (config must not mix in one dataset).
- Run one instance at a time. A LIVE phase must not run a second bot instance against the same database — concurrent bots writing one database sum their independent inventories into silently invalid data.
- Phase 1's three haircut components are calibrated **independently** from data; a single joint global discount is forbidden. The markout guard can veto a keep.

---

### Task 1: Merge/complete Phase 1 (queue haircut) from the session branches

**Files:** reconcile `session-62/63/64` onto a `gflow/issue-27-phase1` branch off current `main`; resolve conflicts; `strategy/fills.py`, `strategy/config.py`, `tests/test_fills.py`, `scripts/eval_tau_sensitivity.py`, `scripts/eval_post_latency_sensitivity.py`.

- [ ] Rebase/merge the three session branches in order (62 → 63 → 64) onto a fresh branch off `origin/main`.
- [ ] Resolve conflicts, preserving each branch's intent (amendment penalty, cancel race, latency penalty).
- [ ] Run `pytest -q tests/test_fills.py` at root; fix what the merge broke.
- [ ] Write the calibration amendment entry in all four research files naming which prior profit numbers are superseded and by how much.
- [ ] Commit: `feat(strategy): queue-decay haircut — amendment penalty, cancel race, latency penalty (#27 phase 1)`.

### Task 2: Phase 1 deciding metric — fill-rate gap + markout guard

- [ ] Add the replay command that reports simulated fill rate vs the rate measured against real queue depth on identical windows.
- [ ] Add the post-fill markout guard split into adverse/benign populations; guard vetoes a keep when the surviving fill population shrank without getting worse.
- [ ] Tests: fixtures where tape prints before modelled cancel-ack produce a race loss; the markout guard flags a haircut that shrinks both populations equally.
- [ ] Research entries + verdict (keep/discard). Commit.

### Task 3: Phase 2 — skew vs pairs-only replay

- [ ] Replay driving both policies through the existing sweep with config-selected policy; 2×2 matrix (skew only / pairs-only / both / neither) over identical windows.
- [ ] Opportunity-set PnL (denominator = all scanned eligible markets) as primary metric; unit edge (cents/share) as veto; activity quorum (≥20% of pool AND ≥500 shares) as disqualifier → `DEAD (insufficient activity)`.
- [ ] Heavy-side withdrawal threshold swept, not chosen; exposure-reducing order invariant preserved.
- [ ] Tests for metric arithmetic (both failure directions), quorum boundaries, light-side-still-quotes invariant.
- [ ] Research entries + verdict. Commit.

### Task 4: Phase 3 — terminal-boundary scaling

- [ ] Risk aversion, holding horizon, and book-gate thresholds become config-sourced functions of time-to-resolution (current constants = far-from-resolution values).
- [ ] Single named handover boundary in config; sweep refusal names time-to-resolution when binding.
- [ ] Replay against recorded markets that actually resolved; metric = profit per market held into the final day (post-haircut).
- [ ] Tests: pinned clock stepping toward resolution asserts threshold transitions + named refusal.
- [ ] Research entries + verdict. Commit.

### Task 5: Phase 4 — cross-venue toxic-flow breaker (DEFERRED)

> **Deferred.** Phase 4's research verdict is already `PARKED`: zero sub-second book gaps across 762,911 samples (min inter-update 1,194.5 ms). A sub-50 ms breaker cannot act on a book that cannot change faster than 1.19 s. Do not implement the breaker or its replay until a fast series (e.g. BTC 5-minute, 630 updates/s) enters the traded universe; re-open the issue at that point. The spec below is the plan for when that happens.

- [ ] External reference-price adapter behind the fetch seam (parse half counts malformed rows; structurally wrong payload raises fetch-shaped).
- [ ] Fusion of lead-lag momentum + order-flow imbalance + book-skew velocity into one cancel-all decision naming the dominant signal; fail-open on absent feed; fail-closed at startup on region misconfig.
- [ ] Every trip recorded (inputs + dominant signal + cooldown), surfaced via the state reader.
- [ ] Replay threshold sweep; metric = adverse-selection cost avoided minus spread income forgone.
- [ ] Tests: fusion as a pure function (including absent-reference case + cooldown, no network); breaker-trip dashboard exposure via state reader.
- [ ] Research entries + verdict. Commit.

---

## Self-Review

**Spec coverage:** Phases 1–3 map to Tasks 1–4; Phase 4 is deferred (Task 5) pending qualifying sub-second book-gap data, matching its recorded `PARKED` verdict rather than planning an executable path against a book that cannot change fast enough. Cross-cutting (state reader, write module, four research files, session numbering, no live orders, one-instance rule) is in Global Constraints.

**Placeholder scan:** No TBD/TODO; each task names files, metric, and test intent. Full code is not repeated here because issue #27's body already contains the implementation + testing decisions verbatim; implementers read the issue as the source of truth and this plan as the sequencing.

**Type consistency:** Phase 1 seam is the single fills-crediting path (`strategy/fills.py`); Phase 2 seam is the sweep interface; Phase 3 seam is the existing time-to-resolution parameter; Phase 4 seam is the fetch seam. Names deferred to the issue body, which is authoritative.

## GSTACK REVIEW REPORT

| Runs | Status | Findings |
|------|--------|----------|
| Self-review (coverage, placeholders, consistency) | PASS | 0 unresolved |
| Scope review | PASS | Phases sequenced; existing session branches surfaced as the Phase 1 start point |

**VERDICT: CROSS-MODEL absorbed — sequencing is implementable; Phase 1 resumes from the unmerged session branches.**

NO UNRESOLVED DECISIONS
