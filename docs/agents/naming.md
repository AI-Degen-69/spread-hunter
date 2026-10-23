# Names and paths

One grammar per kind of artifact. Two grammars total, chosen by one question:
**does the name point at a thing (an issue, a run) or at a moment (a date)?**

Anything a human opens and reads is a **document**. Anything a process writes
and a program reads back is **state**. Documents live under `docs/`, state lives
under `data/`, `runtime/` and `reports/`.

## G1 — Identity grammar (documents about an issue or PR)

```
<id>-<kind>[-<slug>].<ext>        docs/issues/
```

- `<id>` is the issue or PR number, bare: `655`.
- `<kind>` is a closed vocabulary: `presentation`, `showcase` (legacy),
  `noticed-but-not-touching`, `audit`.
- `<slug>` is optional when the kind is self-explanatory.

| Name | What it is |
| --- | --- |
| `655-presentation-rebate-settled.html` | visual showcase of what shipped for #655 |
| `655-noticed-but-not-touching.md` | out-of-scope ledger for #655 |
| `355-audit-matchup-refusal.md` | generated audit of #355's question |

The id comes first because that is how the operator looks for the file, and
because Station VI commits showcases by id. Never `rebate-settled-655.html`.

## G2 — Moment grammar (records that happened on a date)

```
<YYYY-MM-DD>-<kind>[-<id>-]<slug>.<ext>        docs/runs/, docs/audits/, docs/superpowers/specs/
```

- `<kind>` is a closed vocabulary: `run`, `tournament`, `audit`, `spec`.
- The issue id follows the kind when the record is about an issue, so it stays
  secondary to the date: `2026-10-16-run-622-refusal-hold.md`.
- The date is the day the run **started**, not the day the note was written.

Undated documents (`docs/ideas/`, `docs/reference/`, `docs/agents/`) are the
plain kebab-case slug with no prefix: `short-window-ladder.md`,
`sports-game-status-values.md`.

## G3 — State grammar (generated stores, tapes, reports)

```
<kind>_<identity>_<stamp>.<ext>         data/, runtime/, reports/
```

- `<kind>` first: `stats`, `statistics_report`, `shadow`, `tournament`.
- `<identity>` is the run id, arm name, or other discriminator, sanitized to
  `[A-Za-z0-9.-]`.
- `<stamp>` is `YYYY-MM-DDTHH-MM` — **one stamp format everywhere**, produced by
  `core_brain/naming.stamp()`, never formatted by hand. ISO 8601 basic with a
  hyphen in the time because Windows forbids `:`. It carries the year and it
  sorts; the old `%d-%m_%H-%M` did neither, so `21-10` sorted after `04-11` and
  two Octobers collided.

| Name | What it is |
| --- | --- |
| `stats_shadow-01_2026-10-23T08-47.db` | statistics store for one observed run |
| `statistics_report_shadow_shadow-01_2026-10-23T08-47.md` | that run's report |
| `statistics_report_market_metrics_2026-10-23T08-47.json` | research output |
| `shadow_shadow-01.jsonl` | the run's own event ring |
| `orders.db`, `price_tape.db`, `demo_orders.db`, `processes.json` | singletons: no identity, no stamp |

A singleton is a store there is exactly one of by definition. It gets a bare
`<kind>.<ext>` and never a date — a date on `orders.db` would mean "which orders
db", a question that must never be asked.

## Slug rules (all grammars)

- kebab-case, `[a-z0-9-]` only: `rebate-settled`, not `Rebate_Settled`.
- 2–5 words, describing the content, not the type. `presentation` already says
  it is a presentation.
- Never `final`, `new`, `v2`, `updated`, `copy`, `old` — a name that claims
  freshness is stale within a week.
- No date and no id repeated inside the slug: `2026-10-16-run-622-refusal-hold.md`,
  never `2026-10-16-run-622-2026-10-16-refusal-hold.md`.
- No spaces, no parentheses, no non-ASCII. A Hebrew presentation is still
  `655-presentation-rebate-settled.html`; the language lives inside the file.

## Paths

A document's folder is decided by **who reads it**, not by who wrote it:

| Artifact | Home | Grammar |
| --- | --- | --- |
| Issue showcase / explainer | `docs/issues/` | G1 |
| Noticed-but-not-touching ledger | `docs/issues/` | G1 |
| Run / experiment / tournament record | `docs/runs/` | G2 |
| Analysis of committed evidence | `docs/audits/` | G2 |
| Design spec | `docs/superpowers/specs/` | G2 |
| Unshaped idea, external fact | `docs/ideas/`, `docs/reference/` | slug only |
| Statistics store | `data/` | G3 |
| Production registry, price tape, demo fixture | `data/` | singleton |
| Event rings, logs, heartbeats, PIDs, snapshots | `runtime/` | G3 or singleton |
| Tournament plan record | `runtime/tournaments/` | G3 — `tournament_<key>_<stamp>.json` |
| Tournament launcher log | `runtime/tournaments/` | G3 — same stem as the record, `.log` |
| Tournament results | `reports/tournaments/` | G3 — `tournament_results_<key>_<stamp>.json` |
| Tournament store | `data/` | G3 — `tournament_<key>_<arm>.db`, no stamp (the run key is unique and the cockpit predicts the path) |
| Tournament arm definitions | `configs/tournaments/` | `<key>_arms.json`, hand-written input, not generated |
| Generated reports | `reports/` | G3 |

Two rules keep this true, both from "Where generated files go" in
`architecture.md`: the destination is a **module constant**, never a path
literal at the call site, and a misplaced file gets **moved and its writer
fixed**, never hidden behind a new `.gitignore` rule.

## Known deviations (and when to close them)

| Artifact | What it does today | Upgrade trigger |
| --- | --- | --- |
| Shadow ring | `shadow_<run-id>.jsonl` — no stamp, so a reused `--run-id` appends to the previous run's tape | when run ids become reusable by design; the name is resolved in `core_brain.shadow_run._run_ring_name` and four dashboard call sites |
| Legacy tournament store | No run number: `data/<issue>_tournament_<NN>_<arm>_<stamp>.db` keeps its shape because the dashboard's clustering parser reads the arm index out of it | when the clustering parser is rewritten to take the arm from the plan record instead |
| Report identity | `statistics_report_<mode>_<run-id>_<stamp>.md` reads `..._shadow_shadow-01_...` because run ids conventionally repeat the mode | when run ids stop carrying the mode word; today the uniformity is worth the redundancy |
| Menu shadow store, validation report dir | `data/NN_shadow_<dd-MM_HH-mm>.db` and `reports/validation_<dd-MM_HH-mm>_<run-id>/` keep the old stamp: the dashboard run switcher, the KPI funnel and `data_retention.py` all recognise the `NN_shadow_` shape | when those readers take the stamp from the session file instead of the filename — the menu's *statistics* store already follows G3 |

The tournament family closed its deviation: writer, both record readers and every
on-disk record moved in one change. `<key>` is `i<issue>-r<run>`, `i<issue>` or
`i<issue>-<run_name>`; `--stamp` now *names* a run instead of replacing the
timestamp, and `core_brain.shadow_run.latest_tournament_record` is the only
"which plan is current" ranker (highest run, then newest stamp — never string
order).

## Adding a new artifact

1. Decide the grammar: is it read by a human (document) or by a program (state)?
2. Pick an existing `<kind>` unless a new one is genuinely needed; a new kind is
   a new row in this file, not a decision made inside a script.
3. Put the destination in a module constant next to the writer.
4. Add the row to the table above.
5. If it is a document, `tests/test_naming.py` already enforces the slug rules
   for everything under `docs/`; if it is state, add the name shape to that
   test's writer checks.
