# Session 67 handoff — read this first

You are **Prime** in a multi-model pairing. **Sub** is Gemini 3.7 Flash (high) on Antigravity, on
the same working tree. The **Owner** relays between you by copy-paste.

Objective: finish the live execution loop. The strategy brain works; the hands do not exist yet.

## Where things stand

Branch `session-66-live-exec-loop`, tip `bd1f235`, tree clean, **739 tests passing**.

| Stage | What | Status |
| --- | --- | --- |
| 0 | Crash-safe relayer submit + audit log | **done**, `ecd4e5b` |
| 1 | `mergePositions` with pre-flight guards | **done**, `e006f2b` + `bd1f235` |
| 2 | Order registry + fill detection by polling | **next — directive written, not started** |
| 3 | Stop-loss / naked exit | specced in the architecture doc |
| 4 | Second-leg completion | specced |
| 4.5 | One supervised ~$1 live order | Owner approved the concept |
| 5 | Automated quoting | **out of scope this phase** |

Nothing has opened exposure. `redeem` and `merge` only close positions. Dry run is the default and
`--live` needs Owner sign-off per command.

## The three documents that matter

1. `.claude/reviews/STAGE-2-4-ARCHITECTURE.md` — the reviewed design. Source of truth.
2. `.claude/reviews/STAGE-2-DIRECTIVE.md` — hand this to Sub to start Stage 2.
3. `.claude/reviews/SESSION-66-BRIEF.md` — the phase framing and stage list.

**Directives R8 through R12 are gone.** They lived in the previous session's scratchpad, which does
not carry over. Everything load-bearing from them is in the three files above; nothing needs
recovering.

## How this pairing runs

**Prime writes architecture, specs, and approvals. Sub implements.** But when Sub's output is wrong
and you can fix it yourself, **fix it and move on** — do not open another correction round. That is
an explicit Owner instruction, and it is why `bd1f235` exists.

Corrections to Sub are short and framed as *general standing rules*, never post-mortems of the
specific mistake. Owner updates are terse: what changed, what is next. No relationship commentary,
no "what Sub got right" sections.

**Verify every load-bearing claim yourself before relaying it.** Sub has twice reported values it
did not compute — most recently a 64-character hash whose first 8 characters were correct and whose
remaining 56 were invented. The selector shipped was right; the evidence was not. Recompute hashes,
rerun the suite, decode the calldata. This costs less than a relay cycle and it has caught something
every single round.

## Sub's environment

Skills live at `~/.gemini/config/skills` plus `~/.gemini/config/plugins/`.

- **Available:** the full `superpowers` suite (14 skills), and the whole gstack suite — `review`,
  `cso`, `qa`, `investigate`, `plan-eng-review`, `careful`, `ship`, `land-and-deploy`.
- **Not available:** `ecc:*` and `compound-engineering:*`. Those lenses are yours. `cso` is a bash
  script and does not run under Sub's Windows shell — Sub reported that honestly, and the real
  security review moves to the phase-end PR review the Owner runs.

## Environment notes

- PowerShell on Windows for anything shown to the Owner. Sequence with `;`, never `&&`.
- A pre-commit hook blocks `strategy/` changes unless all four `research/` files are updated in the
  same commit: `RESEARCH_LOG.md`, `RESEARCH_SUMMARY.md`, and both `he_` Hebrew mirrors. The log
  convention is Question -> Method -> Result -> **Decision** (not "Verdict"), vocabulary
  DEAD / PARKED / LIVE / OPEN.
- `ruff` and `mypy` are not installed. Static analysis has never run on this repo.
- The Polymarket MCP the Owner connected was not visible in session 66 — MCP servers load at
  session start, so check for it now.
- A GateGuard hook demands a facts preamble before the first Bash call and before creating or first
  editing any file. Answer it and retry the same call; it is not a refusal.

## Verified venue facts, so you do not re-derive them

- `py_clob_client_v2` has **no** WebSocket client. `get_order` (`client.py:529`), `get_open_orders`
  (`:534`), `get_trades` (`:577`) all exist.
- Rate limits, from Polymarket's own reference: CLOB 9,000 req / 10 s; Data API `/trades`
  200 / 10 s; Relayer `/submit` 25 / 1 min. A 5-second poll uses 2 of 200. Not a constraint.
- The `/ws/user` channel is real and documented; auth is the three L2 credentials in plain JSON,
  which `_client()` already derives at `strategy/live_exec.py:90`. It is deferred, not rejected —
  polling is required for restart recovery either way.
- `mergePositions` selector `0x9e7212ad`, independently derived and cross-checked against
  `redeemPositions` -> `0x01b7037c`.

## First action

Hand Sub the Stage 2 directive:

```
Read .claude/reviews/STAGE-2-4-ARCHITECTURE.md then .claude/reviews/STAGE-2-DIRECTIVE.md, both in
full, before acting on any part of them. Pull bd1f235 first. Report in the shape section 6 specifies.
```

Then wait for Sub's report and review it against the acceptance bar in section 5.
