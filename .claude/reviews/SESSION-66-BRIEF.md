# Session 66 Brief — Build the Live Execution Loop

**Written:** 2026-08-16, at the close of Session 68 analysis.
**Branch:** `session-66-live-exec-loop` (already created, current with `session-65-ws-recorder`).
**Read this first. Everything the next session needs is here — do not re-derive it.**

---

## 1. THE OBJECTIVE, IN ONE PARAGRAPH

**The brain exists and works. The hands do not.** The decision engine already runs against real
live order books: it reads real books, decides when to quote, recognises when a pair is complete,
decides when to merge, and decides when to cut a one-sided position. That logic is written, tested,
and validated across 26,777 pairs of real market data. It is the hard part and it is finished.

What is missing is narrower than "four capabilities." No order was ever **sent**, so nothing was
ever built to talk to the venue about one:

| The decision | State | What is actually absent |
| --- | --- | --- |
| "my order filled" | works — inferred from the real tape | no code asks the venue *"did **mine** fill?"* — there was never an order to ask about |
| "cut this position" | works — fired correctly 16 times | no code **sends** the cancel or the sell |
| "merge this pair" | works — `merge.py` decides | no code **calls** the contract |

So Session 66 is **not** rebuilding strategy logic. It is wiring existing, proven decisions to the
venue — turning *"the model concluded I filled"* into *"the exchange told me I filled,"* and *"the
model booked a merge"* into *"the contract burned the pair and paid me $1.00."*

Anything that looks like re-deriving strategy behaviour is out of scope and is a sign of drift.

The build order below still holds, for one reason: **the moment orders become real, the exit must
be real too.** Otherwise the first one-sided fill sits there with nothing able to close it.

## 2. WHAT THE BUSINESS ACTUALLY IS

Buy one UP share and one DOWN share of the same market for a combined cost below $1.00. A complete
set is redeemable for exactly $1.00. The profit is `1.00 − pair_cost`, collected by calling
`mergePositions` on-chain **before** the market resolves. That is the entire business.

Measured, `run/fleet.db`, n=376 closes:

| method | realised PnL |
| --- | --- |
| `merge` | **+$926.85** |
| `naked_exit` | −$42.71 |
| `sell` | **$0.00** |

Net **+$884.13** over 26,777 pairs. `sell` — cross the book and capture the spread — has earned
nothing across the entire run.

**Do not re-open these. They are PARKED, and they all describe the `sell` path:**
`spread_capture_frac`, the `mid_h0`–`mid_h3` markout horizons, `tau_post`, the queue-decay haircut,
the 5.38 GB `books.db` BTC recording, and bankroll-size ranking. Three separate rounds of Session 68
were spent calibrating these before anyone checked which path earns. Pooled markout is **negative**
(−1.13¢/share, n_eff 260) — the sell path loses money, which is why `spread_capture_frac = 0.25`
stands and why every proposal to raise it was rejected.

## 3. THE NUMBERS THAT MATTER (all MEASURED, do not re-derive)

| Figure | Value | Source |
| --- | --- | --- |
| One-sided fills | 368 | `stats.pairs_ev()` |
| Completion rate | **95.65%**, Wilson 95% CI [93.05%, 97.31%] | same |
| Stop-loss exits | 16 (4.35%) | same |
| Gain per completed pair | 3.68¢ | same |
| Cost per exit | 3.67¢ | same |
| **EV per attempt** | **+3.36¢** | same |
| Break-even completion (uncoupled) | 49.93% | 3.67 / (3.68+3.67) |
| Break-even at 2× exit loss | **66.61%** | coupled surface — the two share a driver |
| Category mix (share of PnL) | sports 72.1%, e-sports 25.9%, crypto 2.0% | `closes` |
| Real fills, ever | **N_real = 0** | `fills.reason='sim'` on all 776 rows |
| Wallet balance | $0.99 (Owner can fund on demand) | `live_exec balance` |

**The exit mechanism works and is not a leak.** All 16 exits trace to one trigger,
`strategy/sweep.py:798-825`, firing when `pair_cost >= 0.995` (`max_pair_cost`). The other four risk
mechanisms never fired. Losing $42.71 against $926.85 gained is the stop-loss doing its job.

## 4. THE CAPABILITY GAP — THIS IS THE WORK

Read this table as *"the decision exists; the venue call does not."* Every `ABSENT` below is a
missing conversation with the exchange, not a missing strategy rule — the rule already ran 368
times against live books and produced the numbers in section 3.

| step | live path | evidence |
| --- | --- | --- |
| a. quote both legs | manual CLI only | `live_exec.py:171-214`; the automated loop is sim-only |
| b. detect one-leg fill | **ABSENT** | no fill listener, no user-channel stream, no order polling |
| c. complete second leg | **ABSENT** | no taker crossing |
| d. **merge at parity** | **ABSENT** | no `mergePositions` encoder or contract call anywhere |
| e. collect proceeds | post-resolution only | `redeem` works; pre-resolution merge cannot settle |
| f. stop-loss exit | **ABSENT** | `sweep.py:798-825` lives inside the simulator loop |

`mergePositions` occurs repo-wide **only** in `server/explainer_html.py:253,480` (marketing copy
claiming the bot calls it), `strategy/config.py:247` (a gas constant), and a test asserting that
marketing string. `strategy/merge.py` states it in its own header: *"Pure arithmetic. It decides;
the caller applies and persists."* It decides. Nothing applies.

`merge` and `redeem` are **different contract methods**. `redeem` claims after resolution
(`redeemPositions`, selector `0x01b7037c`, implemented and working). `merge` combines UP+DOWN into
$1.00 before resolution (`mergePositions`, not implemented).

## 5. BUILD ORDER — THE INVARIANT

**Never ship a stage that OPENS exposure before the stages that CLOSE it exist.**

| Stage | Capability | Effect on exposure | Status |
| --- | --- | --- | --- |
| **0** | Harden relayer submit (HIGH finding from PR #31) | hardens existing path | spec written, not started |
| **1** | `mergePositions` on-chain call | **closes** | spec written, not started |
| **2** | Fill detection | read-only | not specced |
| **3** | Stop-loss / naked exit on live path | **closes** | not specced |
| **4** | Second-leg completion (taker cross) | **closes** | not specced |
| **5** | Automated quoting loop | **OPENS** | out of scope this phase |

Stage 1 is first among the new capabilities because merging can only ever *reduce* exposure — it
converts a hedged position into cash. There is no state where merging leaves you worse off, gas
aside. Stage 5 does not get built until 1–4 are proven.

**Funding note:** the Owner can transfer funds on demand. Do not treat $0.99 as a constraint on
planning — but also do not request funding before stages 1–4 land. Money before the stop-loss
exists means a one-sided fill rides unhedged to resolution at up to 100% loss of that leg.

## 6. WORKFLOW — PRIME / SUB

Sub is **Gemini 3.7 Flash (high) on Antigravity**, full agent access, same working tree. Prime is
Claude Opus 5 in Claude Code. The Owner relays by copy-paste.

| Phase | Owner of phase | Skill |
| --- | --- | --- |
| Architecture + stage spec | **Prime** | `superpowers:writing-plans` |
| Plan the stage | **Sub** | `superpowers:writing-plans`, plan lands *before* the diff |
| Implement | **Sub** | `superpowers:test-driven-development` — red then green, both shown |
| Self-verify | **Sub** | `superpowers:verification-before-completion` |
| Stuck | **Sub** | `superpowers:systematic-debugging` — never patch around |
| Per-stage review | **Sub dispatches** | `ecc:python-review`; `ecc:security-reviewer` on any signing or contract code |
| Approve stage | **Prime** | reads diff + review output |
| Open PR at phase end | **Sub** | `ecc:pr` |
| Review the PR | **Owner runs** | `/code-review <PR#>` — catches interaction bugs the per-stage review cannot |
| Prime self-audit | **Prime** | `ecc:agent-self-evaluation` at each phase boundary |

**Directive style, learned this session:** numbered items, no rationale paragraphs, one italic
clause where the *why* prevents a wrong answer. Directives go in a file; the Owner pastes only a
short pointer prompt.

### 6b. Ceremony is not uniform across stages

Stages 0 and 1 are already specced, small, and exposure-closing. They need no planning ceremony —
build them. **Stages 2 through 4 are not specced and contain the real design forks**, so they get
the full treatment before a line is written.

| Phase | What runs |
| --- | --- |
| **Stages 0–1** | Nothing extra. Spec exists, scope is small, both are safe. Build, review, approve. |
| **Before Stage 2** | `superpowers:brainstorming` → Prime writes the Stage 2–4 architecture → `/plan-eng-review` **and** `/plan-devex-review` on that architecture → only then build |
| **Every stage** | TDD → `superpowers:verification-before-completion` → `ecc:python-review` (+ `compound-engineering:ce-code-review` as a second lens) → Prime approves |
| **Phase end** | `ecc:pr` → Owner runs `/code-review <PR#>` |

Why each of those is not optional at the Stage 2 boundary:

- **`brainstorming`** — fill detection has a genuine fork: WebSocket user-channel versus order
  polling. Different failure modes, different reconnect semantics, different state to persist
  across a restart. That is a design decision, not an implementation detail, and picking wrong is
  expensive to unwind once Stages 3 and 4 sit on top of it.
- **`/plan-eng-review`** — Stages 2–4 are where the risk concentrates. Reviewing the shape before
  the code is the cheapest possible place to catch a wrong one.
- **`/plan-devex-review`** — these stages produce a long-running process the Owner has to operate:
  start it, watch it, kill it, know what it did overnight. That is a first-class requirement here,
  not polish.

**One planning family only.** `superpowers` (`writing-plans` / `executing-plans`) and
`compound-engineering` (`ce-plan` / `ce-work`) both own plan-then-execute. Running both produces two
plan formats and two execution protocols for the same stage. **superpowers is the spine** — Sub
already ran `superpowers:verification-before-completion` successfully this session — and
`ce-code-review` is pulled in only as an additional review lens.

**Deliberately not used:** `/plan-ceo-review` asks *whether to build this*, which the Owner has
already decided; running it re-litigates a closed call. gstack `/review` would be a third PR-review
layer on top of `ecc:python-review` per stage and `/code-review` on the PR, which is already more
coverage than the work warrants.

**Standing rules for Sub, carried forward:**
1. Tag every figure MEASURED / DERIVED / ASSUMED. MEASURED means the measurement exists *at the
   stated resolution*.
2. Every decision-bearing figure carries an interval.
3. A verdict needs a test, not a range.
4. Before declaring anything unmeasurable, enumerate the data stores that exist.
5. Snapshot mutating data before analysing it — `run/fleet.db` is written live.
6. When a result contradicts your own earlier MEASURED figure, reconcile it explicitly.
7. When recommending an action that costs money, size it.

## 7. HOW PRIME FAILED THIS SESSION — DO NOT REPEAT

Self-evaluation scored 3.8/5. The three that cost real cycles:

1. **Analysed for four rounds before asking what generates the income.** One question in round 1 —
   *"which line item is the P&L?"* — would have skipped rounds 2–4 entirely. **Ask what makes the
   money before analysing how it is made.**
2. **Relayed a Sub figure without verifying it.** Told the Owner books update "once every 17
   minutes" from Gemini's 0.001/s; the real figure is 0.2466/s. **246× wrong.** Verify any number
   before it reaches the Owner.
3. **Presented a gate as authoritative without asking whether the Owner endorsed it.** Led a brief
   with "you are 3.6 days from go-live." The Owner's reply: *"I didn't approve that."*

## 8. REPO STATE AT HANDOFF

- **PR #31** open: `session-65-ws-recorder` → `main`, 72 files, +4,384/−14,504. My review posted
  (1 HIGH, 2 MEDIUM, 1 LOW; artifact at `.claude/reviews/pr-31-review.md`). CodeRabbit review was
  still running at handoff — **its comments must be resolved before merge.**
- **The HIGH:** `strategy/live_exec.py:486-498`, relayer submit unguarded, audit row written only
  on success. This is Stage 0.
- `session-66-live-exec-loop` exists and is current with `session-65-ws-recorder`.
- Working tree clean apart from `.claude/reviews/` artifacts.
- **716 tests pass.**
- Pre-commit hook blocks any `strategy/` change unless all four research files are updated
  (`RESEARCH_LOG.md`, `RESEARCH_SUMMARY.md`, and both `he_` Hebrew mirrors), Question → Method →
  Result → Verdict, vocabulary DEAD / PARKED / LIVE / OPEN. Budget for this on every stage.
- `.env` invariants: signer `0x3e69e2f2…Cbc1`, funder `0xed0b7d6a…b090`, **`POLY_SIG_TYPE=3`**.
  Types 0/1/2 report a truthful `$0.00` about a wallet that never existed — this has cost two
  sessions hours. Sweep `for s in 0 1 2 3; do POLY_SIG_TYPE=$s python -m strategy.live_exec balance;
  done` if a balance ever reads zero.
- Calendar gate retired from `go_live_readiness()` by Owner decision; status now
  `READY_FOR_SMALL_LIVE_PILOT`, which is **display-only** — every consumer is a dashboard renderer,
  nothing under `strategy/` reads it, and `live_exec.py` never consults it.

## 9. FILES THE NEXT SESSION WILL NEED

| Path | Why |
| --- | --- |
| `strategy/live_exec.py` | The only real-money path. Stages 0–4 all land here. |
| `strategy/merge.py` | The merge decision arithmetic. Do not change it — implement its caller. |
| `strategy/sweep.py:798-825` | The stop-loss to port to live (Stage 3). Reference only. |
| `strategy/fills.py` | `queue_ahead` derivation; reference for Stage 2. |
| `tests/test_live_exec.py` | Fixture style to follow. |
| `research/QUANT_QUESTIONS.md` | Q1, Q2, Q3 LIVE; Q4 PARKED. |
| `.claude/reviews/pr-31-review.md` | The HIGH finding, in full. |
| `AGENTS.md:65-70` | Workspace hygiene policy — delete scratch scripts. |

## 9b. CODERABBIT ITEMS DEFERRED FROM PR #31 — pick these up in Session 66

9 of 15 resolved on `session-65-ws-recorder` (commit `1ca28fc`). Six deferred, all verified real.
**Two of them matter for this session's work:**

| Item | Why it matters here |
| --- | --- |
| `scripts/audit_settlement.py` imports legacy `py_clob_client` while `requirements.txt` declares only `py_clob_client_v2` | The script fails at import in a clean environment and reports an error for every signature type instead of balances. This is the diagnostic you will reach for when a live balance looks wrong. |
| `scripts/audit_settlement.py` relayer-log reader is format-incompatible with `_log_order` | `_log_order` writes one pretty-printed JSON array (`indent=2`) with `"action": "REDEEM"` and the response under `"response"`. The reader does JSON-Lines parsing and matches `"redeem_positions"` / `"relayer_response"`, inside a bare `except: pass`. Section 4 therefore always prints `NO_RELAYER_TX_FOUND`. Stage 0 touches the same log file — fix both together. |

The other four sit in the PARKED latency path and can stay deferred: NTP sampling blocking the
recorder's event loop, `analyze_ws_staleness.py`'s broad `except` around its NTP fallback,
`websockets` undeclared in `requirements.txt`, and latency-override validation in
`strategy/config.py:780`. One comment was **declined** — removing the record of an accepted live
order from the research log would falsify the evidence base later verdicts rest on.

## 10. FIRST ACTION IN THE NEW SESSION

1. Check whether PR #31 merged and whether CodeRabbit's comments were resolved.
2. If merged: rebase `session-66-live-exec-loop` onto `main`.
3. Issue Stage 0 to Gemini (spec already written — see the R7 directive).
4. Do **not** start Stage 1 until Stage 0 is reviewed and approved.

**Open questions requiring the Owner, not the agents:** how much capital to fund once stages 1–4
land, and whether the first live pilot runs on one market or several.
