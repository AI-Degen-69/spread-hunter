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

When setting up or starting a multi-model session, verify the pairing roles. If not already specified in project rules, determine:

1. **What model is running locally (Self)?**
2. **What partner model is collaborating (Partner)?**
3. **What are the operational constraints (quota limits, rate limits, tool capabilities)?**
4. **Who is the Orchestrator / Architect, and who is the Working Agent / Executor?**

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

## Anti-Patterns

| Anti-Pattern | Why It Fails | Correct Action |
| :--- | :--- | :--- |
| **Executor commanding the Orchestrator** | Inverts authority and wastes reasoning quota on conversational ping-pong. | Executor presents data and asks: "Awaiting your directive." |
| **Orchestrator doing mechanical coding** | Burns expensive tokens and quota on boilerplate that a faster model can write and test locally. | Orchestrator specifies invariants; Executor writes and tests code. |
| **Unverified claims** | Promising results before executing test suites or reading DB rows causes hallucinated progress. | Executor runs the tool/script first, then reports exact terminal output. |
| **Nuance negotiation** | Long, conversational debates about minor styling. | Follow bounded, numbered checklists and strict ADHD-style summaries. |
