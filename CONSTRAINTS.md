# Constraints: Issue #230 — Stamp resolutions without re-recording

## Quality & Tests
- Zero regressions on existing test suite: full `python -m pytest -q` must remain green.
- Every new behavior (flag registration, early-return branch, stamped+pending output, no-tick guarantee) must have automated tests.
- Anti-Cheat: Strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing linters.
- Tests must use isolated temporary SQLite DB fixtures (`tmp_path`) and fake network sessions; never touch `data/price_tape.db` or `data/orders.db`, never hit the real venue.

## Venue Safety & Risk
- This change is read-plus-stamp only: it must NEVER record ticks (`poll_once`/`backfill` forbidden in the new branch), sign, quote, or spend.
- `refresh_resolutions()` logic must NOT be modified: only clean binary outcomes (`0.0`/`1.0`) are stamped; ambiguous or open markets stay unresolved.
- No new external dependencies.

## Architectural Integrity
- Reuse `refresh_resolutions()`, `TapeStore.tracked_tokens()`, `_new_session()` as-is; flat argparse flag style, no subcommands.
- Branch placement: next to `--status`/`--analyse` branches, after `TapeStore(args.db)`, before session/polling section.
- Idempotence: re-running `--resolve-only` stamps nothing new and reports the same pending count.
