# PROGRAM.md — the research org code

This is spread-hunter's `program.md`, the autoresearch adaptation
([karpathy/autoresearch](https://github.com/karpathy/autoresearch)). Its
premise: research speed comes from the **agent instructions**, not the
code — "you are programming the program.md files that provide context to
the AI agents and set up your autonomous research org." The strategy files
are what experiments modify; this file is how experiments are run. Iterate
on THIS file when research feels slow — it is the loop's code, and it is
deliberately a bare-bones baseline.

## The loop

One experiment = one change, run against a **fixed budget**, judged by
**one metric**, kept or discarded by that metric, and logged. The repo
already has the two pieces autoresearch builds on:

- **The fixed budget = the replay harnesses.** A strategy change replays
  against recorded books/trades in minutes
  (`scripts/replay_risk_gates.py`, `scripts/trial_depth_gate.py`,
  `scripts/mirage_triage.py`, `scripts/record_books.py` +
  `scripts/measure_fill_rate.py`). This is our "5-minute training run."
  **Replay first, always, before anything live.**
- **The log = keep/discard.** `research/` with the Question → Method →
  Result → Verdict contract in AGENTS.md. Verdicts are decisions
  (DEAD / PARKED / LIVE / OPEN); negative results are kept, never deleted.

## The experiment card

Every strategy or knowledge experiment gets one card **before** the change:

    Question.      What exactly is being asked?
    Single metric. THE number that decides. One, named in advance.
    Keep/discard.  If the metric moves this way → keep; else → discard/revert.
    Budget.        Replay first; a fresh live sample only if the replay passes.
    Verdict.       DEAD / PARKED / LIVE / OPEN.

Rules that follow:

1. Name the single metric before you run. If you cannot, you are not ready.
2. One experiment per card. Two changes together cannot be read.
3. A strategy change that passes replay invalidates the live sample —
   archive and start fresh (AGENTS.md).
4. If the change does not move the metric, discard it. Keeping a null
   result as a log entry is the point; keeping it as live code is not.
5. Instrumentation bugs get their own card, always — they have repeatedly
   been the difference between a real finding and a fake one.

## Scope forms

- **Strategy / knowledge:** the full card above.
- **Design/UI only** (`server/spread_dash*`): the operator-approved minimal
  one-line entry — visual reskin only, no behavior change, all numbers from
  existing `strategy.stats` / `fleet_dash.fleet()`.

## IDs — one canonical scheme

The log grew two parallel session sequences plus U/KTD/C/issue tags and
bare dates. Going forward:

- Full cards take the **next free `Session N`** number.
- The U-series stays as the change-set tracking tag (U1…U36g).
- Issue numbers stay for GitHub issues; KTD/C stay for their reviews.
- Design-only entries keep the dated minimal form.

Do not open a third sequence — reuse `Session N`.

## Quick start for an agent

1. Read AGENTS.md (conventions, safety, log contract) and CONTEXT.md.
2. Pick ONE scope; write the experiment card first.
3. Run the replay harness for the change; keep/discard by the metric.
4. Log it (all four research files: EN + HE log, EN + HE summary).
5. Stop — one card per pass.
