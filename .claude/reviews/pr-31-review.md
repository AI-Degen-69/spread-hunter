# PR Review: #31 — Session 65-68: WS recorder, relayer redeem, latency constants, and the income-decomposition finding

**Reviewed**: 2026-08-16
**Author**: AI-Degen-69
**Branch**: session-65-ws-recorder → main
**Decision**: REQUEST CHANGES (1 HIGH)

## Summary

72 files, +4,384/−14,504. The bulk of the deletion is hygiene (12k lines of runtime logs, stale
report artifacts). Source risk concentrates in one file: `strategy/live_exec.py` (+765), the only
path that spends real assets. Its dry-run guard, resolution guard and secret handling are sound.
One real defect: the relayer submit call is unguarded and the audit log is written only on success,
so a failed submit leaves an ambiguous on-chain state with no record.

## Findings

### CRITICAL

None. No hardcoded secrets — every key is read from the environment (`live_exec.py:77,381,437`),
and the module header's claim that "nothing in this module prints, logs, or writes a key" holds
under inspection. The 64-hex strings flagged by pattern scan are public transaction hashes and
condition IDs, not key material. The relayer API key travels only in a request header and is never
printed, including in the nonce-fetch exception path.

### HIGH

**1. `strategy/live_exec.py:486-498` — relayer submit has no error handling and logs only on success.**

```python
with urllib.request.urlopen(req_submit, timeout=30) as resp:
    res = json.loads(resp.read().decode("utf-8"))

_log_order({...})
```

The nonce fetch 25 lines above is wrapped in `try/except` with a clear `SystemExit`. The submit call
is not. Three consequences, in order of severity:

- An `HTTPError` or timeout fires **after the transaction is signed and sent**. Whether the relayer
  broadcast it is unknown client-side — a 504 on the response says nothing about what happened to
  the request.
- `_log_order` runs only if the call returns cleanly, so the one case where you most need an audit
  row — an ambiguous submit — is the case that writes nothing to `run/live_orders.json`.
- The operator sees a raw traceback rather than an actionable message, unlike every other failure
  path in this function.

Fix: wrap the submit, and write the log row *before* the call with the signed payload and a
`pending` status, updating it with the response or the exception afterwards. That makes the record
survive any outcome.

### MEDIUM

**2. Seven new analysis scripts contradict the hygiene policy this branch adds.**

`AGENTS.md:67` — added in this same branch (commit `0b9266e`) — states: *"Scratch scripts,
diagnostic dumps, one-off analysis snippets, and temporary mockups must be deleted as soon as their
output has been verified."* This PR adds `scripts/audit_all_markouts.py`,
`diagnose_phase1_checks.py`, `eval_post_latency_sensitivity.py`, `eval_tau_sensitivity.py`,
`inspect_fill_diffs.py`, `query_onchain_balances.py`, `analyze_ws_staleness.py`.

`analyze_ws_staleness.py` is a reusable tool and earns its place. The other six read as one-off
analyses, and by this PR's own Session 68 finding they analyse parameters now marked PARKED — they
measure the `sell` path, which has earned $0.00. Either delete them or record in `AGENTS.md` which
part of `scripts/` is exempt.

**3. `strategy/live_exec.py:499` — unbounded response print.**

`print(f"  RELAYER RESPONSE: {res}")` prints the full decoded response while `_log_order` truncates
the same value to 400 chars. Inconsistent, and an unexpectedly large relayer body floods the
terminal. Truncate to match.

### LOW

**4. `strategy/live_exec.py:447` — `import urllib.request` inside the function.** The deferred
imports elsewhere in this file carry a comment explaining why; this one does not.

## Validation Results

| Check | Result |
|---|---|
| Type check | Skipped — no mypy/pyright config in repo |
| Lint | Skipped — no ruff/flake8 config in repo |
| Tests | **Pass** — `python -m pytest tests/ -q` → 716 passed, 1 pre-existing warning (starlette/httpx deprecation, not from this PR) |
| Build | N/A — no build step |

## What is well done

- The dry-run path (`live_exec.py:410-425`) builds the submit payload with a placeholder nonce and a
  zeroed 65-byte signature, so no real signature is ever produced without `--live`. Preview without
  capability is the right shape.
- The resolution guard (`:428-435`) distinguishes "RPC unreachable" from "condition unresolved",
  requiring an explicit `--skip-resolution-check` for the former while never allowing a bypass of
  `denom == 0`.
- `--live` uses `argparse.SUPPRESS` on every subcommand so the global default cannot be silently
  overwritten to `False` by a subparser.
- Test coverage lands with the code: `tests/test_live_exec.py` +408, `tests/test_fills.py` +349.

## Files Reviewed

| File | Change | Focus |
|---|---|---|
| `strategy/live_exec.py` | Modified +765 | Full read — secrets, signing, guards, error paths |
| `strategy/fills.py` | Modified +214 | Latency-parameter changes |
| `strategy/stats.py` | Modified +13 | Calendar gate removal |
| `strategy/config.py` | Modified +35 | Latency constants |
| `scripts/*.py` | Added x7 | Policy compliance, secret scan |
| `tests/test_live_exec.py`, `tests/test_fills.py` | Added +757 | Coverage of new paths |
| `run/*.log`, stale report artifacts | Deleted -12,254 | Hygiene, no source impact |
| `research/*` | Modified +889 | Documentation only |
