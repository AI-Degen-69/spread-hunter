# Plan — #754 Cockpit: surface arm-record parse failures, drop dead badge metadata

Branch: `i754/cockpit-record-parse-warning-and-drop-dead-badge`
Files: `scripts/tournament_cockpit_server.py`, `tests/test_tournament_cockpit_arms.py`

## CodeRabbit plan comment: adopted vs rejected

Issue #754 carries one `coderabbitai` comment (446-line `Coding Plan`). Mined once; the
duplicated HTML echo under `<!-- <rawResChunk> -->` was not read. Every point is triaged:

**Adopted**

1. A named `TournamentRecordError` raised by shape validation, with only `OSError` / `ValueError`
   caught around the read. The blanket `except Exception: return default_arms` is deleted, so an
   unforeseen bug surfaces as a real error instead of posing as "not started".
2. A sibling `resolve_tournament_arms_with_status(key)` returning `(arms, error_or_None)`.
   `resolve_tournament_arms` stays a one-line wrapper, so five existing tests and
   `collect_all_stats` keep working unchanged.
3. The error text rides in the `/api/stats` payload as `record_error`, shown as an escaped
   warning line, and re-synced by `renderAll()` — the 5s poll must not erase the banner.
4. Reuse `.badge-red`; add no stylesheet class.
5. `caplog` assertions for the log line, and every new test must fail on the old code.

**Rejected**

1. *Render `TOURNAMENTS["487"]["badge"]` in the nav and keep the field.* The issue title says
   drop; the nav serves exactly one page, so a metadata-driven one-item nav buys the operator
   nothing visible while leaving four entries with inconsistent key sets. Dropping all four
   entries removes the duplicate literal instead of moving it.
2. *Log a warning on every resolve call.* `get_cached_stats` re-collects every 3s and calls the
   resolver each time, so a per-call warning prints roughly 1200 identical lines per hour. One
   warning per broken record instead (module-level `_LAST_WARNED_RECORD`, reset on a successful
   read so a record that breaks again warns again).
3. *Update `docs/issues/487-noticed-but-not-touching.md` here.* Two-file scope; those rows
   already say "promoted to #754", which stays true. Artifact closeout is the last station.

## Locked interface

```python
class TournamentRecordError(Exception):
    """An arm record exists but cannot be trusted to describe this run."""

_WARNED_RECORD: Optional[str] = None        # last record message already logged

def _read_arm_record(path: Path) -> list[dict[str, Any]]:
    """Read + shape-check an arm record. Raises TournamentRecordError."""

def resolve_tournament_arms_with_status(
    tourney_key: str = CURRENT_SETUP_KEY,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return (arms, record_error). record_error is None when nothing failed."""

def resolve_tournament_arms(tourney_key: str = CURRENT_SETUP_KEY) -> list[dict[str, Any]]:
    """Unchanged signature/return type: the arms only."""
    return resolve_tournament_arms_with_status(tourney_key)[0]
```

Status mapping — this is the whole contract:

| Record state | arms returned | `record_error` |
| --- | --- | --- |
| key not live (`484`) | static table | `None` |
| no record file | static table | `None` |
| valid record | merged table | `None` |
| broken record | static table | `"<path>: <reason>"` |

Every error string starts with the record path, so the page and the log both name the file.

Shape validation in `_read_arm_record`: top level must be a JSON object; `"arms"` must be
present and a list; each entry must be an object with a non-empty string `name`. Anything else
raises `TournamentRecordError` with a plain reason ("'arms' is not a list", "arm 2 is not a JSON
object", …). `json.JSONDecodeError` and `UnicodeDecodeError` are `ValueError` subclasses, so the
single `except (OSError, ValueError)` around the read covers truncation, bad bytes and bad JSON.

## Tasks

### T1 — Named error, shape check, status function (server)

- `import html` / `import logging` at the top; `log = logging.getLogger(__name__)`.
- Add `TournamentRecordError` + `_read_arm_record` next to `_strip_round_suffix`.
- Rename today's `resolve_tournament_arms` body to `resolve_tournament_arms_with_status`:
  - the plan-file picker (lines 727-750) moves across unchanged — precedence is a frozen
    contract: the named `487_current_setup_10arm.json` still wins over a leftover timestamped
    record, and #492/#608 still pick the highest `i<key>_tournament-<run>.json`;
  - `if latest_plan is None: return default_arms, None`;
  - `try: record_arms = _read_arm_record(latest_plan)` / `except TournamentRecordError as exc:`
    → warn once (`"Could not read tournament record %s"`), `return default_arms, str(exc)`;
  - merging keeps the per-field fallback (`db_path` / `run_id` / `dash_port`) and the static base
    for arms the record omits;
  - `return merged, None`.
- `resolve_tournament_arms` becomes the one-line wrapper.
- `collect_all_stats`: `arms_config, record_error = resolve_tournament_arms_with_status(...)` and
  `"record_error": record_error` in the returned dict.

### T2 — Warning on the page and in the poll (server + template)

- At the top of `generate_cockpit_html`, compute `record_error` via `initial_data.get(...)` and
  build the warning block. **Always** emit the container so the 5s poll can show/hide it:

  ```html
  <div id="record-warning" class="badge badge-red" style="display:none; ...">…</div>
  ```

  When `record_error` is set: `display:block` and text
  `⚠️ Tournament record failed to parse: <html-escaped reason>. The table shows default arms, not live data.`
- Place `{record_warning_html}` directly after `</header>` (line 1462), above the metric cards.
- `renderAll()`: read `#record-warning`, set `innerText` from `state.record_error` and toggle
  `display`. Absent field on an older payload ⇒ hidden.
- No new CSS class: `.badge-red` (line 1401) + inline layout only.

### T3 — Delete the dead badge metadata

- Remove `badge` / `badge_cls` from all four `TOURNAMENTS` entries (492: 645-646, 608: 656-657,
  484: 667-668, 487: 678-679). Keep every other key.
- Nav pill literal (line 1437) is untouched — it is now the only source of that string.
- `analyze_arm_database`'s per-arm `badge_cls` (line 1026) is a different feature: do not touch.

### T4 — Tests (`tests/test_tournament_cockpit_arms.py`)

Reuse `_record()` + `monkeypatch.setattr(cockpit, "ROOT", tmp_path)`.

1. `test_broken_record_reports_the_path_and_the_reason` — truncate the file (`'{"arms": ['`):
   default arms + error containing `487_current_setup_10arm.json`.
2. `test_record_shape_errors_are_named` — parametrised: top-level list, `arms` missing,
   `arms` as dict, arm as int, arm without `name`; each returns default arms + a reason.
3. `test_missing_record_is_not_an_error` — `(ARMS_CONFIG_487, None)`.
4. `test_valid_record_has_no_error` — merged db/run_id/port applied, error `None`.
5. `test_resolver_wrapper_still_returns_arms_only` — broken record → `ARMS_CONFIG_487`.
6. `test_broken_record_logs_one_warning` — `caplog`, resolver called twice → exactly one
   WARNING containing the file name; valid and missing records log none.
   Reset `cockpit._WARNED_RECORD` with `monkeypatch`.
7. `test_cockpit_page_shows_the_record_warning` — `generate_cockpit_html({"arms": [], "tourney_key":
   "487", "record_error": "<path>: boom"})` contains "failed to parse" + the path; the same call
   without `record_error` does not, and the container is `display:none`.
8. `test_no_tournament_entry_carries_badge_metadata` — no `badge` / `badge_cls` key on any of the
   four entries.
9. `test_nav_renders_one_current_setup_badge` — the fixed nav pill string appears once and the
   removed values (`R1 FRESH`, `R1 CHARACTERS`, `R1 ARCHIVE`) appear nowhere.

## Verification (agent-run)

```
python -m pytest -q tests/test_tournament_cockpit_arms.py
python -m pytest -q tests/test_tournament_cockpit_funnel.py tests/test_tournament_dashboard_hub.py
```

Full suite stays the CI merge gate.

## Risks

- **Log spam** — handled by `_WARNED_RECORD`; the test asserts one record across two calls.
- **Contract drift on the picker** — the picker code is moved, not rewritten; the frozen
  #492/#608 test plus the named-vs-timestamped test guard it.
- **Banner vanishing on refresh** — avoided by always emitting the container and syncing it in
  `renderAll()`.

