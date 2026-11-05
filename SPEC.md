# SPEC - #942 Size order positions with the Kelly criterion

## Goal

Make order sizing at quote time derive from the Kelly criterion (the bankroll fraction
that maximizes long-run log growth) instead of the fixed couple-allocation heuristic
(1% of baseline bankroll, floored at $10), which is decoupled from measured edge. The
work is: research the criterion properly, capture it in a committed note, then wire it
into the quoting path as an opt-in sizing mode.

## Acceptance criteria

- [ ] Research note committed under `docs/backlog/` (`i942-kelly-criterion.md`) naming the
  formula family, sources, the chosen framing (a/b/c) with the rejected framings and
  why, and the Kelly fraction setting — before any sizing code.
- [ ] A Kelly sizing mode exists in the quoting path (`bot_quote_size_mode="kelly"`):
  order size derives from a Kelly fraction of the configured bankroll basis, floored at
  `bot_quote_size_min_shares`, clamped by the unchanged dynamic caps and the (1−u)²
  naked-budget taper.
- [ ] No-edge markets quote nothing: measured Kelly ≤ 0 ⇒ size 0, with the reason
  surfaced in the quote diagnostics.
- [ ] Insufficient-sample fallback: below the configured close minimum, behavior is
  byte-identical to `bot_quote_size_mode="dollars"` and the quote reason says so
  (`kelly fallback: insufficient sample (n closes)`).
- [ ] Flag off: mode not selected ⇒ zero change in every sizing output (covered by test).
- [ ] `python -m pytest -q tests/test_risk.py tests/test_quotes.py` exits 0 on an idle
  stack (on a live tournament expect the environmental `db-wal` teardown errors noted in
  the issue; a clean exit requires an idle stack).

## Edge cases

- Kelly inputs missing (not merged — rehearsal/replay/tests construct cfg directly) ⇒
  dollars-mode fallback, never a crash, never a fabricated number.
- n closes ≥ minimum but payoff_ratio NULL (no loss yet, or no wins) ⇒ Kelly
  unmeasurable ⇒ dollars-mode fallback with the reason recorded.
- Implied full Kelly > 1 ⇒ capped at 1 before the fractional multiplier applies; never
  lever up.
- Kelly ≤ 0 ⇒ size 0 with the no-edge reason; the `(1−u)²` taper and dynamic caps still
  apply on top of any positive Kelly size, unchanged in shape.
- `VENUE_SYNC_RUN_ID` closes are excluded from the Kelly sample under every filter —
  the wallet's sync history is not the bot's trading record.
- `bot_quote_size_shares <= 0` still defunds every mode (defunded means silent).
- Strict-pairs light-side rule unchanged: the flattening side is never Kelly- or
  taper-cut below the deficit it must buy.

## Out of scope

- Rewriting the `risk_utilization` taper or the (1−u)² shape (naked-budget defense).
- Loosening, replacing or bypassing any existing cap (`bot_risk_exposure_max_*`,
  `bot_pos_price_max_pair_cost_usd`, venue floor). Kelly allocates inside them.
- A per-market resolution-probability model (directional `f* = (p−P)/(1−P)`). If the
  research note selects that framing instead of the measured-stats framing, the issue
  must be re-scoped in a comment before Station II plans.
- Dashboard risk surfaces (#934 is a separate lane); a read-only "Kelly fraction: …"
  line only if it falls out for free.
- The `_cents` field renames (#936 owns those; no name overlap — see plan).
- A config gate for "full Kelly only in shadow" — not enforceable from inside config;
  recorded as an operator rule in the research note, capped at 1.0 in code.
