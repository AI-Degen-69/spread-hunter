# Historical relay records — not current status

**Every file in this directory is a point-in-time record. Do not read any of it as the current state
of the repository.**

These are the Prime/Sub relay documents from the staged build of the live execution path: directives
issued, schemas confirmed, corrections applied, and verdicts reached, each captured at the moment it
was written. Test totals, stage status, open blockers and "as committed" code quotations were true
when recorded and are stale by design afterwards.

They are kept because the reasoning is the artifact. A directive explains why a constraint exists; a
verdict explains what was wrong with the first attempt and how it was found. Rewriting them to match
today's code would erase the only record of how the design arrived where it did, and would make
every quoted defect look like it never happened.

## Where current status lives

| Question | Source |
| --- | --- |
| What is true now, and why | `research/RESEARCH_LOG.md` — chronological, `Question → Method → Result → Verdict` |
| One line per change | `research/RESEARCH_SUMMARY.md` (Hebrew mirrors: `research/he_*.md`) |
| Current test count | `python -m pytest tests/ -q` |
| Current behaviour | the code |

## What is in here

| File | What it was, when |
| --- | --- |
| `STAGE-2-4-ARCHITECTURE.md` | The reviewed design for Stages 2-4. **The one exception to the rule above** — a living specification, kept current, because Stages 3 and 4 are not built yet. |
| `SESSION-67-HANDOFF.md` | Handoff written before Stage 2 began. Its snapshot (739 tests, Stage 2 not started) is historical. |
| `STAGE-2-DIRECTIVE.md` | The Stage 2 build order and acceptance bar, as issued. |
| `STAGE-2-RELEASE.md` | Addendum adding the constraints the baseline confirmation surfaced. |
| `STAGE-2-SCHEMA-CONFIRM.md` | Schema checkpoint verdict, four defects corrected inline. Totals quoted at 745. |
| `STAGE-2-VERDICT.md` | Stage 2 verdict: six defects found by reading the committed code, and the report rejected as evidence. Totals quoted at 755. |
| `SESSION-66-BRIEF.md`, `pr-31-review.md` | Earlier session records. |

If you add a file here, it inherits this rule: write it as a record of a moment, date it, and do not
come back to update it. The update belongs in `research/`.
