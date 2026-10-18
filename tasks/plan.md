# Station II plan — #657 [IMPROVE] Differential live_marks subscription frames

**Issue:** https://github.com/AI-Degen-69/spread-hunter/issues/657
**Branch:** `i657/differential-live-marks-frames` (from `main` 6b0b191, clean, synced)
**Size:** Small (one method + one test file; zero changes to other modules)
**Scope guard:** #657 only. `sync_subscription` sends delta frames. No move of
`live_marks` into `market_stream.py`, no dashboard render change, no mark-math
change. Full-suite `pytest` stays the CI merge gate, never the local loop.
**Task type:** Code + Performance (kill 1–5s reconnect lag on token rotation)

## 0. Grounded seams (verified verbatim — no guesswork)

- `core_brain/live_marks.py:266-270` — `subscribe_payload()` returns
  `json.dumps({"assets_ids": sorted(wanted), "type": "market"})` (initial full
  subscribe on fresh socket, unchanged).
- `core_brain/live_marks.py:402-409` — `_send_subscribe(app)` sends the full
  payload, sets `_sent_ids = frozenset(wanted)`, returns bool.
- `core_brain/live_marks.py:411-437` — `sync_subscription()` today: `None` app
  → `False`; equal set → `False`; else `app.close()` → `True`. Docstring
  (419-426) claims the venue answers a second subscribe with INVALID OPERATION —
  **stale**: measured with a repeated `type: market` frame, not an `operation`
  frame. The docstring is rewritten, not preserved.
- `core_brain/live_marks.py:323` — `self._sent_ids: frozenset | None = None`
  (per-socket baseline; `None` = nothing sent on this socket yet).
- `core_brain/market_stream.py:261` — proven in-repo seam:
  `self._send_frame(app, {"assets_ids": fresh, "operation": "subscribe"})`.
- `core_brain/market_stream.py:291-292` — proven in-repo seam:
  `self._send_frame(app, {"assets_ids": removed, "operation": "unsubscribe"})`.
- `tests/test_live_marks.py:124-200` — drift test pins today's close-and-redial
  (`closed == True`, 2 apps, full resubscribe). It is **rewritten** into delta
  assertions (deliberate, reason recorded here).
- `tests/test_live_marks.py:214-231` — missing-`websocket` loud-failure test;
  untouched, must stay green.
- Must NOT be modified: `core_brain/market_stream.py`, `dashboard/server.py`,
  `LiveMarkCache` mark math, `_run_venue` reconnect loop, `WANTED_TTL_SEC`.


## 0A. CodeRabbit plan — adopted with changes

Read 2026-10-18. Adopted: delta frames on open socket, sorted ids, subscribe
before unsubscribe, per-frame `_sent_ids` update, send-failure → close socket
(existing reconnect loop recovers with full set), `_sent_ids is None` → keep
close path, catch only `OSError` + `websocket.WebSocketException`, no worker
lock, no cross-module import, `assets_ids` + `operation` keys exactly.
Changed: its `tasks/plan.md [UNVERIFIED]` note refers to a file absent on `main`
(pruned by #648) — dropped. Its "public examples" line is unverifiable here —
replaced by the two in-repo `market_stream.py` seams plus the issue's #648
wire-probe claim, flagged [UNVERIFIED-in-repo] until a live probe re-confirms.

## 0B. Open question resolved from code

Q: subscribe-then-unsubscribe, or unsubscribe-then-subscribe?
A: Subscribe first (matches `market_stream.py` ordering, avoids a window where
a still-held token has no subscription). No operator question needed.

## 1. Spec (embedded — Small tier, no SPEC.md)

`sync_subscription` computes `added = wanted − sent`, `removed = sent − wanted`
and sends sorted differential frames on the live socket without closing it.
Return `True` iff ≥1 frame sent cleanly. Reconnect is strictly for transport
drops / heartbeat / socket errors.

Acceptance: delta frames on live connection with no close; unit tests verify
deltas; `python -m pytest -q tests/test_live_marks.py` passes (background).

Edge contract: `app is None` → `False`; `_sent_ids is None` → keep close path
(reconnect recovers with full set); `wanted == sent` → `False`, zero frames;
empty side → no frame; send raises `OSError`/`WebSocketException` → close,
`False`, `_sent_ids` unchanged; untouched tokens keep marks/books/generation
(no `disconnect()`, no cache drop).

Out of scope: moving `live_marks` into `market_stream.py`, dashboard UI, mid
math, TTL tuning, shared cross-module helper (private helper in `live_marks.py`
only).

## 2. Constraints (locked)

- Zero regressions: `tests/test_live_marks.py` green; every new behaviour has a
  test failing without the change (baseline red-check first).
- Anti-cheat: no skipped/disabled tests, no deleted assertions except the
  deliberate drift-test rewrite, no lint suppressions.
- No new dependencies. `data/orders.db` untouched. No live commands by agent.
- Frame keys exactly `assets_ids` + `operation`; ids sorted per frame.
- Docstring rewrite states: repeated `type: market` → INVALID OPERATION (old
  note, still true); `operation` membership frames are the supported path.

## 3. Interfaces (frozen)

`sync_subscription(self) -> bool` — signature + return contract unchanged.
True = delta frame(s) sent cleanly (socket stays up). False = no-op (no app /
equal set) or failure (close kicked, loop recovers).

Wire frames (sorted ids): subscribe / unsubscribe with `assets_ids` +
`operation` keys. No new API/schema/config.

## 4. Improvement (adopted — hardening, not expansion)

Kept-token marks must survive sync (mid intact, `reset_generation` unchanged)
— pins the "no reset" property the change exists for, using only existing
`apply_message` + `snapshot` seams.

## 5. Tasks (dependency order, risk-first)

- [x] T1 (S, [Backend/Logic], focused pytest) — RED baseline + test rewrite.
  Rewrite drift test into delta cases: unchanged→no frame/False/no close;
  add→1 subscribe/True/no close/1 app/sent=={a,b}; remove via TTL clock→1
  unsubscribe; mixed→subscribe-before-unsubscribe; kept-mark→mid intact and
  generation unchanged; send-fail→False/closed/baseline kept/second app opens
  full type:market frame. Focused suite → new cases FAIL. Depends on: none.
- [x] T2 (S, [Backend/Logic], focused pytest) — GREEN implementation. Rewrite
  `sync_subscription` per section 1 + docstring; private frame helper, no
  import from `market_stream.py`. Focused suite → all pass. Depends on: T1.
- [x] T3 (XS, [Backend/Logic], focused re-run) — regression sweep of the file;
  missing-websocket + cache tests untouched-green. Depends on: T2.

Checkpoints: after T1 (red proof); after T3 (green file, handoff to build).

