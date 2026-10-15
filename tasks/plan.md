# Plan — #608 [TEST] 10-profile shadow tournament on shipped defaults

Branch: i608/10-profile-shadow-tournament | Issue: #608
Assignees: AI-Degen-69 | Labels: ready-for-agent | Base: main

## Tier & type

**Standard** — one data file (10 arms) + a frontend refresh across two servers
(cockpit, hub) + a validation test. No strategy-core changes, no new deps,
shadow-only. Helper skills: `incremental-implementation`, `test-driven-development`,
`frontend-ui-engineering` (cockpit/hub cards).

## Intake (Step 0A) — seams verified against live code at planning time

Every line below was opened and confirmed on this branch (`main` @ 9daf33f):

- Launcher `scripts/shadow_tournament.py`: `--arms-file` (:558), custom
  `HUNTER_*` env (:303-313), full-preset selector `HUNTER_TOURNAMENT_PRESET`
  (:317-318). **No `--issue`→arms-file lookup** — omitted `--arms-file` falls
  back to the 4 `TOURNAMENT_PRESETS` (:276-281). This is the fork in Step 5.
- All 10 character knobs are wired in `core_brain/config.py`:
  `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` (:1566), `HUNTER_SINGLE_BUY_GRACE_SEC`
  (:1548), `HUNTER_DYNAMIC_OFFSET_MULT/MAX_CENTS` (:1621/:1629),
  `HUNTER_REQUOTE_DEAD_BAND` (:1473), `HUNTER_REQUOTE_HOLD_BELOW` (:1480),
  `HUNTER_REQUOTE_HOLD_QUEUE` (:1487). Arm-name pattern `^[a-z0-9-]{1,24}$`
  (shadow_tournament.py:292) — all 10 character slugs fit.
- Cockpit `scripts/tournament_cockpit_server.py` already computes and returns,
  per arm, the four metrics the swap promotes: `profit_factor` (:816),
  `var_95` (:822), `sharpe_proxy` (:823), `mc_p5` (:824). Column swap is
  **frontend-only**: header (`:1298-1313`) + row-builder (`:1588-1610`).
  HTML is generated live by `generate_cockpit_html` (:924) on every request and
  snapshotted to `reports/tournament_cockpit_*.html` at server start (:1966) —
  no new endpoint. Cross-arm sums already computed at `:844-857`.
- Cockpit serves a tournament by `TOURNAMENTS[tourney_key]` (:484-520) resolved
  through `resolve_tournament_arms`. To serve #608, add `TOURNAMENTS["608"]`
  + `ARMS_CONFIG_608` with **empty** catalyst columns (filled from the run, per
  the existing #492 precedent comment at :331-338). Do NOT reuse the stale
  #484/#487 forensics (:42-329).
- Hub `scripts/tournament_hub.py`: cards render `#issue · name` + stop/offset/
  run-id/cycle/fills/pnl (:260-392); hardcoded 5-name `ARMS_ORDER` (:324) caps
  sort at the first five arms — must become plan-order for 10 arms.
- Grace-vs-stop settled from code (`single_buy_saver.py:1992-2064`): adverse
  stop fires BEFORE the grace hold; shipped grace 0.0 means the hold never
  engages. Grace governs only calm legs. No issue text contradicts this.

CodeRabbit plan: none on #608 (issue is fresh). No `[UNVERIFIED]` items remain.

## Spec (concise, no SPEC.md — Standard embeds it here)

**Goal:** a runnable 10-character shadow plan, a cockpit/hub that serves it,
and a shared Data Analysis verdict per arm. Shadow only — no signer, no venue
writes, `data/orders.db` untouched.

**Out of scope:** live-money pilot (separate issue/decision); strategy-code
changes; the #487 tweak list (superseded by these characters); auto-resolving
arms from `--issue` (see Step 5 opt-in).

**Acceptance bar (the runnable one):**
`python -m scripts.shadow_tournament --issue 608 --arms-file configs/tournaments/i608_tournament-1_arms.json --minutes 720 --run-number 1 --dry-run`
prints a valid 10-arm plan and exits 0. (This is #608's acceptance line plus the
explicit `--arms-file`; see Step 5 for why the bare `--issue` form is deferred.)

## Step 5 — Improvement proposal (one, opt-in)

**Evidence (verbatim):** #608 acceptance line — "`python -m scripts.shadow_tournament --issue 608 --minutes 720 --run-number 1 --dry-run` prints a valid 10-arm plan" — against the launcher code showing no issue→arms lookup (`shadow_tournament.py:276-281` falls back to 4 presets when `--arms-file` is omitted).

**Proposal:** teach the launcher to resolve `configs/tournaments/i<issue>_*_arms.json`
when `--issue` is given and `--arms-file` is not — the lookup #492's reviewer
recommended and never shipped.

**Classification: scope expansion (opt-in only).** The plan defaults to the
simpler path (pass `--arms-file` explicitly; zero launcher changes). The lookup
would touch `shadow_tournament.py` + `tests/test_shadow_tournament.py` and is
built **only on operator approval.** Rejection recorded here so it doesn't
resurface: defaulting to explicit `--arms-file` keeps #608 shadow-scoped.

## Task decomposition (risk-first)

Depends-on graph: T1 (arms file) feeds T4 (dry-run) and T5 (tests). T2/T3
(cockpit/hub) are frontend-only, independent of the specific arms, so they run
in parallel with T1; T5 asserts all of them last.

- [ ] **T1 [S] [Backend/Logic]** — `configs/tournaments/i608_tournament-1_arms.json`
  : the 10 characters, one `HUNTER_*` env delta each, matching #608's table.
  Slugs: the-accountant, the-patient-one, the-surgeon, the-daydreamer,
  the-regular, the-metronome, the-gambler, the-chaperone, the-graceful,
  the-strict-parent. Depends on: —. Verify: JSON parses; each arm's env has
  exactly the one knob #608 names (or two for the-gambler/the-strict-parent,
  which #608 defines as two-knob); names match `^[a-z0-9-]{1,24}$`.

- [ ] **T2 [M] [Design/UI]** — cockpit refresh in `generate_cockpit_html`
  (`tournament_cockpit_server.py`): (a) add `TOURNAMENTS["608"]` +
  `ARMS_CONFIG_608` (empty catalyst columns); (b) leaderboard header (:1298)
  + row-builder (:1588) swap Quoted Orders→Sharpe, Merged-legs→Profit Factor,
  add VaR95 + Monte-Carlo P5, keep Stop-Loss Exits; (c) aggregate strip above
  the leaderboard from existing sums (:844-857) — realized PnL, closes-weighted
  win/merge %, best-minus-worst, NO_DATA when DBs missing; (d) guard the stale
  #484/#487 forensics out of the default render. Depends on: —. Verify: server
  starts, `/` renders the 608 table with new columns; `pytest tests/test_dashboard_server.py`
  (or the cockpit's own test if one exists) still green.

- [ ] **T3 [S] [Design/UI]** — hub refresh `scripts/tournament_hub.py`: replace
  hardcoded `ARMS_ORDER` (:324) with plan-index order so all 10 arms sort
  correctly; surface worst-catalyst + next-run tweak on each card when present.
  Depends on: —. Verify: `python -m scripts.tournament_hub` serves, cards show
  10 arms in plan order.

**Checkpoint** — after T1-T3: arms file parses, cockpit + hub render #608 with
new columns/order. One-line progress report; no approval pause in Mode A.

- [ ] **T4 [XS] [Backend/Logic]** — dry-run gate: run the acceptance command
  and confirm a valid 10-arm plan + exit 0. Depends on: T1. Verify: the
  acceptance command in the Spec bar.

- [ ] **T5 [M] [Backend/Logic]** — tests: extend `tests/test_shadow_tournament.py`
  with a case that loads the i608 arms file through `build_tournament_plan` and
  asserts 10 arms, unique names, ports 8801-8810, run-number slugs
  `<base>-1`, and one-knob-per-arm where #608 says one knob. Depends on: T1,
  T2. Verify: `python -m pytest -q tests/test_shadow_tournament.py`.

## Constraints

- Zero regressions: touched-file suites pass (`test_shadow_tournament.py`,
  `test_dashboard_server.py`, hub/cockpit tests if present).
- No new dependencies. No live calls, no signer, `data/orders.db` untouched.
- No hardcoded forensics: catalyst columns empty until the run reports them.
- Anti-cheat: no skipped/disabled tests, no deleted assertions.

