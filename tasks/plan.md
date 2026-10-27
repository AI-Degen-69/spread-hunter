# Implementation Plan — Issue #856: Active strategy profile badge in Dashboard top nav

Branch: `i856/feat-display-active-strategy-profile-name-in-dashb` | Issue: #856

## Summary

Show the active engine profile (`balanced`, `queue-clear-5m`,
`balanced+queue-clear-5m`, `baseline`, `default`) as a `PROFILE` pill in the
top nav, fed by a new top-level `active_profile` field on
`GET /api/system/status`, refreshed every poll.

Right-sizing: **Standard** — one architectural decision (the engine records its
own profile; the dashboard only reads records), ~6 source files plus tests, no
new external dependency, no DB schema change. Task type: **Code** +
**Design/UI**. No `quick-fix` label on the issue → normal lane, no gate text.

## Code-review intake (CodeRabbit plan comment on #856)

One reading; the HTML-echo copy ignored. **Adopted:** engine-recorded profile
(heartbeat `profile` + `runtime/trader_profile.json`), shared
`describe_active_profile` helper, `active_profile` status field, `unknown`
fallback, live support ordered last, the listed test matrices. **Rejected:**
nothing — the plan is minimal and grounded; its 3-task split is kept as-is.
**[UNVERIFIED] resolved by spot-check:** every cited file path, function, and
line range below was read in the live tree; test-file line numbers it cites
(`test_shadow_run_stopwatch.py:166-365`, `test_dashboard_server.py:596-610` /
`1948-1959`, `test_ui_top_nav_consistency.py:37-45`,
`test_ui_top_nav_groups.py:126-138` / `198-216`,
`test_per_run_shadow_heartbeat.py:49-193`) were NOT re-read — the build must
locate the current anchors on read instead of trusting them.

## Seam verification (live tree, this session)

| Cited | Actual |
|---|---|
| `resolve_tournament_preset_name()` | `core_brain/config.py:620` ✓ raises `ValueError` on unknown |
| `TOURNAMENT_PRESETS["balanced"]` | dict at `config.py:574`, `"balanced"` at `:582` ✓ |
| queue gate flags | `bot_order_gate_queue_clear_on`, `bot_order_queue_max_clear_min` at `config.py:210-211` ✓ |
| heartbeat writer | `write_shadow_heartbeat` at `shadow_run.py:451`, `book_source_mix` pattern at `:513` ✓ |
| shadow startup cfg block | `shadow_run.py:1297-1331` region ✓ (`cfg_supplied`, `shadow_cfg()`) |
| heartbeat `_heartbeat` | `shadow_run.py:1662` ✓ |
| shadow reader | `_read_shadow_heartbeat_file` at `server.py:965`, `book_source_mix` passthrough at `:1078` ✓ |
| `get_system_status()` | `server.py:1506` ✓, payload through `timestamp` at `:~1652` |
| `/api/parameters` + `get_parameters` | `server.py:4212` ✓ (stays read-only) |
| top nav markup | `.top-group-source` at `index.html:82`, `#db-mode-badge` at `:85` ✓ |
| renderers | `renderCodeVersion` at `app.js:2087`, `renderDbMode` at `:2189`, `renderPillHtml` at `:301`, `esc` at `:445`, `pollStatus` at `:10853` ✓ |
| runtime paths | `resolve_runtime_file` at `runtime_paths.py:91`, `runtime_file` at `:113` ✓ |
| trader entry | `main()` at `trader_loop.py:3393`, `cfg = load()` + `db_path` in `:3424-3445` ✓ |
| harness | `tests/js/live_state_harness.cjs` exists ✓; `tests/test_active_profile.py` does NOT exist yet (greenfield, no collision) ✓ |

Drift corrected: the issue cites `app.js:2294-2330` and `/api/status` — the
renderers live at `app.js:2087-2236` and the endpoint is `/api/system/status`
(`app.js` re-serve fetch confirms). The dashboard must NOT call
`resolve_tournament_preset_name()` in its own process (env differs per
launcher); the issue's suggestion to do so is the one point overruled, with
reason recorded here.

## Resolved open questions

No `needs-answers` label and no Open-questions section on the issue. The one
genuine ambiguity — *where does the dashboard get the name?* — resolves from
code: `MakerConfig.from_env` (`config.py:311`) discards the preset name, and
the launcher strips `BOT_*` from dashboard processes, so the name cannot be
re-derived dashboardside. Hence the engine-record rule in SPEC.md.

## Interface contracts (frozen before build)

1. `describe_active_profile(cfg, preset_name) -> dict` in
   `core_brain/config.py` (pure): `{name, preset, queue_clear_on,
   queue_max_clear_min}`. `name` = `preset` + (`queue-clear-<N>m` if gate on),
   joined with `+`; `default` if empty. `N` trims trailing zeros. No preset
   inference, no `MakerConfig` change. Unknown preset → caller catches
   `ValueError`, field omitted.
2. Heartbeat: optional `profile` dict on `runtime/shadow_run_<run_id>.json`
   (same optional-field pattern as `book_source_mix`). Dashboard accepts it
   only if `isinstance(profile, dict)` and `isinstance(profile["name"], str)`.
3. `GET /api/system/status` gains top-level `active_profile: {name, preset,
   queue_clear_on, queue_max_clear_min, source: "shadow"|"live", running: bool,
   tournament_arm?} | null`. Lookup wrapped in narrow
   `try/except (ValueError, OSError)` → `null`. Shadow wins over live.
4. Live record `runtime/trader_profile.json` (new constant in
   `runtime_paths.py`, no legacy entry): `{pid, started_at, db_path, profile…
   }`. Trusted only if pid alive AND matches the `decide` registry entry AND
   `db_path` resolves to the active store.
5. DOM: `#profile-badge.pill > .pill-label(PROFILE) + .pill-value` inside
   `.top-group-source` after `#db-mode-badge`; renderer `renderProfileBadge`
   exported like `renderDbMode`, full `className` replace per call
   (`state-unknown` / `state-running` / `state-stopped`), all text via
   `renderPillHtml` + `esc()`, sentence-case `title` (source, preset-or-none,
   queue state + minutes, tournament arm).

## Improvement proposal (adopted)

> Show `PROFILE balanced` (label/value spans, no colon), not the issue's
> `PROFILE: balanced` sketch.

Evidence, verbatim — the issue's acceptance criterion: "`PROFILE: balanced`,
`PROFILE: queue-clear-5m`, or `PROFILE: baseline`"; against the live markup at
`dashboard/static/index.html:85`: `<span id="db-mode-badge" …><span
class="pill-label">DB</span> <span class="pill-value">UNKNOWN</span></span>`.
Existing pills never use a colon; a colon would make this the one inconsistent
pill. Classification: simplification / repo-pattern fit → adopt-by-default,
folded into T2 below.

## Task graph (risk-first; each slice shippable)

- **T1** — shadow profile → status field. No deps. Riskiest (startup +
  endpoint contract) → runs first.
- **T2** — badge markup + renderer. Depends on: T1 (needs `active_profile`).
- **T3** — live writer + fallback. Depends on: T1. Independent of T2.

Sizes: T1 M, T2 S, T3 M. Checkpoints: after T1 (field observable in JSON),
after T2 (badge visible), after T3 (full matrix green).

### T1 — Shadow engine records its profile; status endpoint returns it [Backend/Logic] (M) [x]

Target files: `core_brain/config.py`, `core_brain/shadow_run.py`,
`dashboard/server.py`, `tests/test_active_profile.py` (new),
`tests/test_shadow_run_stopwatch.py`, `tests/test_dashboard_server.py`.
Helper skill: `api-and-interface-design` (field contract). Verification:
focused pytest (agent-run).

- Add `describe_active_profile(cfg, preset_name)` per contract §1.
- `shadow_run.py`: compute once near the `shadow_cfg()`/`load()` block; resolve
  preset only when the run loaded its own config (caller-supplied `cfg` →
  `preset=None`); read queue flags from the config in use; thread through
  `_heartbeat` into `write_shadow_heartbeat(..., profile=...)`; construction
  failure omits the field; write failure stays best-effort (`None`, debug log).
- `server.py` reader: copy heartbeat `profile` into the run dict per contract
  §2 (follow the `book_source_mix` pattern directly above it).
- `get_system_status()`: top-level `active_profile` per contract §3 with
  `source: "shadow"`, run `running`, `tournament.arm` when present; else
  `null`. Never call `load_cfg`/`resolve_tournament_preset_name()` here.
- Tests: six helper cases (balanced/off, baseline/off, none+5m, balanced+5m,
  none+2.5m fractional, none/off→`default`-not-`baseline`); heartbeat cases
  (env preset+gate → `balanced+queue-clear-5m`; supplied cfg → `preset None`);
  server cases (no heartbeat→null; matching heartbeat; old heartbeat; malformed
  list/numeric-name; other-store heartbeat; dashboard-env preset set and
  dashboard-env typo → still null + 200). Keep existing status + db-identity
  assertions untouched (locate current anchors; cited line numbers unverified).

### T2 — PROFILE pill in the top nav [Design/UI] (S) [x]

Target files: `dashboard/static/index.html`, `dashboard/static/app.js`,
`tests/test_ui_top_nav_consistency.py`, `tests/test_ui_top_nav_groups.py`,
`tests/js/live_state_harness.cjs` (+ `@requires_node` wrapper).
Depends on: T1. Helper skills: `frontend-ui-engineering`,
`frontend-design`. Verification: focused pytest + node harness (agent-run);
`test_dashboard_narrow_viewport.py` guards width (touch `styles.css` only on
failure).

- Markup: pill directly after `#db-mode-badge` in `.top-group-source`,
  `id="profile-badge"`, `class="pill state-unknown mono"`, label `PROFILE`,
  value `--`. No fourth `.top-group`; group order, ARIA, scroll area,
  dropdown placement unchanged.
- Renderer `renderProfileBadge(status)` per contract §5; called from
  `renderDbMode` next to `renderCodeVersion`; early return when absent;
  `unknown` / `state-running` / `state-stopped`-with-name-retained;
  sentence-case tooltip; same export pattern as `renderDbMode`.
- Tests: `profile-badge` in the named-pill list + Data-source group asserts +
  header grammar id list (keep the three-group check green); harness
  `profile` scenario (missing→unknown; running shadow name; ended shadow
  retained; running live; `<b>x</b>` escaping; running→missing class reset).

### T3 — Live trader records its profile; endpoint falls back to it [Backend/Logic] (M) [x]

Target files: `core_brain/runtime_paths.py`, `core_brain/trader_loop.py`,
`dashboard/server.py`, `tests/test_active_profile.py`,
`tests/test_dashboard_server.py`. Depends on: T1. Verification: focused
pytest (agent-run).

- Constant for `trader_profile.json` beside the other runtime names (no legacy
  entry). `write_trader_profile(cfg, db_path)` in `trader_loop.py`, called
  from `main()` after `cfg = load()` + resolved `db_path`; writes `pid`,
  `started_at`, resolved `db_path`, helper fields via `runtime_file(...)`
  temp-then-replace; `OSError`/`ValueError` → warning log, trader continues.
- Reader via `resolve_runtime_file(...)` per contract §4; fallback in
  `get_system_status()` only when no shadow profile, with `source: "live"`,
  `running: true`; shadow priority preserved; existing `try/except` guard kept.
- Tests: writer holds `pid == os.getpid()` + db + name; unwritable runtime dir
  → no raise; server matrix (valid live; dead pid; pid absent from registry;
  other-store db; shadow+live → shadow wins). Runtime files only under
  `tmp_path`.

## Files NOT to modify

`scripts/shadow_tournament.py`, `scripts/resume_tournament_arms.py`,
`scripts/tournament_cockpit_server.py`, `scripts/tournament_dashboard_hub.py`,
`configs/tournaments/*.json`, `core_brain/risk.py`, `core_brain/env_file.py`,
`get_parameters()` + Strategy tab, `MakerConfig` fields,
`dashboard/static/styles.css` (conditional), `.gitignore`.

## Verification (agent-run while building; operator block hands-on only)

- `python -m pytest -q tests/test_dashboard_server.py` (ticket acceptance)
- `python -m pytest -q tests/test_active_profile.py tests/test_shadow_run_stopwatch.py tests/test_per_run_shadow_heartbeat.py`
- `python -m pytest -q tests/test_ui_top_nav_consistency.py tests/test_ui_top_nav_groups.py tests/test_live_state_language.py tests/test_dashboard_narrow_viewport.py`
- Full suite stays the CI merge gate, not a local loop.
