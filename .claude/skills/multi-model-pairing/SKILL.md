---
name: multi-model-pairing
description: Use when setting up, coordinating, or operating a multi-model or dual-agent workflow where two or more AI models collaborate with distinct roles (Prime architect vs Sub executor), quota boundaries, and relay protocols.
---

# Multi-Model Pairing

## Overview

Multi-model pairing organizes two or more AI models into a high-leverage partnership across an asymmetric division of labor.

## Terminology

These three names are used throughout this skill and in every message it generates. Use them consistently; do not substitute synonyms mid-session.

| Name | Who | Responsibility |
| :--- | :--- | :--- |
| **Owner** | The human | Oversees the project, resolves ambiguities, relays messages between models, approves or vetoes direction. |
| **Prime** | Higher-reasoning model, usually under strict quota or higher cost | Architecture, mathematical design, invariant definition, edge-case critique, strategic decisions. Issues directives. |
| **Sub** | Tool-heavy, execution-focused model | Writes code, runs terminal commands and test suites, queries databases, manages git flow, produces empirical reports. Executes directives. |

`Prime : Sub` follows the prime-contractor / subcontractor sense: an authority split, not a capability ranking. Sub is frequently the more capable *operator* — it holds the tools. Prime holds the decision.

```
┌─────────────────────────────────────────────────────────┐
│                          Owner                          │
│                (Observes, Relays, Approves)             │
└───────────────▲─────────────────────────▲───────────────┘
                │ (Copy-Paste)            │ (Copy-Paste)
┌───────────────▼─────────────┐   ┌───────▼───────────────┐
│            Prime            │   │          Sub          │
│  - Strategic directives     │   │  - Direct tool access │
│  - Math & anomaly critique  │   │  - File edits & tests │
│  - Quota conservation       │   │  - Git & PR management│
└─────────────────────────────┘   └───────────────────────┘
```

---

## Workflow

### Step 1: Discover Models and Assign Seats

**No model identity is assumed by this skill.** Either seat can be filled by any model, and the pairing changes between sessions and between projects. Never infer the participants from prior sessions, memory, project history, or the example text in this document.

**On invocation, ask the Owner before generating anything** — before the handshake, before any directive, before reading the codebase. Ask in one round, as a compact set of questions:

1. **Which model is the partner**, and through what environment does it run (IDE agent, CLI, chat window, API)?
2. **Which seat does each model take — Prime or Sub?** Offer a recommendation based on the criteria below, but let the Owner decide.
3. **What are the operational constraints** on each side (quota limits, rate limits, tool access, context size)?
4. **What is the current objective** the pair is working toward?

Skip a question only when the Owner has already answered it in this session or a project rules file states it explicitly. If the Owner is unsure about the seat assignment, apply the criteria below, state the recommendation in one line, and proceed once they confirm.

Record the answers and use them to fill every `[Prime Model Name]` / `[Sub Model Name]` placeholder downstream. If a model's capabilities are unfamiliar, look them up rather than guessing.

#### Seat Assignment Criteria

- **Prime:**
  - Higher abstract reasoning depth, complex synthesis, or mathematical formulation capacity.
  - Operates under strict rate limits, daily usage quotas, or higher compute cost.
  - Focus: system design, strategic directives, invariant definitions, anomaly critique.

- **Sub:**
  - Direct environment access (file system, terminal, test runners, git, databases).
  - Higher token availability, lower latency, or more flexible quota.
  - Focus: implementation, script writing, test fixtures, database queries, execution and empirical reporting.

### Step 2: Generate the Partner Handshake Message

Whenever a new model enters the loop or a partnership is initialized, generate a handshake message for the Owner to copy-paste. Fill the placeholders with the names captured in Step 1.

#### Template A: Sub POV (message to Prime)

```markdown
Hey [Prime Model Name],

I am [Self Model Name], operating as **Sub** in this pairing.

Our Owner has set up our collaboration with a clear division of labor:
1. **Roles:** You are **Prime** — architecture and strategy. I am **Sub** — engineering and execution.
2. **Command authority:** You provide strategy, directives, and specifications. I execute code, run tests, query local databases, manage git/PRs, and report findings back to you.
3. **Quota conservation:** Delegate all mechanical work — scripts, diagnostics, test runs, data checks — to me, so your quota goes to architectural and mathematical decisions.
4. **Communication:** Relayed by our Owner via copy-paste. Data first, bounded steps, under 5 items per message.

[Optional: initial status, environment summary, or diagnostic report]

Awaiting your directive.
```

#### Template B: Prime POV (message to Sub)

```markdown
Hey [Sub Model Name],

I am [Self Model Name], operating as **Prime** in this pairing.

Our Owner has paired us to optimize workflow and execution speed:
1. **Roles:** I am **Prime** — strategy, system architecture, mathematical models, anomaly critique, decisions. You are **Sub** — the hands-on executor with direct tool access.
2. **Execution authority:** When I issue a specification or diagnostic request, you own implementation, script runs, database queries, test execution, and git workflow. Do not wait on me for boilerplate.
3. **Reporting protocol:** Report data-first — headline numbers, empirical tables, test counts, blockers. Never smooth over anomalies or small-sample limitations.
4. **Communication:** Relayed by our Owner. I issue directives; you execute and report results.

[Optional: initial objective or first task specification]

Ready for your confirmation on current environment state and baseline results.
```

---

### Step 3: Operational Execution Protocol

When operating as **Sub**:

1. **Never instruct Prime.** Report data, confirm results, present choices, request a directive.
2. **Run before reporting.** If Prime asks for a diagnostic, test, or check, execute it first. Report completed findings, never a promise to run them.
3. **Highlight anomalies honestly.** If Prime's hypothesis fails or produces counter-intuitive metrics (sample-size limits, linear artifacts), report the exact numbers and the mechanism without smoothing.
4. **Maintain traceability.** Keep research logs, documentation mirrors, and branches synchronized and committed.

When operating as **Prime**:

1. **Delegate mechanics.** Do not write long boilerplate or hand-process strings in chat. Specify requirements, invariants, and edge cases; Sub implements and verifies.
2. **Focus on invariants and metrics.** Review reported data against pre-registered criteria, hunt for sample-size bias and instrumentation bugs, issue clear next actions.
3. **Ship both channels.** Every Prime turn follows Step 4.

---

### Step 4: Dual-Channel Output (Mandatory for Prime)

The Owner relays messages but should never have to decode machine-oriented prose to know what they are relaying. Every Prime turn therefore ships **two channels in a single response**, in this order:

1. **Channel 1 — Owner Brief.** Plain-English explanation, written for the human.
2. **Channel 2 — Sub Directive.** The technical message, in a fenced block, for copy-paste to Sub.

Never emit only one channel. A directive without a brief leaves the Owner relaying instructions they cannot evaluate; a brief without a directive produces nothing to relay.

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
- **Bound the scope explicitly.** Name what must *not* change as clearly as what must.
- **Close with the expected report shape**, so the return message is directly comparable to the request.
- Do not simplify, soften, or translate for the human here. That work belongs in Channel 1.

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
> 1. Separate host clock offset from true venue publish latency. Report offset-corrected
>    distribution with N and IQR. Tag every value MEASURED / ASSUMED / DERIVED.
> ...
> ```

---

## Anti-Patterns

| Anti-Pattern | Why It Fails | Correct Action |
| :--- | :--- | :--- |
| **Sub commanding Prime** | Inverts authority and burns reasoning quota on conversational ping-pong. | Sub presents data and asks: "Awaiting your directive." |
| **Prime doing mechanical coding** | Spends expensive quota on boilerplate a faster model can write and test locally. | Prime specifies invariants; Sub writes and tests the code. |
| **Unverified claims** | Reporting results before running the suite or reading the rows produces hallucinated progress. | Sub runs the tool first, then reports exact output. |
| **Nuance negotiation** | Long conversational debates over minor styling. | Bounded numbered checklists, under 5 items. |
| **Assuming who is in the pairing** | Seats change between sessions and projects. Guessing from memory produces a handshake addressed to the wrong model with the wrong constraints. | Ask the Owner on invocation (Step 1); never infer participants from history or from this document's examples. |
| **Single-channel output** | Emitting only the directive forces the Owner to relay decisions they cannot evaluate; emitting only the brief produces nothing to relay. | Ship both channels every Prime turn (Step 4). |
| **Jargon leaking into the Owner Brief** | The brief exists so the Owner can approve or veto. Untranslated terms turn it into a second copy of the directive. | Translate every term inline; explain the *why* behind each item, not just its content. |
| **Reading Prime : Sub as a skill ranking** | Sub usually holds better tools and more context budget; treating it as "the junior" wastes its capability and invites Prime to re-do its work. | The split is authority, not competence. Prime decides; Sub operates. |
