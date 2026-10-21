"""The Tournament Cockpit's #487 arm table and its plan-record resolver.

Two things an operator reads on `http://127.0.0.1:8800/487` depend on these:
the ten arms with the hypothesis each one tests, and the db/run_id/port the run
actually wrote. The resolver must also keep serving #492 and #608 from their own
record forms -- that path is a frozen contract, so the last test pins it.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import tournament_cockpit_server as cockpit  # noqa: E402

I487_ARMS_SPEC = ROOT / "configs" / "tournaments" / "i487_current_setup_10arm_arms.json"


def _spec_arm_names() -> list[str]:
    return [a["name"] for a in json.loads(I487_ARMS_SPEC.read_text(encoding="utf-8"))]


def _record(tmp_path: Path, filename: str, arms: list[dict]) -> Path:
    """Drop a plan record into the temporary tournaments directory."""
    tourn_dir = tmp_path / "runtime" / "tournaments"
    tourn_dir.mkdir(parents=True, exist_ok=True)
    path = tourn_dir / filename
    path.write_text(json.dumps({"arms": arms}), encoding="utf-8")
    return path


def test_i487_arm_table_names_the_ten_spec_arms_with_their_hypotheses():
    arms = cockpit.ARMS_CONFIG_487
    assert [a["name"] for a in arms] == _spec_arm_names()
    assert len(arms) == 10
    assert [a["port"] for a in arms] == list(range(8801, 8811))
    for arm in arms:
        assert arm["hypothesized_tweak"], arm["name"]
        assert arm["tweak_rationale"], arm["name"]
        # No forensic run exists, so a catalyst score here would be invented.
        assert arm["worst_catalyst"] == "", arm["name"]
        assert arm["worst_catalyst_desc"] == "", arm["name"]


def test_i487_each_arm_tweak_names_the_one_knob_the_spec_file_sets():
    """The cockpit prose and the executable env live in two files.

    Names alone are not enough: a value edited in one file and not the other
    would misreport what the arm actually ran, which is the whole promise the
    table makes to the operator.
    """
    spec = {
        a["name"]: a.get("env", {})
        for a in json.loads(I487_ARMS_SPEC.read_text(encoding="utf-8"))
    }
    arms = {a["name"]: a for a in cockpit.ARMS_CONFIG_487}
    assert spec["control"] == {}, "the control arm's entire point is an empty env"
    for name, env in spec.items():
        if not env:
            continue
        assert len(env) == 1, f"{name} moves {len(env)} settings, not one"
        (key, value), = env.items()
        assert key.startswith("BOT_"), (name, key)
        assert arms[name]["hypothesized_tweak"] == f"{key.removeprefix('BOT_').lower()} = {value}", name


def test_i487_resolves_the_named_plan_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "487_current_setup_10arm.json", [
        {
            "name": "control",
            "db_path": "data/real_control.db",
            "run_id": "shadow-real-control",
            "dash_port": 9901,
        },
        {
            "name": "price-risk-030",
            "db_path": "data/real_price_risk.db",
            "run_id": "shadow-real-price-risk",
            "dash_port": 9908,
        },
    ])
    arms = {a["name"]: a for a in cockpit.resolve_tournament_arms("487")}
    assert list(arms) == _spec_arm_names()
    # db/run_id/port come from the record for the arms it names
    assert arms["control"]["db"] == "data/real_control.db"
    assert arms["control"]["run_id"] == "shadow-real-control"
    assert arms["control"]["port"] == 9901
    assert arms["price-risk-030"]["db"] == "data/real_price_risk.db"
    assert arms["price-risk-030"]["port"] == 9908
    # an arm the record omits keeps its static placeholder AND its prose
    assert arms["widen-on"]["db"] == (
        "data/487_tournament_07_widen-on_current_setup_10arm.db")
    assert arms["widen-on"]["hypothesized_tweak"] == "quote_gate_widen_on = 1"


def test_i487_prefers_the_named_record_over_a_leftover_timestamped_one(tmp_path, monkeypatch):
    """Both record shapes can sit in the folder; the named one is this run.

    A timestamped `487_<stamp>.json` could only be the superseded catalyst round,
    so the resolver must read db/run_id/port from the named record. That choice is
    a sort, so it gets a test rather than a comment.
    """
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "487_20261018-140000.json", [{
        "name": "control",
        "db_path": "data/stale_control.db",
        "run_id": "shadow-stale-control",
        "dash_port": 9999,
    }])
    _record(tmp_path, "487_current_setup_10arm.json", [{
        "name": "control",
        "db_path": "data/named_control.db",
        "run_id": "shadow-named-control",
        "dash_port": 8801,
    }])
    arms = {a["name"]: a for a in cockpit.resolve_tournament_arms("487")}
    assert arms["control"]["db"] == "data/named_control.db"
    assert arms["control"]["run_id"] == "shadow-named-control"
    assert arms["control"]["port"] == 8801


def test_i487_without_a_record_falls_back_to_the_static_table(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    assert cockpit.resolve_tournament_arms("487") == cockpit.ARMS_CONFIG_487


def test_492_and_608_still_resolve_from_their_run_numbered_records(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "i492_tournament-2.json", [{
        "name": "control-1",
        "db_path": "data/real492.db",
        "run_id": "shadow-real-492",
        "dash_port": 8901,
    }])
    _record(tmp_path, "i608_tournament-1.json", [{
        # The real #608 record names its arms WITHOUT the round suffix the
        # cockpit's static table carries; the resolver strips it to match.
        "name": "the-accountant",
        "db_path": "data/real608.db",
        "run_id": "shadow-real-608",
        "dash_port": 8902,
    }])
    # A #487 named record sitting beside them must not bleed into either key.
    _record(tmp_path, "487_current_setup_10arm.json", [])

    c492 = {a["name"]: a for a in cockpit.resolve_tournament_arms("492")}
    assert len(c492) == len(cockpit.ARMS_CONFIG_492)
    assert c492["control-1"]["db"] == "data/real492.db"
    assert c492["control-1"]["run_id"] == "shadow-real-492"
    assert c492["control-1"]["port"] == 8901

    c608 = {a["name"]: a for a in cockpit.resolve_tournament_arms("608")}
    assert len(c608) == len(cockpit.ARMS_CONFIG_608)
    assert c608["the-accountant-1"]["db"] == "data/real608.db"
    assert c608["the-accountant-1"]["run_id"] == "shadow-real-608"
    assert c608["the-accountant-1"]["port"] == 8902


# --- The purged past-run pages ------------------------------------------------
#
# Issue #698 ordered the broken past-run pages purged. PR #703 closed the issue
# without touching a single route, so `/484`, `/492` and `/608` kept serving
# stale snapshots for two more days. Nothing on the page said which tournament
# it was showing, and the operator had to notice by eye. These tests are the
# guard: the cockpit serves one page, and the retired paths 404 on purpose.
#
# Their arm records stay (the resolver tests above), because "no page" is not
# "no data": #492/#608 must remain launchable and comparable.


class _CockpitHandlerStub(cockpit.CockpitHandler):
    """The real handler with no socket under it, so routing is testable."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.statuses: list[int] = []
        self.wfile = io.BytesIO()

    def send_response(self, code: int, message: str | None = None) -> None:
        self.statuses.append(code)

    def send_header(self, keyword: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass


def _stub_stats(monkeypatch) -> None:
    """Keep the handler off the arm databases; routing is what is under test."""
    monkeypatch.setattr(
        cockpit, "get_cached_stats",
        lambda key=cockpit.CURRENT_SETUP_KEY: {"arms": [], "tourney_key": key},
    )


def test_cockpit_serves_only_the_current_setup_page(monkeypatch):
    _stub_stats(monkeypatch)

    for path in ("/", "/index.html", "/487", "/487.html", "/cockpit_487", "/487?refresh=1"):
        handler = _CockpitHandlerStub(path)
        handler.do_GET()
        assert handler.statuses == [200], path
        assert b"#487 Current Setup" in handler.wfile.getvalue(), path

    for path in ("/484", "/484.html", "/492", "/492.html", "/608", "/608.html", "/nope"):
        handler = _CockpitHandlerStub(path)
        handler.do_GET()
        assert handler.statuses == [404], f"{path} must not render a page"


def test_stats_api_will_not_serve_a_purged_tournament(monkeypatch):
    """`?tourney=<retired or nonsense>` serves the page's own tournament.

    An empty or repeated key must not reach the arm analysis with a key nothing
    serves: the JSON echoes `tourney_key`, and the page's Refresh reads it back.
    """
    _stub_stats(monkeypatch)

    for query in ("tourney=608", "tourney=492", "tourney=484", "tourney=", "tourney=../487", ""):
        handler = _CockpitHandlerStub(f"/api/stats?{query}")
        handler.do_GET()
        assert handler.statuses == [200], query
        body = json.loads(handler.wfile.getvalue())
        assert body["tourney_key"] == cockpit.CURRENT_SETUP_KEY, query


def test_cockpit_html_links_to_no_purged_page():
    html = cockpit.generate_cockpit_html({"arms": [], "tourney_key": "487"})
    for dead in ('href="/484"', 'href="/492"', 'href="/608"'):
        assert dead not in html, f"purged page still linked: {dead}"
    assert 'href="/487"' in html


# --- A record that cannot be read is not the same as no record ---------------
#
# `resolve_tournament_arms` silently fell back to the static presets on any
# exception, and the page then said "not started". A truncated or hand-edited
# record -- exactly what a crash mid-write leaves behind -- therefore looked
# like a clean slate, and the operator drew conclusions from default numbers.
# The status function makes that state visible instead of guessing.

BROKEN_RECORD = '{"arms": [{"name": "control",'


def _broken_record(tmp_path: Path) -> Path:
    tourn_dir = tmp_path / "runtime" / "tournaments"
    tourn_dir.mkdir(parents=True, exist_ok=True)
    path = tourn_dir / "487_current_setup_10arm.json"
    path.write_text(BROKEN_RECORD, encoding="utf-8")
    return path


def test_broken_record_reports_its_path_and_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    path = _broken_record(tmp_path)

    arms, record_error = cockpit.resolve_tournament_arms_with_status("487")

    # The table keeps working (static presets), but never silently.
    assert arms == cockpit.ARMS_CONFIG_487
    assert record_error is not None
    assert str(path) in record_error, "the operator must know WHICH file to fix"
    assert "line" in record_error.lower(), f"no JSON parser reason: {record_error}"


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("[]", "top level is not a JSON object"),
        ('{"arm": []}', "'arms' is missing"),
        ('{"arms": {}}', "'arms' is not a list"),
        ('{"arms": ["control"]}', "arm 0 is not a JSON object"),
        ('{"arms": [{"db_path": "x"}]}', "arm 0 has no string 'name'"),
    ],
)
def test_broken_record_shape_errors_name_the_arm_at_fault(
    tmp_path, monkeypatch, payload, expected
):
    """A record that parses but is the wrong shape is just as unreadable."""
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    tourn_dir = tmp_path / "runtime" / "tournaments"
    tourn_dir.mkdir(parents=True, exist_ok=True)
    (tourn_dir / "487_current_setup_10arm.json").write_text(payload, encoding="utf-8")

    arms, record_error = cockpit.resolve_tournament_arms_with_status("487")

    assert arms == cockpit.ARMS_CONFIG_487
    assert record_error is not None
    assert expected in record_error


def test_no_record_is_not_an_error(tmp_path, monkeypatch):
    """A run that has not started yet is a normal state, not a failure."""
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)

    arms, record_error = cockpit.resolve_tournament_arms_with_status("487")

    assert arms == cockpit.ARMS_CONFIG_487
    assert record_error is None


def test_a_readable_record_reports_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    _record(tmp_path, "487_current_setup_10arm.json", [{
        "name": "control",
        "db_path": "data/real_control.db",
        "run_id": "shadow-real-control",
        "dash_port": 9901,
    }])

    arms, record_error = cockpit.resolve_tournament_arms_with_status("487")

    assert record_error is None
    assert {a["name"]: a for a in arms}["control"]["db"] == "data/real_control.db"


def test_plain_resolver_still_returns_only_the_arms(tmp_path, monkeypatch):
    """The wrapper keeps its old signature: other callers must not break."""
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    _broken_record(tmp_path)

    arms = cockpit.resolve_tournament_arms("487")

    assert isinstance(arms, list)
    assert arms == cockpit.ARMS_CONFIG_487


def test_a_broken_record_warns_once_not_once_per_poll(tmp_path, monkeypatch, caplog):
    """`get_cached_stats` re-collects every 3s; 1200 log lines an hour help nobody."""
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    _broken_record(tmp_path)

    with caplog.at_level(logging.WARNING, logger=cockpit.__name__):
        for _ in range(3):
            cockpit.resolve_tournament_arms_with_status("487")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "Could not read tournament record" in warnings[0].getMessage()


def test_a_record_that_breaks_again_warns_again(tmp_path, monkeypatch, caplog):
    """A fixed record must clear the memory, or the next break goes unseen."""
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    monkeypatch.setattr(cockpit, "_WARNED_RECORD", None)
    path = _broken_record(tmp_path)

    with caplog.at_level(logging.WARNING, logger=cockpit.__name__):
        cockpit.resolve_tournament_arms_with_status("487")
        path.write_text(json.dumps({"arms": []}), encoding="utf-8")
        assert cockpit.resolve_tournament_arms_with_status("487")[1] is None
        path.write_text(BROKEN_RECORD, encoding="utf-8")
        assert cockpit.resolve_tournament_arms_with_status("487")[1] is not None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, [r.getMessage() for r in warnings]


def test_cockpit_page_shows_the_broken_record_warning():
    """The fallback table must say, on screen, that it is the fallback."""
    broken = cockpit.generate_cockpit_html({
        "arms": [], "tourney_key": "487",
        "record_error": "C:/run/487_current_setup_10arm.json: line 1 column 27",
    })
    assert 'id="record-warning"' in broken
    assert 'record-warning" class="badge-red" style="display: block;' in broken
    assert "failed to parse" in broken
    assert "487_current_setup_10arm.json: line 1 column 27" in broken

    healthy = cockpit.generate_cockpit_html(
        {"arms": [], "tourney_key": "487", "record_error": None})
    # Always in the DOM so the 5s poll can reveal it without a reload.
    assert 'id="record-warning"' in healthy
    assert 'style="display: none;' in healthy


def test_tournament_metadata_carries_no_never_rendered_badge():
    """`badge`/`badge_cls` were declared per tournament and rendered nowhere."""
    for key, meta in cockpit.TOURNAMENTS.items():
        assert "badge" not in meta, key
        assert "badge_cls" not in meta, key


def test_nav_pill_is_the_single_current_setup_badge():
    html = cockpit.generate_cockpit_html({"arms": [], "tourney_key": "487"})
    assert html.count("CURRENT SETUP</span>") == 1
    assert html.count('class="tourney-pill') == 1
