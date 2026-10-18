# CONSTRAINTS — #672: Dashboard parameter cards still read legacy names and label legacy env vars

Branch: plan_issue_672 | Issue: #672

## 1. Zero Regressions & Verification Gates
- Targeted suites must pass:
  `python -m pytest -q tests/test_dashboard_server.py tests/test_config_single_source.py`
- All 59 parameter card keys (`key`) and card identifiers (`name`) must remain strictly unchanged to preserve frontend state, table filtering, and bookmarking in `dashboard/static/app.js`.
- Display values (`value`), numeric types and values of `raw_value`, `default` strings, `trigger` strings, and `action` descriptions must remain identical for an unchanged configuration.

## 2. Anti-Cheat & Quality Guardrails
- Strictly forbid skipping, commenting out, or disabling tests.
- Strictly forbid removing or weakening assertions in `tests/test_dashboard_server.py`, `tests/test_config_single_source.py`, or `tests/test_config_divergence_guard.py`.
- No suppression of warnings via blanket `warnings.filterwarnings("ignore")`. Deprecation warnings from `dashboard/server.py` must be genuinely resolved by reading canonical fields.
- Do not modify `dashboard/static/app.js`, `core_brain/config.py`, `scoring/config.py`, or `.env.example`.

## 3. Interface & Data Integrity
- Every card dictionary in `/api/parameters` must have `env_var` typed as `str` (`""` or canonical variable name), never `None`.
- No `HUNTER_*` or `SPREAD_HUNTER_*` names may appear in any `env_var` field.
- Every non-empty `env_var` must be present verbatim in the root `.env.example` file.

## 4. Dependencies & Safety
- Zero new external dependencies.
- Production real-money safety: this refactor touches only read-only dashboard presentation logic (`get_parameters` in `dashboard/server.py`) and tests. No execution or order placement logic is touched.
