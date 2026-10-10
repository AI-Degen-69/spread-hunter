"""Runtime state survives the run/ -> runtime/ rename.

The rename moved state that is NOT in git: it sits on the operator's disk,
written by processes that may still be running when the new code starts. Two
consequences are money, not cosmetics:

  * A process registry the new code cannot find reads as STOPPED. `start_bot`
    only refuses a second stack when the status says RUNNING, so START would
    launch a second `core_brain.trader_loop --live` beside the live one.
  * A markets feed the new code cannot find leaves the Trader with an empty
    universe until the filter regenerates the file.

These tests pin the fallback that closes both. Every one of them fails against
the plain `runtime/`-only lookups the rename shipped with.
"""
from __future__ import annotations

import json
import sys

import pytest

from core_brain.runtime_paths import (
    LEGACY_FILE_NAMES,
    LEGACY_SERVICE_KEYS,
    legacy_runtime_file,
    resolve_runtime_file,
    runtime_file,
    service_entry,
)


@pytest.fixture()
def dirs(tmp_path):
    """A `runtime/` and a pre-rename `run/`, both empty."""
    current = tmp_path / "runtime"
    legacy = tmp_path / "run"
    current.mkdir()
    legacy.mkdir()
    return current, legacy


def _resolve(name, dirs):
    current, legacy = dirs
    return resolve_runtime_file(name, runtime_dir=current, legacy_dir=legacy)


# ── resolve_runtime_file ──

def test_prefers_the_current_path_when_it_exists(dirs):
    current, legacy = dirs
    (current / "markets.json").write_text("[]", encoding="utf-8")
    (legacy / "markets.json").write_text("[]", encoding="utf-8")

    assert _resolve("markets.json", dirs) == current / "markets.json"


def test_falls_back_to_the_pre_rename_path(dirs):
    """The whole point: state written before the rename is still readable."""
    _, legacy = dirs
    (legacy / "markets.json").write_text("[]", encoding="utf-8")

    assert _resolve("markets.json", dirs) == legacy / "markets.json"


def test_returns_the_current_path_when_neither_exists(dirs):
    """A caller that writes through this path must create the NEW file."""
    current, _ = dirs

    assert _resolve("markets.json", dirs) == current / "markets.json"


def test_fallback_disarms_itself_once_the_new_file_appears(dirs):
    """The filter writes runtime/markets.json; the next read must switch."""
    current, legacy = dirs
    (legacy / "markets.json").write_text("[]", encoding="utf-8")
    assert _resolve("markets.json", dirs) == legacy / "markets.json"

    (current / "markets.json").write_text("[]", encoding="utf-8")
    assert _resolve("markets.json", dirs) == current / "markets.json"


@pytest.mark.parametrize("current_name,legacy_name", sorted(LEGACY_FILE_NAMES.items()))
def test_renamed_files_fall_back_to_their_old_name(current_name, legacy_name, dirs):
    _, legacy = dirs
    (legacy / legacy_name).write_text("{}", encoding="utf-8")

    assert _resolve(current_name, dirs) == legacy / legacy_name


def test_legacy_runtime_file_maps_renamed_names():
    assert legacy_runtime_file("processes.json").name == "live_procs.json"
    # A file that was not renamed keeps its name across the move.
    assert legacy_runtime_file("markets.json").name == "markets.json"


def test_runtime_file_never_falls_back(dirs):
    """Writers must not resurrect the old path, or the fallback never disarms."""
    current, legacy = dirs
    (legacy / "processes.json").write_text("{}", encoding="utf-8")
    (legacy / "live_procs.json").write_text("{}", encoding="utf-8")

    assert runtime_file("processes.json", runtime_dir=current) == current / "processes.json"


# ── service_entry ──

def test_service_entry_reads_the_current_key():
    saved = {"decide": {"pid": 4242, "started_at": 1.0}}

    assert service_entry(saved, "decide")["pid"] == 4242


@pytest.mark.parametrize("key,legacy_key", sorted(LEGACY_SERVICE_KEYS.items()))
def test_service_entry_reads_the_pre_rename_key(key, legacy_key):
    saved = {legacy_key: {"pid": 4242, "started_at": 1.0}}

    assert service_entry(saved, key)["pid"] == 4242


def test_service_entry_prefers_the_current_key():
    saved = {"decide": {"pid": 1}, "fleet": {"pid": 2}}

    assert service_entry(saved, "decide")["pid"] == 1


def test_service_entry_returns_an_empty_mapping_for_junk():
    """Callers do `.get("pid")` on the result without checking for None."""
    assert service_entry(None, "decide") == {}
    assert service_entry({}, "decide") == {}
    assert service_entry({"decide": "not-a-mapping"}, "decide") == {}
    assert service_entry({"starting_account_value": 42.0}, "decide") == {}


def test_service_entry_copies_so_callers_cannot_mutate_the_registry():
    saved = {"decide": {"pid": 1}}

    service_entry(saved, "decide")["pid"] = 999

    assert saved["decide"]["pid"] == 1


# ── the readers that matter ──

def test_market_feed_reads_the_pre_rename_universe(tmp_path, monkeypatch):
    """Without this the Trader quotes nothing for a whole filter cycle."""
    from core_brain import market_feed

    current = tmp_path / "runtime"
    legacy = tmp_path / "run"
    current.mkdir()
    legacy.mkdir()
    row = {"cid": "0xabc", "min_size": 5.0, "tick": 0.01, "max_spread": 4.5,
           "days_to_resolve": 1.0, "source": "spread", "daily": 0.0}
    (legacy / "markets.json").write_text(json.dumps([row]), encoding="utf-8")

    monkeypatch.setattr(market_feed, "DEFAULT_MARKETS_PATH", current / "markets.json")
    monkeypatch.setattr(market_feed, "PROJECT_ROOT", tmp_path)

    markets = market_feed.load_graduated_markets(max_age_sec=None)

    assert [m.cid for m in markets] == ["0xabc"]


def test_dashboard_sees_a_stack_started_before_the_rename(tmp_path, monkeypatch):
    """The money case: STOPPED here means START opens a second live Trader."""
    import os

    from dashboard import server

    legacy = tmp_path / "run"
    (tmp_path / "runtime").mkdir()
    legacy.mkdir()
    # Pre-rename registry: old file name, old process keys, this live PID.
    (legacy / "live_procs.json").write_text(
        json.dumps({"screener": {"pid": os.getpid()},
                    "engine": {"pid": os.getpid()},
                    "fleet": {"pid": os.getpid()}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    status = server.get_system_status()

    assert status["bot_state"] == "RUNNING"
    assert status["services"]["decide"]["running"] is True
    assert status["services"]["decide"]["pid"] == os.getpid()


# ── fail closed on an unreadable process registry ──

def _write_registry(tmp_path, text: str):
    (tmp_path / "runtime").mkdir(exist_ok=True)
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "runtime" / "processes.json").write_text(text, encoding="utf-8")


def test_unreadable_registry_reports_unknown_not_stopped(tmp_path, monkeypatch):
    """A half-written registry must never read as 'nothing is running'."""
    from dashboard import server

    _write_registry(tmp_path, '{"decide": {"pid": 4242')  # truncated mid-write
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    status = server.get_system_status()

    assert status["bot_state"] == "UNKNOWN"
    assert status["registry_unreadable"] is True


def test_registry_holding_a_list_reports_unknown(tmp_path, monkeypatch):
    """`saved_procs.get(...)` would explode on a list; treat it as unreadable."""
    from dashboard import server

    _write_registry(tmp_path, "[]")
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    assert server.get_system_status()["bot_state"] == "UNKNOWN"


def test_readable_empty_registry_is_stopped(tmp_path, monkeypatch):
    """An empty object is a real answer: nothing recorded, nothing running."""
    from dashboard import server

    _write_registry(tmp_path, "{}")
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    status = server.get_system_status()

    assert status["bot_state"] == "STOPPED"
    assert status["registry_unreadable"] is False


def test_start_refuses_while_the_registry_is_unreadable(tmp_path, monkeypatch):
    """The money case: UNKNOWN must not be treated as permission to start."""
    from dashboard import server

    # start_bot creates .bot_start.lock under LIVE_ROOT before it checks the
    # status, so this must point at tmp_path -- otherwise the test writes into
    # the real repository and its stale-lock branch can remove a lock that a
    # live start owns.
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        server, "get_system_status",
        lambda: {"bot_state": "UNKNOWN", "registry_path": "runtime/processes.json"},
    )

    result = server.start_bot()

    assert result["ok"] is False
    assert "refusing to start" in result["message"].lower()
    assert "runtime/processes.json" in result["message"]


def test_reset_refuses_while_the_registry_is_unreadable(tmp_path, monkeypatch):
    """Unlinking the registry DB under a live writer loses every later write."""
    from dashboard import server

    monkeypatch.setattr(server, "get_system_status", lambda: {"bot_state": "UNKNOWN"})

    result = server.reset_database(custom_path=tmp_path / "orders.db")

    assert result["ok"] is False
    assert "state is unknown" in result["message"]


# ── STOP and reset must not report a stack down that they never reached ──

def test_stop_refuses_on_an_unreadable_registry(tmp_path, monkeypatch):
    """Reporting 'stopped' here lets the reset archive the DB under a live writer."""
    from dashboard import server

    _write_registry(tmp_path, '{"decide": {"pid": 4242')
    procs_file = tmp_path / "runtime" / "processes.json"
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    result = server.stop_bot()

    assert result["ok"] is False
    assert "cannot read the process file" in result["message"].lower()
    # The record survives: it is the only way back to the running PIDs.
    assert procs_file.exists()


def test_stop_clears_a_readable_registry(tmp_path, monkeypatch):
    """The normal path is unchanged: readable registry, record removed.

    Issue #457: the file is rewritten rather than deleted -- a survivor entry
    and `starting_account_value` must survive the stop so a retry can reach
    them. A stopped stack leaves no service entries behind.
    """
    from dashboard import server

    _write_registry(tmp_path, '{"decide": {"pid": 999999999, "started_at": 1.0},'
                              ' "starting_account_value": 42.0}')
    procs_file = tmp_path / "runtime" / "processes.json"
    monkeypatch.setattr(server, "LIVE_ROOT", tmp_path)

    result = server.stop_bot()

    assert result["ok"] is True
    kept = json.loads(procs_file.read_text(encoding="utf-8"))
    assert "decide" not in kept
    assert kept["starting_account_value"] == 42.0


def test_idempotency_guard_reads_the_pre_rename_order_log(tmp_path, monkeypatch):
    """A merge recorded before the rename must still block a second one.

    Missing that row is a duplicate on-chain settlement for a condition that
    is already in flight.
    """
    from core_brain import order_manager

    (tmp_path / "runtime").mkdir()
    legacy_dir = tmp_path / "run"
    legacy_dir.mkdir()
    (legacy_dir / "live_orders.json").write_text(
        json.dumps([{"id": "merge-1", "condition_id": "0xABC", "status": "submitted"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(order_manager, "ROOT", tmp_path)
    monkeypatch.setattr(order_manager, "RUN", tmp_path / "runtime")

    with pytest.raises(SystemExit) as excinfo:
        order_manager._check_idempotency_guard("0xabc")

    assert "merge-1" in str(excinfo.value)


def test_idempotency_guard_still_allows_an_unseen_condition(tmp_path, monkeypatch):
    from core_brain import order_manager

    (tmp_path / "runtime").mkdir()
    legacy_dir = tmp_path / "run"
    legacy_dir.mkdir()
    (legacy_dir / "live_orders.json").write_text(
        json.dumps([{"id": "merge-1", "condition_id": "0xABC", "status": "confirmed"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(order_manager, "ROOT", tmp_path)
    monkeypatch.setattr(order_manager, "RUN", tmp_path / "runtime")

    order_manager._check_idempotency_guard("0xabc")  # must not raise


# ── --no-live must mean --no-live, wherever it appears ──

@pytest.mark.parametrize("argv", [
    ["--no-live", "merge", "0xabc", "--amount", "1"],
    ["merge", "0xabc", "--amount", "1", "--no-live"],
])
def test_no_live_wins_from_either_side_of_the_subcommand(argv, monkeypatch):
    """`--no-live merge 0xabc` used to go LIVE.

    Each subparser redefined `--live` with `default=True`, and argparse applies
    the subparser default AFTER the parent has parsed, so a `--no-live` typed
    before the subcommand was silently overwritten back to True. An operator
    asking for a dry run got a real on-chain merge.
    """
    from core_brain import order_manager

    captured = {}
    monkeypatch.setattr(order_manager, "merge", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(sys, "argv", ["order_manager", *argv])

    try:
        order_manager.main()
    except SystemExit:
        pass

    assert captured.get("live") is False


def test_live_is_still_the_default(monkeypatch):
    """The safety rail is unchanged: no flag at all means LIVE."""
    from core_brain import order_manager

    captured = {}
    monkeypatch.setattr(order_manager, "merge", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(sys, "argv",
                        ["order_manager", "merge", "0xabc", "--amount", "1"])

    try:
        order_manager.main()
    except SystemExit:
        pass

    assert captured.get("live") is True
