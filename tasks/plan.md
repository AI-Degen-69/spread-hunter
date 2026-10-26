# Plan — Issue #838: Update Hybrid Configuration Blueprint in Tournament Cockpit

Branch: `i838/cockpit-hybrid-blueprint-champion-spec` | Issue: #838
Tier: **Standard** | Task type: **UX / Copy** (primary) + **Code** (regression tests) + **Docs** (backlog note)
Stack: Python 3, standard library only, pytest. Test runner: `python -m pytest -q`.

Tier rationale: three files change — one existing pane, one existing test module, one
new backlog note — with a single real decision in it (how the blueprint presents the
pair cap). No interface, schema, dependency or `core_brain/` behaviour changes, so it
is not Large; it is more than one file, so it is not Small.

Skill routing (verified to exist in this environment): `incremental-implementation`
(core execution), `humanizer` (UX / Copy — the pane's claims are read as safety
promises), `documentation-and-adrs` (the backlog note). `frontend-design` is **not**
loaded: the pane introduces no new visual language and reuses existing classes.
`test-driven-development`, `spec-driven-development`, `planning-and-task-breakdown`,
`performance-optimization` and `security-and-hardening` are **not installed** in this
environment; the TDD loop is carried by `iii-build-plan`. The `code-explorer` and
`type-design-analyzer` personas were **not** deployed: this change touches one static
HTML pane with no interface or type contract, and the pane, its callers and the
config loader were read directly.

---

## 1. Grounded seams (every line verified in the working tree at `3daa04a0`)

| Seam | Location | What it means here |
| --- | --- | --- |
| `generate_cockpit_html(initial_data)` | `scripts/tournament_cockpit_server.py:1421` | Returns the whole page as one f-string. The blueprint pane is static markup inside it. |
| `tab-hybrid-config` pane | `scripts/tournament_cockpit_server.py:1857-1956` | The only region this plan edits. The next pane (`tab-pnl-chart`) starts at 1959. |
| Tab button | `:1788` | `onclick="switchTab('hybrid-config', this)"` — stays byte-identical. |
| Other pane ids | `:1797, 1959, 1972, 1996, 2009, 2022` | `tab-overview`, `tab-pnl-chart`, `tab-risk-stats`, `tab-execution`, `tab-monte-carlo`, `tab-recommendations` — the isolation test pins all six plus the edited one. |
| `run_cockpit_server(port)` | `:2547` | Writes `reports/tournament_cockpit.html` at `:2556` from `generate_cockpit_html(collect_all_stats("487"))`, then serves. |
| `TOURNAMENT_PRESETS` | `core_brain/config.py:556-598` | `prudent` = dynamic on, `0.60` multiplier, `2`–`4`¢ clamp. `hybrid` = dynamic **off** plus a `0.060` deadband. Neither alone is the champion. |
| `MakerConfig.bot_pos_price_max_pair_cost_usd` | `core_brain/config.py:56` | Ships as `0.99`. |
| `resolve_pair_cost_trial` | `core_brain/config.py:705-722` | Raises `ValueError` for any non-blank value when the process has not declared a rehearsal. |
| `core_brain/venue.py:24` | `_CFG = _load_cfg()` at import time | This is why a pair-cap assignment in `.env` breaks the live engine rather than merely being ignored. |
| `shadow_run` | `core_brain/shadow_run.py:2012` | Calls `rehearsal.declare_rehearsal()` before anything reads config, and reads `os.environ` only — never a dotenv file. |
| shadow-run CLI | `core_brain/shadow_run.py:1944-1990` | `--minutes`, `--interval`, `--db` (**required**), `--run-id`, `--starting-bankroll-usd` all exist. |
| menu `-Preset` | `scripts/spread-hunter-menu.ps1:3032-3040` | The `shadow-run` action validates `-Preset` against `control, conservative, balanced, aggressive, prudent, hybrid` and exports `BOT_QUOTE_TOURNAMENT_PRESET` for the child. `-Preset prudent` is real and effective. |
| menu preset plumbing | `scripts/spread-hunter-menu.ps1:109-137` | Captures an inherited `BOT_POS_PRICE_MAX_PAIR_COST_USD` / `BOT_QUOTE_TOURNAMENT_PRESET`, restores them after the shadow launch. |
| #799 arm definitions | `configs/tournaments/i799-tournament-test_arms.json` | Ten arms, including `prudent-hybrid` (`prudent` + `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`), `wide-shield` (8¢ stop), `hair-trigger-2c` (2¢), `patient-stop-9c` (9¢), `low-churn-deadband` (static + 10¢), `static-floor-2c` (dynamic off), `queue-clear-5m` (5 min). |
| Bounds that must accept the champion | `core_brain/config.py:393-415, 442-482, 515-532`; `core_brain/single_leg_lifecycle.py:25-30` | Deadband `0.06` is inside `0.0..1.0`; max offset `6` is an `int` inside `1..20`; clear minutes `3.0` is inside `0.1..1440.0`; rescue lead `45.0` is finite and non-negative; `ABSOLUTE_DROP` is in `STOP_LOSS_MODES`; drop `0.045` is inside `DROP_CENTS_MIN 0.01 .. DROP_CENTS_MAX 0.90`. Every champion value loads. |
| Existing render calls | `tests/test_tournament_cockpit_arms.py:249, 440`; `tests/test_tournament_cockpit_funnel.py:153` | `cockpit.generate_cockpit_html({"arms": [], "tourney_key": "487"})` is the established, working form. Reuse it; add no fixture. |
| Test write guard | `tests/conftest.py` autouse `block_real_state_writes` (`docs/agents/architecture.md:172-178`) | A test that writes under `data/`, `runtime/` or `reports/` fails. The new tests render strings in memory only. |

**Must not be modified:** `core_brain/**`, `.gitignore`, `.env.example`,
`configs/tournaments/**`, `scripts/shadow_tournament.py`,
`scripts/spread-hunter-menu.ps1`, `tests/test_tournament_cockpit_funnel.py`, and
every pane in `scripts/tournament_cockpit_server.py` other than `tab-hybrid-config`.
`reports/tournament_cockpit.html` is generated output: never hand-edited, never committed.

---

## 2. CodeRabbit plan intake (read once, then not again)

**Adopted:** the single-pane / markup-only shape; the one-value-set rule across table,
commands and `.env`; leaving the pair cap unset in the `.env` block; `prudent` plus
explicit overrides rather than `-Preset hybrid`; the honest labels ("attributed",
"projected target", "not tested in #799", "trigger, not cap"); the slice-based tests;
regenerate-the-report-from-the-template step; removing the `scripts.shadow_tournament`
and `-Preset hybrid` commands.

**Rejected:** its line-number range "about lines 1856–2028" for the pane (the pane
ends at 1956; 2028 is inside `tab-recommendations`); its `--db data/champion_prudent_shadow_01.db`
peculiarity is kept because `--db` is genuinely required, but the surrounding prose was
cut; its implication that the report file exists in the checkout (it does not); and its
"record the current tab-pane ids before editing" *as a step* — the ids are recorded in
§1 and in `CONSTRAINTS.md` instead, so the plan needs one fewer task.

**Left `[UNVERIFIED]` (and therefore not relied on):** the #799 figures
(`289 / 210 / 74 / 9.52 / $0.70 / -$2.28 / 44.1%`). `data/tournaments/` is empty in
this checkout and no committed file carries them, so the pane attributes them instead
of asserting them; the source of the ticket's Sharpe 9.52 is unknown. Also unverified:
that GitHub issue #799 is the tournament — #799 is a merged PR titled
`feat(dashboard): Orders tab split STATUS pair COST columns`, and "Tournament #799"
here means the run keyed to that issue number, whose arms file is committed.

**CodeRabbit's own contradiction, confirmed:** "An explicit pair-cap value crashes
outside rehearsal." Verified directly — see §3 C2.

---

## 3. Contradictions between the issue and the code, and how each resolves

- **C1 — the report file is not in the checkout.** The issue cites
  `reports/tournament_cockpit.html` lines ~422–522. The file does not exist; `/reports/`
  is gitignored (`docs/agents/architecture.md:50`) and `run_cockpit_server` regenerates
  it from the template on every start (`:2547-2558`). *Resolution:* edit only the
  template, then start the cockpit once to regenerate the report. Never hand-edit it,
  never change `.gitignore` (`docs/agents/architecture.md:168-170` forbids exactly that).
  Acceptance criterion 5 is met by regeneration.
- **C2 — the issue's pair-cap line breaks the live engine.** The issue asks for
  `BOT_POS_PRICE_MAX_PAIR_COST_USD=0.990` and for the `.env` block to match "exactly".
  `resolve_pair_cost_trial` (`core_brain/config.py:705`) raises `ValueError` for **any**
  non-blank value unless the process declared a rehearsal, and `core_brain/venue.py:24`
  loads config at import time. `$0.990` is already the shipped default (`:56`).
  *Resolution:* show `$0.990` as the shipped default, state that the variable must stay
  unset, and print no assignment — not even a comment, so it cannot be pasted back.
  This is the one deliberate deviation from criterion 4, and it is the plan's most
  important single decision; the operator can reject it before build (see §7).
- **C3 — `prudent` alone clamps at 2–4¢, not 2–6¢.** The `prudent` preset is
  `0.60x` held between 2¢ and 4¢. The `2-6¢` band the issue specifies requires
  `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`, which is exactly what the `prudent-hybrid`
  arm ran. *Resolution:* keep `6` in all three places and name the arm as the source.
- **C4 — several champion values were never varied by #799.** No arm sets a 6¢ deadband,
  a 4.5¢ stop, a 3-minute queue-clear, or a 45s rescue lead; `#799`'s deadband arms ran
  10¢, and its stop arms ran 2¢ / 8¢ / 9¢. *Resolution:* the Source Champion column says
  where each value comes from and `badge-amber` marks the values #799 did not test
  directly, instead of implying a measured optimum.
- **C5 — "ready-to-run" must not mean "runs the wrong thing".** `python -m
  scripts.shadow_tournament --minutes 60.0 --interval 1.0` runs four default presets,
  not the champion, and the tournament launcher strips inherited `BOT_*` variables.
  *Resolution:* drop that command and print the nine `$env:` assignments, the pair-cap
  `Remove-Item` guard, `-Preset prudent` for the menu, the direct `shadow_run` command,
  and the cockpit command.
- **C6 — shadow runs and live runs read settings from different places.**
  `core_brain.shadow_run` reads `os.environ` and never loads a dotenv file, while the
  live trader reads `.env`. *Resolution:* the pane states which block applies to which,
  so the operator does not set `.env`, start a rehearsal, and wonder why nothing changed.

**Resolved from code, no operator input needed:** no `needs-answers` label, no
`quick-fix` label and no Open questions section on the issue, so the quick-fix gate
does not run and nothing is unresolvable from the code.

---

## 4. Interface contract (locked before any markup is written)

This change has no type or function contract; its contract is the rendered HTML, and
the tests are its enforcement.

- **Pane boundary:** starts at `id="tab-hybrid-config"`, ends before the next
  `<div id="tab-`.
- **Table:** the blueprint slice's first `<table>` … `</table>` keeps five columns in
  today's order: Parameter | Config Value | CLI / Env Key | Source Champion | Live
  Function & Mathematical Rationale.
- **Command block:** heading contains `Ready-to-Run PowerShell`, then the nine
  `$env:KEY = "VALUE"` lines, the pair-cap `Remove-Item` line, the menu command, the
  direct `shadow_run` command, the cockpit command — in that order.
- **`.env` block:** heading contains `Live Production Environment Variables`, then the
  same nine `KEY=VALUE` lines, then a plain comment naming `$0.990` as the shipped
  default. The slice must not contain the substring `BOT_POS_PRICE_MAX_PAIR_COST_USD=`.
- **Cards:** a `Performance Forecast` card and a card headed
  `Failure Modes &amp; Stress Scenarios`, both built from the existing
  `decision-grid` / `decision-box` classes.
- **Value form:** `KEY=VALUE` (no spaces) in the table and `.env`; `$env:KEY = "VALUE"`
  (spaces) in the command block.
- **Untouched:** the seven `id="tab-…"` pane ids, the tab button, the CSS, the
  `switchTab` script, `analyze_arm_database`, `collect_all_stats`,
  `run_cockpit_server` and `CockpitHandler`.

---

## 5. Tasks

Dependency graph:

```
T1 (tests, RED) ──┬─> T2 (pane content) ──┐
                  └─> T3 (two cards)   ──┴─> T4 (verify + regenerate + backlog)
```

T2 and T3 touch the same pane but different regions; T3 needs T2's insertion point.
T1 is first because it is the riskiest: if the slice boundaries cannot be cut cleanly,
every later task is built on a contract that does not hold.

### T1 — Add the two blueprint regression tests (size S, `[Code]`)

**Target file:** `tests/test_tournament_cockpit_arms.py` (append; do not touch
`tests/test_tournament_cockpit_funnel.py`).
**Depends on:** nothing. **Verification:** run the focused suite; both new tests must
**fail** on the unmodified markup, each for the stated reason, and no existing test
may change status.

Render with the established call, adding no fixture and no production helper:

```python
html = cockpit.generate_cockpit_html({"arms": [], "tourney_key": "487"})
```

Cut four slices, and fail with a clear message when the blueprint start marker is
missing:

- blueprint = `'id="tab-hybrid-config"'` … the next `'<div id="tab-'` (or end of HTML)
- table = first `<table` … `</table>` inside the blueprint
- commands = `Ready-to-Run PowerShell` … `Live Production Environment Variables`
- env = `Live Production Environment Variables` … end of the blueprint slice

**Test 1 — champion content and cross-block agreement.** A local dict of the nine
pairs, exactly as in `SPEC.md`. For each pair assert `f"{k}={v}"` is in the table
slice, `f'$env:{k} = "{v}"'` is in the command slice, and `f"{k}={v}"` is in the env
slice. Then assert, in the table slice, the seven source arm ids `prudent-hybrid`,
`static-floor-2c`, `low-churn-deadband`, `hair-trigger-2c`, `patient-stop-9c`,
`wide-shield`, `queue-clear-5m`. Then, in the blueprint slice: `rounded up to a whole
cent`, `$0.990`, `Performance Forecast`,
`Failure Modes &amp; Stress Scenarios`, the six escaped forecast values `80% - 84%`,
`82% - 86%`, `&gt; 10.5`, `+$0.38 - +$0.46`, `&gt; 8.0`, `&gt; +$30.00`, and the three
scenario names. Then, in the command slice, the menu command
`.\\scripts\\spread-hunter-menu.ps1 shadow-run -Preset prudent -Minutes 60` (a **raw
string** in the test, so the rendered backslashes stay single) and
`python -m core_brain.shadow_run`, plus `--db`, `--run-id`, `--interval` and
`--starting-bankroll-usd`.

**Test 2 — safety and tab isolation.** The blueprint slice contains none of
`BOT_POS_PRICE_MAX_PAIR_COST_USD=`, `$env:BOT_POS_PRICE_MAX_PAIR_COST_USD =`,
`-Preset hybrid`, `scripts.shadow_tournament`, `TOURNAMENT_PRESETS["hybrid"]`. The full
page still contains `switchTab('hybrid-config'` and all seven pane ids —
`tab-overview`, `tab-hybrid-config`, `tab-pnl-chart`, `tab-risk-stats`,
`tab-execution`, `tab-monte-carlo`, `tab-recommendations` — hard-coded, `tab-pnl-chart`
included.

**Expected RED:** test 1 fails on the missing queue-clear keys and the absent cards;
test 2 fails on `-Preset hybrid`, `scripts.shadow_tournament` and
`TOURNAMENT_PRESETS["hybrid"]`.

### T2 — Rewrite the banner, parameter table, commands and `.env` block (size M, `[UX / Copy]`)

**Target file:** `scripts/tournament_cockpit_server.py`, `tab-hybrid-config` only.
**Depends on:** T1. **Verification:** focused suite green for the T2 slice of test 1 and
test 2's safety assertions; then render the page and confirm the tab.

- **Banner.** Keep the `🌟 BENCHMARK SYNTHESIS` badge and the "Static benchmark
  synthesis" wording. Name Tournament #799, ten arms, ports 8801–8810, and cite
  `289 trades`, `210 merges`, `74 stop exits` as *attributed to the #799 tournament
  review*. Replace the "Status in Codebase" value: the champion is
  `core_brain.config.TOURNAMENT_PRESETS["prudent"]` plus explicit overrides, and **no
  single shipped preset holds the full champion**.
- **Six rows**, using the exact values from `SPEC.md`. `badge-green` where an arm
  sourced the value (`prudent-hybrid` for the offset); `badge-amber` where #799 did not
  test it (deadband, stop, queue-clear, rescue lead) or where it is a shared safety
  default (the cap). Rationales:
  - *Requote Deadband* — the queue-priority defender: the price move the bot ignores
    before re-quoting; a 10¢ deadband risks stale fills. Between `static-floor-2c`
    (3¢ at the time) and `low-churn-deadband` (10¢); not tested at 6¢ in #799.
  - *Volatility Pricing Offset* — offset = round(0.60 × recent range in cents), held
    between 2 and 6; stale or missing range data falls back to the static base offset.
    Source `prudent-hybrid`. Attribute the arm's Sharpe 9.52 / `$0.70` drawdown and
    `static-floor-2c`'s `-$2.28` outlier to the #799 review.
  - *Single-Buy Loss Stop* — trigger is entry minus 0.045, **rounded up to a whole
    cent**; the emergency floor can raise the trigger. Between `hair-trigger-2c` (2¢,
    44.1% stop rate per the #799 review) and `patient-stop-9c` / `wide-shield`
    (9¢ / 8¢, tail losses). Not tested at 4.5¢ in #799.
  - *Queue-Clear Gate* — estimated clear time = size ahead ÷ recent traded rate, an
    upper-bound estimate; no recent flow makes it infinite, so the entry is refused.
    Tightened from `queue-clear-5m`; not tested at 3 minutes.
  - *Aged-Out Rescue Lead* — unwinds stranded orphan legs before the settlement freeze.
    Not varied in #799 (all arms inherit the shipped 900s).
  - *Absolute Pair Cap* — value `$0.990`; the key cell reads
    `BOT_POS_PRICE_MAX_PAIR_COST_USD (shipped default, leave unset)` with **no `=`**;
    rationale: the bot refuses a two-leg sum above $0.99, which leaves at least a
    $0.01 gross spread before costs.
- **Command block**, in order: nine `$env:KEY = "VALUE"` lines;
  `Remove-Item Env:BOT_POS_PRICE_MAX_PAIR_COST_USD -ErrorAction SilentlyContinue`; the
  menu command with a note that it needs PowerShell 7; the direct command
  `python -m core_brain.shadow_run --minutes 60 --interval 1 --db data/champion_prudent_shadow_01.db --run-id shadow-champion-prudent-01 --starting-bankroll-usd 100`;
  and `python -m scripts.tournament_cockpit_server --port 8800`. Remove the bare
  `python -m scripts.shadow_tournament` line and the `-Preset hybrid` line.
- **`.env` block:** the same nine lines, then
  `# Pair cap: $0.990 shipped default. Leave the pair-cap variable unset.`, then a line
  saying a shadow run reads the shell environment and not `.env`.
- Apply the f-string rules from `CONSTRAINTS.md` (doubled braces and backslashes,
  `&amp;`, literal `"` in `<pre>`).

### T3 — Add the Performance Forecast and Failure Modes cards (size S, `[UX / Copy]`)

**Target file:** the same pane. **Depends on:** T2 (insertion point).
**Verification:** focused suite green for the card assertions; both cards visible in
the browser.

- **Performance Forecast** card, reusing `decision-grid` / `decision-box`, with the six
  figures in their escaped form (`&gt;` for `>`) and, under each, one line saying how
  the cockpit computes it — verified against `analyze_arm_database`
  (`scripts/tournament_cockpit_server.py:1052-1300`): win rate is the share of closes
  with positive PnL (`:1185`); merge ratio is merges ÷ closes (`:1186`); EV is mean PnL
  per close (`:1180`); profit factor is gross wins ÷ gross losses (`:1191`); the Sharpe
  proxy is mean ÷ standard deviation × 10 (`:1221`); the Monte Carlo floor is the 5th
  percentile of 500 resamples of 100 trades (`:1223-1230`). Close with one line saying
  these are projected targets for a combination #799 never ran, not measured results.
- **Failure Modes &amp; Stress Scenarios** card, three scenarios, each with a
  description and a mitigation:
  - *Flash Directional Breakout* — leg 1 fills just before a move of more than 15¢ and
    leg 2 cannot fill. Mitigation: the 4.5¢ stop triggers early, targeting about
    -$0.045 to -$0.05 per share — and a gap or a thin book can make the real loss larger.
  - *Liquidity Vacuum / Book Desertion* — taker activity freezes after the leg 1 fill.
    Mitigation: the 3-minute queue-clear filter avoids the slowest books and the 45s
    aged-out rescue unwinds before the settlement freeze — with the estimate caveat.
  - *High-Frequency Ping-Pong Jitter* — fast bid-ask swings inside the spread.
    Mitigation: the 6.0¢ deadband suppresses cancel / re-quote churn and keeps queue
    priority.

### T4 — Verify, regenerate the report, record out-of-scope discoveries (size XS, `[Docs]`)

**Depends on:** T2, T3. **Verification:** the focused suite green with zero failures and
zero errors; the regenerated report read back from disk.

- Run `python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`
  yourself and paste the real output. Never hand the command to the operator.
- Regenerate the report: start `python -m scripts.tournament_cockpit_server --port 8800`
  once, then stop it. Read `reports/tournament_cockpit.html` and confirm it contains
  `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0`, `Failure Modes &amp; Stress Scenarios` and
  `Performance Forecast`. Do not hand-edit, stage or commit it.
- Record the out-of-scope discoveries in
  `docs/backlog/i838-cockpit-hybrid-blueprint.md`: `.env.example`'s stale stop-mode
  names; the leaderboard's empty worst-catalyst / hypothesised-tweak columns for the
  #799 arms (issue #837 owns that); the absence of any committed #799 run record, so
  the attributed figures cannot be re-derived; and that the cockpit's Sharpe proxy
  (mean ÷ σ × 10) is not the same statistic as the ticket's Sharpe 9.52.
- End the hand-off with the operator block in §8 — five steps or fewer, no `pytest`,
  no `git`, no `gh`.

---

## 6. Checkpoints

- **After T1:** exact focused-test output pasted, showing the two new tests red for the
  stated reasons and every pre-existing test still green. In `auto` mode this is a
  one-line progress report, not an approval pause.
- **After T3:** the focused suite fully green, and the page opened at
  `http://127.0.0.1:8800/487` with the blueprint tab showing the six-row table, both
  cards, and the two command blocks.
- **After T4:** the regenerated report on disk carries the same markup.

## 7. One improvement proposal (evidence-based, adopt-by-default)

**Proposal — pin the blueprint's champion values to the committed #799 arms file, the
way the #487 table already pins itself.**

Evidence, quoted verbatim from `tests/test_tournament_cockpit_arms.py`:

> """The cockpit prose and the executable env live in two files.
>
> Names alone are not enough: a value edited in one file and not the other
> would misreport what the arm actually ran, which is the whole promise the
> table makes to the operator.
> """

That test (`test_i487_each_arm_tweak_names_the_one_knob_the_spec_file_sets`) reads
`configs/tournaments/i487-current-setup-10arm_arms.json` and asserts the cockpit's prose
matches the file the launcher actually executes. The blueprint tab has exactly the same
drift exposure — it names seven #799 source arms — and the equivalent file,
`configs/tournaments/i799-tournament-test_arms.json`, is committed and already carries
the champion's two preset-sourced values (`prudent-hybrid`: `BOT_QUOTE_TOURNAMENT_PRESET=prudent`,
`BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6`).

**Classification:** edge-case hardening / better fit to an existing repo pattern →
**adopt-by-default**. It costs one more test function in the same file and no production
code: assert that the pane's `prudent-hybrid` row agrees with the arm file's `env`, so a
future edit to either file is caught rather than misreported to the operator. Recorded
here so it is not re-litigated; if it is dropped, drop it deliberately.

## 8. How to verify (operator, hands-on)

1. Start the cockpit: `python -m scripts.tournament_cockpit_server --port 8800`.
2. Open `http://127.0.0.1:8800/487` and click **⚙️ HYBRID Configuration Blueprint**.
3. In the parameter table, the Queue-Clear Gate row reads **3.0 minutes**, and the
   Volatility Pricing Offset row reads **2-6¢** with `BOT_QUOTE_TOURNAMENT_PRESET=prudent`.
4. Under the table, the **Performance Forecast** and **Failure Modes & Stress
   Scenarios** cards are both rendered, and the `.env` box has **no**
   `BOT_POS_PRICE_MAX_PAIR_COST_USD=` line — the pair-cap row instead says *shipped
   default, leave unset*.
5. Open `reports/tournament_cockpit.html` from disk and confirm the same tab and the
   same values. A failure looks like a 404, a missing tab, a table still showing
   `TOURNAMENT_PRESETS["hybrid"]`, or a leftover `-Preset hybrid` line.

No money is at risk and nothing is placed, so no undo step is needed.
