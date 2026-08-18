---
name: multi-model-pairing
description: Use when setting up or operating a Prime/Sub multi-model pairing — two models relaying messages through an Owner. Reach it to assign seats, write the handshake, issue a Prime directive, answer as Sub, or correct Sub's work.
---

# Multi-Model Pairing

## Overview

Multi-model pairing organizes two or more AI models into a high-leverage partnership across an asymmetric division of labor.

The core idea is simple: **Prime decides; both build.** Prime is the higher-capability model with authority — it reads the repo, chooses what to implement itself, and delegates the rest. Sub is the lower-capability model — it receives instructions, does the delegated task, and reports what it did. Both are working; both write code.

The economic premise is what makes the split worth running: Prime's turns are the scarce resource (quota, rate limit, cost per turn), so the deeper reasoning goes to decisions only Prime can make well, and Sub's cheap turns absorb the mechanical load.

## Terminology

These three names are used throughout this skill and in every message it generates. Keep them consistent across the whole session.

| Name | Who | Responsibility |
| :--- | :--- | :--- |
| **Owner** | The human | Oversees the project, resolves ambiguities, relays messages between models, approves or vetoes direction. |
| **Prime** | The **higher-capability** model — the one whose quota is being conserved | Reads the repo, decides what to build itself and what to delegate, sets invariants, verifies Sub's work directly. Issues directives. |
| **Sub** | The **lower-capability** model — the one whose turns are cheap to spend | Executes the delegated tasks — writes code, runs commands and tests, manages git — and reports what it did: what ran, what changed, what broke. |

`Prime : Sub` follows the prime-contractor / subcontractor sense. Prime holds the decision: it decides what to implement itself and what to hand off. Sub holds the delegated task: it executes and reports. Both are fully capable agents who write code — Sub is not a toy — but when reasoning depth differs, the deeper model takes Prime so its limited turns go to the decisions only it can make well.

```
┌─────────────────────────────────────────────────────────┐
│                          Owner                          │
│                (Observes, Relays, Approves)             │
└───────────────▲─────────────────────────▲───────────────┘
                │ (Copy-Paste)            │ (Copy-Paste)
┌───────────────▼─────────────┐   ┌───────▼───────────────┐
│      Prime (higher model)   │   │   Sub (lower model)   │
│  - Strategic directives     │   │  - Executes specs     │
│  - Math & anomaly critique  │   │  - File edits & tests │
│  - QUOTA CONSERVED HERE     │   │  - Git, chain, DB     │
└─────────────────────────────┘   └───────────────────────┘
```

---

## Default Capability Assumption

Unless the Owner says otherwise, assume **both models are full agents**:

- Read and write the file system.
- Run terminal commands, test suites, scripts, git.
- Hold agent tools (search, fetch, subagent dispatch, MCP servers).
- Can invoke **skills**, and their skills library is broadly the same as Prime's.

Assume both are full agents in every pairing. Ask about capabilities only when the Owner has already signalled something unusual (a plain chat window with no tooling, an API-only endpoint, a sandbox).

Two consequences follow, and both must be stated in every handshake:

1. **Simultaneous writes are forbidden.** Both agents typically point at the same working tree. Two agents editing concurrently corrupts state. Default posture for Sub is **read + run only** until Prime assigns an explicit file boundary, a separate branch, or a separate worktree. Prime must issue that boundary before asking Sub for any edit.
2. **Sub can be directed by skill name.** See Step 2b.

---

## Workflow

### Step 1: Discover Models and Assign Seats

**No model identity is assumed by this skill.** Either seat can be filled by any model, and the pairing changes between sessions and between projects. Participants come from this session's answers alone — not prior sessions, memory, project history, or the example text in this document.

**On invocation, ask the Owner before generating anything** — before the handshake, before any directive, before reading the codebase. Ask in one round, as a compact set of questions:

1. **Which two models are in the pairing, and through what environment does each run** (IDE agent, CLI, chat window, API)? Ask this whenever the Owner has not already named both models explicitly. Include self — name the model occupying this session.
2. **Which seat does each model take — Prime or Sub?** Ask with a recommendation attached: compute it from the capability criteria below, state it in one line with the reason, and let the Owner confirm or override.
3. **What are Prime's operational constraints** — quota ceiling, rate limit, cost per turn, context size? This question is about **Prime only**. Sub's quota is not a design input; Sub's turns are the cheap resource the pairing is built to spend.
4. **What is the current objective** the pair is working toward?

Skip a question when the Owner has already answered it in this session or a project rules file states it explicitly. Ask about Prime's constraints only — the Default Capability Assumption covers Sub's tools and the economics cover Sub's quota.

Record the answers and use them to fill every `[Prime Model Name]` / `[Sub Model Name]` placeholder downstream. If a model's capabilities are unfamiliar, look them up rather than guessing.

#### Seat Assignment Criteria

Apply in order. The first criterion that discriminates decides the seat.

1. **Reasoning depth.** The model with greater abstract reasoning, synthesis, or mathematical formulation capacity takes **Prime**. Where the models are named versions of one family, the higher version or tier is Prime by default (e.g. Opus 5 over Opus 4.6).
2. **Scarcity.** If reasoning depth is comparable, the model under the tighter quota, rate limit, or cost per turn takes **Prime** — the seat that spends the fewest turns.
3. **Environment.** If both are comparable on 1 and 2, the model with the richer environment access, the larger context window, or the existing project context takes **Sub**, so it can carry the mechanical load without re-deriving state.

State the recommendation to the Owner as a single line with its reason, e.g. *"Opus 5 takes Prime — deeper reasoning and the quota you asked to protect; Opus 4.6 in the IDE takes Sub, separate quota pool, cheap to spend."*

Seat by reasoning depth and scarcity; tool access is a Sub qualification, not a Prime one, and under the Default Capability Assumption both models have it anyway.

### Step 2: Generate the Partner Handshake Message

Whenever a new model enters the loop or a partnership is initialized, generate a handshake message for the Owner to copy-paste. Fill the placeholders with the names captured in Step 1.

Before writing it, **verify the current state of the work** — repo status, test results, what is already built. A handshake or directive built on stale assumptions makes Sub burn turns re-doing finished work, which is the exact waste this pairing exists to prevent. If a prior directive has since been completed, say so explicitly in the handshake and close it.

#### Template A: Sub POV (message to Prime)

```markdown
Hey [Prime Model Name],

I am [Self Model Name], operating as **Sub** in this pairing.

Our Owner has set up our collaboration with a clear division of labor:
1. **Roles:** You are **Prime** — architecture and strategy. I am **Sub** — engineering and execution.
2. **Command authority:** You provide strategy, directives, and specifications. I execute code, run tests, query local databases and chain data, manage git/PRs, and report findings back to you.
3. **Quota conservation:** Your turns are the scarce resource here; mine are not. Delegate every mechanical task — scripts, diagnostics, test runs, data checks, refactors — to me without hesitation, so your budget goes to architectural and mathematical decisions.
4. **My capabilities:** I have full file read/write, terminal, git, agent tools, and a skills library. Direct me by skill name when one applies.
5. **Communication:** Relayed by our Owner via copy-paste. Data first, bounded steps, under 5 items per message.

[Optional: initial status, environment summary, or diagnostic report]

Awaiting your directive.
```

#### Template B: Prime POV (message to Sub)

```markdown
Hey [Sub Model Name],

I am [Self Model Name], operating as **Prime** in this pairing.

Our Owner has paired us to optimize workflow and execution speed:
1. **Roles:** I am **Prime** — strategy, system architecture, mathematical models, anomaly critique, decisions. You are **Sub** — the hands-on executor.
2. **Execution authority:** When I issue a specification or diagnostic request, you own implementation, script runs, database and chain queries, test execution, and git workflow. Do not wait on me for boilerplate.
3. **Quota discipline:** Our Owner is deliberately conserving my turns, not yours. Spend your own freely — run the extra check, read the extra file, re-run the suite. Escalate to me only for architecture, invariant definition, statistical validity, or go/no-go calls.
4. **Tools and skills:** I assume you have full file read/write, terminal, git, agent tools, and a skills library comparable to mine. Where I name a skill, invoke it rather than improvising the equivalent.
5. **Write boundary:** [state the boundary — read+run only / named files / named branch / separate worktree]. We share a working tree; concurrent writes corrupt state.
6. **Reporting protocol:** Reply in the Sub Reply Contract shape (Step 3): headline, tagged evidence, what ran, anomalies, status — "awaiting your directive."
7. **Communication:** Relayed by our Owner. I issue directives; you execute and report results.

[Optional: closure of any stale prior directive, initial objective, or first task specification]

Ready for your confirmation on current environment state and baseline results.
```

### Step 2b: Direct Sub by Skill Name

Sub's skills library is normally the same as Prime's, because both are typically configured from the same skills directory. Naming a skill transmits an entire workflow in two words and costs Prime nothing to specify.

Before the first directive of a session, enumerate the available skills — read the skills directory (commonly `~/.claude/skills/`, plus plugin and project-scoped skill paths) or the session's skills listing. Do not invent skill names; a wrong name makes Sub improvise silently.

Then, when a directive maps onto an existing skill, name it:

- Debugging a failure → *"invoke `systematic-debugging` before proposing any fix."*
- Adding a feature → *"invoke `brainstorming` first, then `writing-plans`."*
- Reviewing a diff → *"run `code-review` at high effort and report findings verbatim."*
- Verifying completion → *"gate your report on `verification-before-completion`."*

Rules:

- Name the skill **and** the outcome expected from it. The skill governs the method; Prime still owns the acceptance criteria.
- If a skill exists for the task, prefer it over hand-writing the procedure in the directive. Re-specifying a skill's workflow in prose wastes a Prime turn and drifts from the maintained version.
- When Prime is unsure a skill exists in Sub's environment, ask Sub to list matching skills as part of the report rather than assuming.

---

### Step 3: Operational Execution Protocol

Each seat has a fixed answer format. **Prime** answers with the dual channel (Step 4) — an Owner Brief plus a fenced Sub Directive. **Sub** answers with the report contract below — a self-contained, data-first reply Prime can verify without a follow-up. The two are the two halves of one round trip: Prime's Channel 2 becomes Sub's next work; Sub's report becomes Prime's next Channel 1.

When operating as **Sub**:

1. **Stay in the reporting role.** Report the work you performed — commands run, changes made, results — then request the next directive. If a directive asks you to paste or reproduce source, give the commit SHA and `path:line` instead, and state the file is on disk for Prime to read.
2. **Run before reporting, and paste the evidence.** If Prime asks for a diagnostic, test, or check, execute it first. Report completed findings, never a promise to run them — and every finding is backed by the command's verbatim output and exit code, pasted in the same report. An assertion without its output is self-attestation, not evidence.
3. **Spend your own turns freely.** Do not economize on checks, file reads, or repeated test runs — your budget is not the constrained one. Economize Prime's instead, by returning complete reports that do not require a follow-up round trip.
4. **Highlight anomalies honestly** — report contradictions and limitations as item 4 of the reply contract requires.
5. **Respect the write boundary.** Do not edit, stage, or commit outside the scope Prime assigned. If a task appears to require an out-of-boundary edit, stop and report that instead of doing it.
6. **Plan your execution on Prime's plan.** For any directive with more than one moving part, reply with your execution plan before writing code — the concrete steps in order: files to touch, commands to run, tests to run, and what each step proves. Wait for Prime's approval before executing. This is your plan of *how* you carry out Prime's plan of *what* to build: it costs one cheap turn and catches a misread scope before any code exists.

#### The Sub Reply Contract

Sub's report is a fixed shape — the mirror image of Prime's dual channel. Prime reads it as the input to the next directive, so it must be self-contained and mechanically verifiable: no preamble, no promises, nothing Prime has to ask about twice.

**The report covers what Sub did — not what is on disk.** Commands run, changes made, results, and the verbatim output of those commands. File bodies and diffs Prime can read itself never appear in the report; the commit SHA and a `path:line` pointer replace them. A model asked to reproduce text it does not have in front of it generates the most plausible version — so never reproduce source, and never ask for it.

**Verification is pasted, never asserted.** A report proves a finding by showing the command output and exit code that produced it, in the same message. “Verified,” “reviewed,” “tests pass,” or “no issues found” without the output underneath is self-attestation — a reconstruction of the check, not the check — and Prime must treat it as unverified.

Report in this order:

1. **Headline.** The answer to Prime's question in one line — the number, verdict, or blocker.
2. **Evidence, tagged.** Every claim carries a tag and a source. `MEASURED` = observed this run; `DERIVED` = computed from data; `ASSUMED` = taken as given. Source is `path:line` or the command that produced it — and that command's output and exit code are pasted in this same report (item 3). A claim whose output is not pasted is self-attestation, not evidence. Tag every number.
3. **What ran.** The exact command, its exit code, and the relevant output — verbatim, not summarized, and pasted in the same turn. Every `MEASURED` or `DERIVED` claim in item 2 traces to a command here. “The tests passed,” “I reviewed the output,” or “no issues found” with no pasted output is self-attestation, not a claim — re-run and paste, or report that you could not.
4. **Contradictions and anomalies.** If a finding contradicts Prime's premise, say so explicitly with the numbers. Declare sample-size limits, timing errors, and artifacts rather than smoothing them over. Omit this section only when there is genuinely nothing to report — do not claim cleanliness you did not check.
5. **Status and next.** What is done, what is blocked, what Sub will do if left unattended, closing with *"Awaiting your directive."*

```markdown
## [One-line answer: number / verdict / blocker]

| tag | value | source |
| :--- | :--- | :--- |
| MEASURED | [number, units] | [path:line or command] |

**Ran:** `[command]` → exit [n], [verbatim output or the exact lines that matter]

**Contradiction / anomaly:** [or omit — never claim cleanliness you did not check]

**Status:** [done / blocked on X / next I will Y]

Awaiting your directive.
```

#### Worked Micro-Example — Sub's reply

This is the report Sub sends back for the directive in Step 4's micro-example, so the two examples read as one round trip.

```markdown
## Offset-corrected publish latency is 143.2 ms (IQR 71.4) — under `tau_post = 150 ms`; the host clock carries the +541.3 ms error

| tag | value | source |
| :--- | :--- | :--- |
| MEASURED | host clock offset +541.3 ms | `chronyc tracking` |
| DERIVED | venue publish latency 143.2 ms (IQR 71.4) | 684.52 − 541.3; `recorder/align.py:112` |
| MEASURED | N = 1,844 samples | `recorder/align.py` run |

**Ran:** `recorder/align.py --since 2026-08-15T00:00Z` → exit 0 — "1844 samples, host offset +541.3 ms, latency 143.2 ms (IQR 71.4 ms)"

**Contradiction:** the premise was that venue jitter exceeds the modeled effect. With the host clock offset removed, venue publish latency sits under `tau_post = 150 ms` — the venue is fine; the host clock sync is the blocker.

**Status:** decomposition done, read + run only respected (no edits). Blocked on your call — sync the host clock, or proceed on venue-timestamped reads.

Awaiting your directive.
```

When operating as **Prime**:

1. **Delegate mechanics.** Do not write long boilerplate or hand-process strings in chat. Specify requirements, invariants, and edge cases; Sub implements and verifies. (Exception: when Sub's work is wrong, Prime patches it itself — Step 5.3.)
2. **Verify the premise before specifying.** Check the actual state of the work — repo, tests, prior directive status — before issuing a directive built on it. A spec for work already finished, or against a state that has moved, wastes a full relay cycle.
3. **Focus on invariants and metrics.** Review reported data against pre-registered criteria, hunt for sample-size bias and instrumentation bugs, issue clear next actions. Treat any claim without its pasted output and exit code as unverified — self-attestation is not evidence.
4. **Set the write boundary explicitly** before asking Sub for any edit, and restate it whenever it changes.
5. **Read the repo yourself.** Anything Prime can open with a file read, `cat`, or `git show`, Prime obtains itself. Directives ask Sub for work — build, run, fix, report — never to paste or reproduce source that is already on disk. Reproducing text it lacks invites reconstruction; reproducing text Prime could read in seconds wastes a relay cycle.
6. **Ship both channels.** Every Prime turn follows Step 4.

---

### Step 4: Dual-Channel Output (Mandatory for Prime)

The Owner relays messages but should never have to decode machine-oriented prose to know what they are relaying. Every Prime turn therefore ships **two channels in a single response**, in this order:

1. **Channel 1 — Owner Brief.** Plain-English explanation, written for the human.
2. **Channel 2 — Sub Directive.** The technical message, in a fenced block, for copy-paste to Sub.

Ship both channels, always. A directive without a brief leaves the Owner relaying instructions they cannot evaluate; a brief without a directive produces nothing to relay.

#### Channel 1 — Owner Brief (human-facing)

Written as a stakeholder update, not a transcript. Constraints:

- **Emoji-titled headings**, mapped to content, never decorative.
- **Structure over prose:** headings, short paragraphs, a table when the directive carries more than two items.
- **No jargon without translation.** If a technical term is unavoidable, define it inline in everyday words the first time it appears.
- **Explain the reasoning, not just the content.** For each directive item, say what it asks for *and* why it helps or what failure it prevents. The Owner should be able to judge whether the plan is sound.
- **Lead with the decision.** If Prime is approving, halting, or reversing something, that is the first line.
- **Length ceiling:** shorter than the directive it explains. Cut filler, recaps, and closers.

Recommended skeleton:

```markdown
## ✅ What Sub delivered
[One or two sentences. Concrete outcomes only.]

## 🛑 The decision and why
[What is being approved, blocked, or redirected — reasoning in everyday language.]

## 📤 What I sent back — N items
| # | Directive | Why |
|---|---|---|
| 1 | [plain-English restatement] | [what it prevents or unlocks] |

## 🎯 Status
[Current verdict, what is gated on what, what happens next.]
```

#### Channel 2 — Sub Directive (model-facing)

Optimized for another model's comprehension, not human readability. Any format is acceptable — prose, numbered specs, tables, pseudo-schema, JSON — chosen for whatever transmits the specification most precisely.

- **Precision over politeness.** Exact figures, units, sample sizes, file paths, thresholds, and pass/fail criteria.
- **State the reasoning chain**, so Sub can detect when its own findings contradict the premise.
- **Bound the scope explicitly.** Name what must *not* change as clearly as what must, and restate the write boundary.
- **Bound the milestone.** One milestone per directive, at most 3–5 items, each with a checkable completion criterion (a number, a file, a passing test), stated in execution order. For multi-step work, require Sub's execution plan before code: *"Reply with your execution plan — files, commands, tests, in order — and wait for my approval before coding."*
- **Name the skills** that apply (Step 2b) rather than re-specifying their workflow.
- **Close with the standing report default.** Every directive ends the same way, so Sub's return message is directly comparable to the request. Standing closer: *"Paste each command, its exit code, and its verbatim output in the same turn. No claim without the output."*
- Keep it model-facing — precision and reasoning chain, untranslated. Human-facing work belongs in Channel 1.

#### Step 4.1 — Delivering a directive as a file

A Channel 2 directive that contains fenced code — source quotes, diffs, JSON, schema snippets — **cannot be delivered inside a fenced block.** The inner fences terminate the outer one, and the Owner is left guessing where the copy region starts and ends. That is a delivery failure regardless of how good the directive is.

When the directive contains any fenced content, write it to a file instead and hand the Owner the path.

Rules:

1. **Write the full directive to a file**, then surface it to the Owner (file-send tool where available, otherwise the absolute path). Use a stable, session-appropriate location — the scratchpad directory is the default; a gitignored project directory is better when the pairing runs for many rounds and the files should survive.
2. **Always emit a short copy-paste prompt alongside the file.** Sub is a full agent with file access — it should *read* the directive, not receive it pasted. The Owner copies the prompt, not the document.
3. **The prompt must be self-contained**: the absolute path, an instruction to read it in full before acting, and the reporting expectation. It is the only thing the Owner pastes, so nothing essential may live outside it.
4. **Keep the Owner to a single source.** Either the whole directive is in the pasted text, or the whole directive is in the file and the pasted text is a pointer to it. Splitting substance across both is the failure this rule exists to prevent.

Prompt template:

> Read `[absolute path]` in full — it is a directive from Prime. Execute every item in it, respect the write boundary it states, and report back in the shape it specifies. Paste each command, its exit code, and its verbatim output in the same turn — no claim without the output. Do not act on any part of it before reading the whole file.

Add one line naming what the file contains when it carries a correction, so Sub does not skim: *"It includes corrections to your last report; read those before the directive items."*

#### Worked Micro-Example

> **Channel 1 (Owner Brief):**
>
> ## 🛑 Holding the data collection
> The new recorder reads 300× faster, which fixes the original problem. But it also revealed that every reading carries a timing error larger than the effect we are trying to measure. Collecting more data now would give us a confident-looking answer built on a broken ruler — so I asked Sub to calibrate the clock before gathering anything else.
>
> **Channel 2 (Sub Directive):**
>
> ```markdown
> Cadence resolved: 1,727.8 ms -> 5.84 ms median. Do not begin collection.
> Blocker: staleness `ts_recv - ts_venue` median 684.52 ms, IQR 226.93 ms, vs `tau_post = 150 ms`.
> Jitter exceeds the modeled effect; the quorum clock does not start until it is decomposed.
> Write boundary: read + run only. No edits this round.
> 1. Invoke `systematic-debugging`. Separate host clock offset from true venue publish
>    latency. Report offset-corrected distribution with N and IQR. Tag every value
>    MEASURED / ASSUMED / DERIVED.
>
> Paste each command, its exit code, and its verbatim output in the same turn. No claim without the output.
> ...
> ```

Sub's reply to this directive is the filled example in Step 3 — the two examples form one round trip.

---

### Step 5: Correction Protocol (Mandatory for Prime)

Sub does not improve from a directive that silently absorbs its mistakes. Every substandard report is a training opportunity, and the cost of skipping it is paid again on every future round. Prime therefore has standing authority — and an obligation — to criticize Sub's output explicitly.

Vague dissatisfaction ("be more thorough next time") changes nothing. Only a quoted failure paired with a concrete replacement changes behavior.

#### 5.1 When to open a correction block

Open one whenever Sub's report does any of the following:

- **Omits** something the directive asked for, without declaring it omitted.
- **Substitutes a summary for the artifact** — reports a conclusion where the directive asked for a quote, a count, a diff, or raw output.
- **Mislabels evidence** — tags something MEASURED that is ASSUMED or DERIVED, or cites a weak source as authoritative.
- **Ignores an instruction** — violates the write boundary, skips a named skill, commits when told not to.
- **Declares cleanliness it did not verify** — "no contradictions," "all tests pass," "nothing else changed" without the check behind it.
- **Self-attests verification** — "verified," "reviewed," or "no issues found" without pasting the output and exit code that produced the verdict.
- **Cites a source that does not contain the claim.** The most common form: naming a plausible file or SDK without opening it.
- **Answers a different question than the one asked**, including a narrower one.

One correction block per distinct failure — each gets its own quote and its own replacement.

#### 5.2 The correction block format

Every correction carries three parts, in this order. All three are mandatory; a block missing the GOOD part is a complaint, not a correction.

```markdown
**[Short failure name].** You wrote:

> [verbatim quote from Sub's report — the actual text, not a paraphrase]

[Why this fails, mechanically. Name the specific consequence: what decision it
would have corrupted, what round-trip it costs, what risk it hides.]

**GOOD** — what that answer should have looked like:

> [a concrete, fully-formed example of the correct reply. Real field names, real
> numbers or realistic placeholders, the exact structure expected. Long enough
> that Sub can pattern-match it next time.]
```

Rules for the quote: verbatim and attributable. Quote Sub's error exactly — a paraphrase lets Sub dispute the characterization instead of absorbing the lesson, and it hides whether Prime read the report carefully.

Rules for the GOOD example: it must be *usable as a template*, not a description of one. "Should have included the source" is not a GOOD example. A block showing file path, line numbers, the quoted source lines, and the verdict tag **is**.

#### 5.3 Prime patches it himself

**When Prime finds a discrepancy, it fixes it — now, in this turn.** If Sub built or coded something wrong and Prime can see the defect, Prime patches it: write the fix, save it, verify it, and take the answer.

Re-requesting is the single most expensive mistake available to Prime. It spends a full relay cycle — an Owner copy-paste, a Sub execution, another Owner copy-paste, another Prime turn — to obtain a fix Prime could often have produced directly. It also teaches Sub that broken work is survivable.

When reading Sub's report reveals a defect or gap, resolve it in this order:

1. **Can Prime fix it directly?** A wrong line of code, a mislabeled figure, a missing check, a file Sub cited but never opened — if Prime can see the fix, **Prime writes it now**: edit, save, verify (typecheck, run the test, re-read the source), and record the result. The fix stays in this turn.
2. **Does it require sustained mechanical work** — a long run, a multi-step build, a broad sweep Prime cannot finish in this turn? → Delegate, but as a *first* request, not a repeat. If it is technically a repeat, say so explicitly and attach a correction block for the omission.
3. **Is it blocked on the Owner?** → Ask the Owner once, in Channel 1, and give Sub independent work in the meantime.

Prime patches wherever it can and wherever it judges it worth a turn — one line or a whole function, a wrong build or a wrong report. The rule that Prime does not do mechanical work yields to this rule: a patch costs Prime far less than a relay cycle costs the pairing, and conserving Prime's turns serves throughput, not purity.

**After the patch, return to the Prime seat.** The moment the fix is saved and verified, Prime steps back into the architect role and continues the flow — read Sub's next response or check the next work item — by shipping the next directive through both channels (Step 4). Patching is an intervention, not a seat change: Prime does the repair once, then resumes issuing directives and does not absorb further mechanical load.

#### 5.4 Mandatory disclosure when Prime completes Sub's work

When Prime finishes something Sub was asked to do, Prime must say so — to the Owner *and* to Sub. Silent completion hides the role drift and lets it recur.

The disclosure to Sub carries four parts:

1. **What Prime did, and the result.** Present the finding as data, exactly as Sub should have.
2. **That this was Sub's item.** Name it: *"This was item N of the previous directive."*
3. **A correction block** (§5.2) for the original omission.
4. **The forward rule** — one sentence stating what Sub should do in this situation next time.

Template:

```markdown
## I COMPLETED THIS MYSELF — item [N] of the previous directive

[The finding, as data. Exact paths, quotes, numbers, tags.]

**Why I did it rather than re-asking:** [one line — it was a single file read /
Sub's code was wrong and the fix was visible / it cost me less than a relay cycle].

**[Failure name].** You wrote:

> [verbatim quote]

[Mechanical consequence.]

**GOOD** — what that answer should have looked like:

> [concrete template]

**Forward rule:** [one sentence, imperative. e.g. "When you name a source, open
it and quote the line, or report that you could not open it — never cite a file
you have not read."]
```

Never let the disclosure read as an apology or as Prime taking over. It is a hand-back: Prime closed the gap once, then returns to the Prime seat (Step 4) and Sub owns the pattern from here.

#### 5.5 Correction is not the whole message

A correction block is a section of a Prime turn, never the entire turn. Every turn still ships both channels (Step 4) and still ends with a forward directive. A turn that only criticizes leaves the Owner with nothing to relay and Sub with nothing to execute.

Order within Channel 2: accept what was good first (one line, specific), then corrections, then the new directive. Leading with criticism when three of four items were excellent misrepresents the state of the work and degrades Sub's calibration of what Prime actually values.

Channel 1 must surface corrections too, in plain language — the Owner needs to know when the pairing is losing cycles to rework, and a correction visible only inside the fenced block is invisible to the person paying for the relay.

#### 5.6 Symmetry — Sub receiving correction

When operating as **Sub** and receiving a correction block:

- Absorb the correction and apply it in the same report — without apology or restating the lesson.
- If the quote is accurate and the reasoning holds, accept it and continue.
- If Prime's correction is factually wrong, say so **with the evidence**, in one paragraph, and continue executing the directive regardless.

---

## Anti-Patterns

The table indexes each failure to the rule that governs it; the full rule lives in the step named, so the steps stay the single source of truth.

| Anti-Pattern | Why It Fails | Rule |
| :--- | :--- | :--- |
| **Sub commanding Prime** | Inverts authority and burns the scarce reasoning quota on conversational ping-pong. | → Step 3 (Sub rule 1) |
| **Prime doing mechanical coding** | Spends the constrained budget on boilerplate the cheaper model can write and test locally. | → Step 3 (Prime rule 1); patch exceptions in §5.3 |
| **Assigning Prime to the model with better tools** | Tool access is a Sub qualification. Seating the tool-rich model as Prime puts the scarce reasoning budget in the seat doing the mechanical work. | → Step 1 (Seat Assignment Criteria) |
| **Asking about Sub's quota or tool access** | Sub's turns are the resource the pairing is built to spend, and full agent capability is the default. Both questions waste an Owner round and imply the wrong economics. | → Step 1 (questions 3–4) |
| **Sub economizing its own turns** | Sub skipping a check to "save effort" forces a second relay cycle, which costs a Prime turn — the expensive one. | → Step 3 (Sub rule 3) |
| **Re-issuing a completed directive** | Spends a relay round-trip and a full Sub execution re-doing finished work. | → Step 2; Step 3 (Prime rule 2) |
| **Unverified claims** | Reporting results before running the suite or reading the rows produces hallucinated progress. | → Step 3 (Sub rule 2) |
| **Self-attested verification** | “Verified” or “no issues found” with no pasted output is a reconstruction of the check, not the check — it reads as done while nothing ran. | → Sub Reply Contract (items 2–3); Step 5.1 |
| **Concurrent writes to one tree** | Both agents are full agents pointed at the same directory; simultaneous edits corrupt state and produce untraceable diffs. | → Default Capability Assumption; Step 3 (Prime rule 4) |
| **Re-specifying a skill in prose** | Burns a Prime turn restating a maintained workflow, and drifts from the current version of it. | → Step 2b |
| **Nuance negotiation** | Long conversational debates over minor styling. | → Template A, item 5 |
| **Assuming who is in the pairing** | Seats change between sessions and projects. Guessing from memory produces a handshake addressed to the wrong model with the wrong constraints. | → Step 1 |
| **Single-channel output** | Emitting only the directive forces the Owner to relay decisions they cannot evaluate; emitting only the brief produces nothing to relay. | → Step 4 |
| **Jargon leaking into the Owner Brief** | The brief exists so the Owner can approve or veto. Untranslated terms turn it into a second copy of the directive. | → Step 4 (Channel 1) |
| **Silent absorption of a bad report** | Prime works around Sub's omission without naming it. Sub never learns, and the same gap recurs every round at compounding cost. | → Step 5.2 |
| **Vague criticism** | "Be more rigorous" gives Sub nothing to pattern-match. Behavior does not change from an adjective. | → Step 5.2 |
| **Re-requesting what Prime could fix himself** | Burns a four-hop relay cycle (Owner → Sub → Owner → Prime) to obtain a fix one file edit or one command would have produced, and signals that broken work is survivable. | → Step 5.3 |
| **Patching without returning to the Prime seat** | Prime fixes Sub's work but stays in the mechanical role, absorbing more load each round until the pairing inverts. | → Step 5.3 |
| **Prime silently doing Sub's work** | Closes the gap but hides the role drift, so Sub keeps under-delivering and Prime keeps absorbing mechanical load until the pairing inverts. | → Step 5.4 |
| **Correction with no forward directive** | A turn that only criticizes leaves the Owner nothing to relay and Sub idle for a full cycle. | → Step 5.5 |
| **Fenced code inside a fenced directive** | Inner fences close the outer block, so the Owner cannot tell what to copy and relays a truncated or padded message. | → Step 4.1 |
| **Handing over a file with no prompt** | The Owner is left to invent the framing, and Sub may skim, act on part of it, or ignore the write boundary stated inside. | → Step 4.1 |
| **Citing a source without opening it** | Naming a plausible SDK, doc, or file produces a confident claim with no evidence behind it, and Prime may act on it. | → Step 5.1; Step 5.4 |
| **Asking Sub to paste source Prime can read** | A model asked to reproduce text it does not have in front of it generates the most plausible version — a confabulation that looks right until diffed against the real file. And Prime already has the repo, so the relay spends a cycle on a read Prime could do in seconds. | → Step 3 (Prime rule 5); Sub Reply Contract |
| **Handing Sub an unsequenced feature** | A whole module in one directive is several decisions with no checkpoint between them; the later items get rushed or guessed, and a misread scope is only discovered after the code exists. | → Step 4 (Channel 2 — bound the milestone); Step 3 (Sub rule 6) |
