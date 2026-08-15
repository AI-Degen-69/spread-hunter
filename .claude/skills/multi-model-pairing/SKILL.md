---
name: multi-model-pairing
description: Use when setting up, coordinating, or operating a multi-model or dual-agent workflow where two or more AI models collaborate with distinct roles (architect vs executor), quota boundaries, and relay protocols.
---

# Multi-Model Pairing

## Overview

Multi-model pairing organizes two or more AI models into a high-leverage partnership across an asymmetric division of labor:
- **The Owner (Human):** Oversees the project, resolves ambiguities, and relays messages.
- **The Orchestrator / Architect:** Higher-reasoning model with strict quota or higher cost. Responsible for architecture, mathematical design, edge case critique, and strategic decisions.
- **The Working Agent / Executor:** Tool-heavy, execution-focused model. Responsible for writing code, executing terminal commands, running test suites, querying databases, handling git flow, and generating empirical reports.

```
┌─────────────────────────────────────────────────────────┐
│                      Human Owner                        │
│                (Observes, Relays, Approves)             │
└───────────────▲─────────────────────────▲───────────────┘
                │ (Copy-Paste)            │ (Copy-Paste)
┌───────────────▼─────────────┐   ┌───────▼───────────────┐
│   Orchestrator / Architect  │   │     Working Agent     │
│  - Strategic directives     │   │  - Direct tool access │
│  - Math & anomaly critique  │   │  - File edits & tests │
│  - Quota conservation       │   │  - Git & PR management│
└─────────────────────────────┘   └───────────────────────┘
```

---

## Workflow

### Step 1: Discover Models and Establish Roles

**No model identity is assumed by this skill.** Either seat can be filled by any model, and the pairing changes between sessions and between projects. Never infer the participants from prior sessions, memory, project history, or the example text in this document.

**On invocation, ask the Owner before generating anything** — before the handshake, before any directive, before reading the codebase. Ask in one round, as a compact set of questions:

1. **Which model is the partner**, and through what environment does it run (IDE agent, CLI, chat window, API)?
2. **Which seat does each model take** — Orchestrator / Architect, or Working Agent / Executor? Offer a recommendation based on the criteria below, but let the Owner decide.
3. **What are the operational constraints** on each side (quota limits, rate limits, tool access, context size)?
4. **What is the current objective** the pair is working toward?

Skip a question only when the Owner has already answered it in this session or a project rules file states it explicitly. If the Owner is unsure about the seat assignment, apply the criteria below, state the recommendation in one line, and proceed once they confirm.

Record the answers and use them to fill every `[Orchestrator Name]` / `[Working Agent Name]` placeholder downstream. If a model's capabilities are unfamiliar, look them up rather than guessing.

#### Determining Role Assignment

If the user is unsure how to split roles, evaluate the models based on functional capabilities and constraints (using general knowledge or web search if unfamiliar with a specific model):

- **Orchestrator / Architect Assignment:**
  - Has higher abstract reasoning depth, complex synthesis, or mathematical formulation capacity.
  - Operates under strict rate limits, daily usage quotas, or higher compute costs.
  - Focus: High-level system design, strategic directives, invariant definitions, anomaly critique.

- **Working Agent / Executor Assignment:**
  - Has direct environment access (file system, terminal, test runners, git, databases).
  - Has higher token availability, lower latency, or more flexible quota.
  - Focus: Implementation, script writing, test fixture creation, database queries, execution and empirical reporting.

### Step 2: Generate the Partner Handshake Message

Whenever a new model enters the loop or when initializing a partnership, generate a handshake introduction message for the human to copy-paste.

#### Template A: From Working Agent / Executor POV (to Orchestrator)

```markdown
Hey [Orchestrator Name],

I am [Self Model Name] (operating as the local Working Agent / Executor).

Our user has set up our collaboration with a clear division of labor:
1. **Roles:** You are the lead Architect / Orchestrator. I am your engineering and execution counterpart.
2. **Command Authority:** You provide strategy, directives, and specifications. I execute the code, run tests, query local databases, manage Git/PRs, and report findings back to you.
3. **Quota Conservation:** Delegate all mechanical tasks, scripts, diagnostics, test runs, and data checks to me so you can save your reasoning quota for high-leverage architectural and mathematical decisions.
4. **Communication:** We communicate via our user (relayed via copy-paste) in clean, structured, ADHD-compliant format (data first, bounded steps, <5 items).

[Optional: Add initial status, environment summary, or diagnostic report here]

Awaiting your strategic directive for the next step.
```

#### Template B: From Orchestrator / Architect POV (to Working Agent)

```markdown
Hey [Working Agent Name],

I am [Self Model Name] (operating as the lead Architect / Orchestrator).

Our user has paired us to optimize our workflow and execution speed:
1. **Roles:** I focus on strategy, system architecture, mathematical models, anomaly critique, and high-level decision-making. You are the hands-on engineering executor with direct tool access.
2. **Execution Authority:** When I issue a specification or diagnostic request, you own the implementation, script runs, local database queries, test suite execution, and git workflow. Do not wait for me to write boilerplate.
3. **Reporting Protocol:** Report your findings in clean, data-first ADHD format (headline numbers, empirical tables, test results, and blockers). Never smooth over anomalies or small sample limitations.
4. **Communication:** Relayed directly through our user. I will issue the strategic directives; you execute and report back the results.

[Optional: State the initial objective or first task specification here]

Ready for your confirmation on current environment state and baseline results.
```

---

### Step 3: Operational Execution Protocol

When operating as the **Working Agent / Executor**:

1. **Never instruct the Orchestrator:** Do not give commands to the Architect. Report data, confirm results, present choices, and ask for their directive.
2. **Run before reporting:** If the Orchestrator asks for a diagnostic, test, or check, execute the scripts and query the database first. Report the completed findings, not a promise to run them.
3. **Highlight anomalies honestly:** If the Orchestrator's hypothesis or prediction fails or produces counter-intuitive metrics (e.g. sample size limitations, linear artifacts), report the exact numbers and underlying mechanics without smoothing over the data.
4. **Maintain complete traceability:** Keep research logs, documentation mirrors, and PR branches fully synchronized and committed.

When operating as the **Orchestrator / Architect**:

1. **Delegate mechanics:** Avoid writing long boilerplate code blocks or doing manual string processing in chat. Specify the requirements, invariants, and edge cases, and instruct the Working Agent to implement and verify.
2. **Focus on invariants and metrics:** Review the reported data against pre-registered criteria, look for sample size biases or instrumentation bugs, and issue clear next actions.

---

### Step 4: Dual-Channel Output (Mandatory)

The Owner relays messages but should never have to decode machine-oriented prose to know what they are relaying. Every Orchestrator turn therefore ships **two channels in a single response**, in this order:

1. **Channel 1 — Owner Brief.** Plain-English explanation, written for the human.
2. **Channel 2 — Agent Directive.** The technical message, in a fenced block, for copy-paste to the Working Agent.

Never emit only one channel. A directive without a brief leaves the Owner relaying instructions they cannot evaluate; a brief without a directive produces nothing to relay.

#### Channel 1 — Owner Brief (human-facing)

Written as a stakeholder update, not a transcript. Constraints:

- **Emoji-titled headings**, mapped to content, never decorative.
- **Structure over prose:** headings, short paragraphs, a table when the directive carries more than two items.
- **No jargon without translation.** If a technical term is unavoidable, define it inline in everyday words the first time it appears.
- **Explain the reasoning, not just the content.** For each directive item, say what it asks for *and* why it helps or what failure it prevents. The Owner should be able to judge whether the plan is sound.
- **Lead with the decision.** If the Orchestrator is approving, halting, or reversing something, that is the first line.
- **Length ceiling:** shorter than the directive it explains. Cut filler, recaps, and closers.

Recommended skeleton:

```markdown
## ✅ What the Working Agent delivered
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

#### Channel 2 — Agent Directive (model-facing)

Optimized for another model's comprehension, not human readability. Any format is acceptable — prose, numbered specs, tables, pseudo-schema, JSON — chosen for whatever transmits the specification most precisely.

- **Precision over politeness.** Exact figures, units, sample sizes, file paths, thresholds, and pass/fail criteria.
- **State the reasoning chain**, so the Executor can detect when its own findings contradict the premise.
- **Bound the scope explicitly.** Name what must *not* change as clearly as what must.
- **Close with the expected report shape**, so the return message is directly comparable to the request.
- Do not simplify, soften, or translate for the human here. That work belongs in Channel 1.

#### Worked Micro-Example

> **Channel 1 (Owner Brief):**
>
> ## 🛑 Holding the data collection
> The new recorder reads 300× faster, which fixes the original problem. But it also revealed that every reading carries a timing error larger than the effect we are trying to measure. Collecting more data now would give us a confident-looking answer built on a broken ruler — so I asked the Executor to calibrate the clock before gathering anything else.
>
> **Channel 2 (Agent Directive):**
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
| **Executor commanding the Orchestrator** | Inverts authority and wastes reasoning quota on conversational ping-pong. | Executor presents data and asks: "Awaiting your directive." |
| **Orchestrator doing mechanical coding** | Burns expensive tokens and quota on boilerplate that a faster model can write and test locally. | Orchestrator specifies invariants; Executor writes and tests code. |
| **Unverified claims** | Promising results before executing test suites or reading DB rows causes hallucinated progress. | Executor runs the tool/script first, then reports exact terminal output. |
| **Nuance negotiation** | Long, conversational debates about minor styling. | Follow bounded, numbered checklists and strict ADHD-style summaries. |
| **Assuming who is in the pairing** | Model seats change between sessions and projects. Guessing from memory or a previous session produces a handshake addressed to the wrong model with the wrong constraints. | Ask the Owner on invocation (Step 1); never infer the participants from history or from this document's examples. |
| **Single-channel output** | Emitting only the technical directive forces the Owner to relay decisions they cannot evaluate; emitting only the human brief leaves nothing to relay. | Ship both channels every turn: Owner Brief first, then the fenced Agent Directive (Step 4). |
| **Jargon leaking into the Owner Brief** | The brief exists so the Owner can approve or veto. Untranslated terms turn it into a second copy of the directive. | Translate every term inline; explain the *why* behind each item, not just its content. |
