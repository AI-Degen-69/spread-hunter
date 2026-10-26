"""Guard for the canonical close-method taxonomy (#558, #520).

The taxonomy lives in ``core_brain/close_methods.py`` and nowhere else. This
file proves it stays that way:

* the scanner fails when a method-string literal list reappears outside the
  canonical module. Every consumer named in #520 now imports the taxonomy
  (#558 moved the leaf, #559 moved the readers in scope for it), so the scan
  is green and a hit is a regression, not a work order;
* the pins keep the module honest: every declared name is either written by
  a named code path or marked legacy, the module is a standard-library leaf,
  and a settlement is never a rescue.

The scanner reads source as text and as a syntax tree; it never imports the
scanned modules.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from core_brain import close_methods as cm

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "core_brain" / "close_methods.py"
SCAN_DIRS = ("core_brain", "statistical_validation_run", "scripts")
STATIC = ROOT / "dashboard" / "static"

# scripts/hooks/ is CI tooling (the git-workflow guard), never a taxonomy
# consumer: its `rest[:2] == ["pr", "merge"]` check is git vocabulary, not
# close methods (operator decision on #558).
HOOKS = ROOT / "scripts" / "hooks"

# dashboard/static/app.js EXIT_METHODS is the one JS consumer of the taxonomy.
# JS cannot import the Python leaf, so it keeps a pinned copy of
# DASHBOARD_FORCED_EXIT_METHODS; it is excluded from the scan and kept honest
# by test_app_js_exit_methods_is_pinned_to_the_leaf instead.
APP_JS_EXIT_METHODS = "const EXIT_METHODS"

# `sell` is also an order side (`("buy", "sell")`), so it never triggers the
# scan on its own; the other eleven names do.
TRIGGER_NAMES = cm.ALL_METHOD_NAMES - {cm.SELL}


# --- the scanner --------------------------------------------------------------

def _scan_files() -> list[Path]:
    """Every file the scan covers: the three Python roots plus the top-level
    dashboard scripts, minus caches, the canonical module, and scripts/hooks/."""
    files: list[Path] = []
    for name in SCAN_DIRS:
        base = ROOT / name
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py")
                         if "__pycache__" not in p.parts and p != CANONICAL
                         and HOOKS not in p.parents)
    files.extend(sorted(STATIC.glob("*.js")))
    return files


def _python_list_hits(tree: ast.AST) -> list[int]:
    """Flag tuple/list/set literals of quoted strings holding a method name."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            continue
        elts = node.elts
        if not all(isinstance(e, ast.Constant)
                   and (e.value is None or isinstance(e.value, str)) for e in elts):
            continue  # a mixed row (demo data) is not a method list
        if any(e.value in TRIGGER_NAMES for e in elts if isinstance(e.value, str)):
            hits.append(node.lineno)
    return hits


def _query_hits(tree: ast.AST) -> list[int]:
    """Flag SQL text that selects on a method name: ``... IN ('name', ...)``."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        if "IN (" in text and any(f"'{n}'" in text for n in TRIGGER_NAMES):
            hits.append(node.lineno)
    return hits


_JS_STRING_ARRAY = re.compile(
    r"\[\s*(?:(?:\"[^\"]*\"|'[^']*')\s*,\s*)*(?:\"[^\"]*\"|'[^']*')\s*\]")
_JS_QUOTED = re.compile(r"\"([^\"]*)\"|'([^']*)'")


def _js_hits(lines: list[str]) -> list[int]:
    """Flag JS arrays of quoted strings holding a method name (``new Set([...])``).

    Object maps such as METHOD_BADGES and comparison chains are display
    logic, not lists, and stay unflagged.
    """
    hits: list[int] = []
    for lineno, line in enumerate(lines, 1):
        if APP_JS_EXIT_METHODS in line:
            continue  # pinned dashboard set; see the pin test below
        for match in _JS_STRING_ARRAY.finditer(line):
            values = [a or b for a, b in _JS_QUOTED.findall(match.group(0))]
            if any(v in TRIGGER_NAMES for v in values):
                hits.append(lineno)
                break
    return hits


def _scan() -> dict[str, list[tuple[int, str]]]:
    """Every site still holding its own method-string list, by file."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _scan_files():
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if path.suffix == ".py":
            tree = ast.parse("\n".join(lines), filename=str(path))
            hits = _python_list_hits(tree) + _query_hits(tree)
        else:
            hits = _js_hits(lines)
        if hits:
            rel = path.relative_to(ROOT).as_posix()
            offenders[rel] = sorted((n, lines[n - 1].strip()) for n in hits)
    return offenders


# --- the guard ----------------------------------------------------------------

def test_no_method_string_list_lives_outside_the_canonical_module():
    """#520's work order is done: a hit here is a fresh literal list, i.e. drift."""
    offenders = _scan()
    assert offenders == {}, (
        "a method-string list still lives outside core_brain/close_methods.py: "
        f"{offenders}")


# --- scanner self-checks -------------------------------------------------------

def test_the_scan_covers_the_real_files():
    files = _scan_files()
    rels = {p.relative_to(ROOT).as_posix() for p in files}
    assert files, "the scan found no files at all"
    assert "core_brain/kpi.py" in rels
    assert "scripts/rescue_exit_report.py" in rels
    assert "dashboard/static/app.js" in rels
    assert "core_brain/close_methods.py" not in rels
    assert "scripts/hooks/git_workflow_guard.py" not in rels


def test_the_scanner_flags_a_python_method_tuple():
    assert _python_list_hits(ast.parse("X = ('merge', 'shadow_merge')"))
    assert _python_list_hits(ast.parse("X = {'single_buy_exit'}"))
    assert _python_list_hits(ast.parse("X = [None, 'ladder_exit']"))


def test_the_scanner_flags_a_query_in_string():
    hits = _query_hits(ast.parse(
        "q = \"WHERE run_id = ? AND method NOT IN ('settlement', '')\""))
    assert hits


def test_the_scanner_flags_a_js_set_array():
    assert _js_hits(["const E = new Set(['single_buy_exit', 'naked_exit']);"])
    assert _js_hits(['const E = ["ladder_exit"];'])


def test_the_scanner_leaves_writer_strings_alone():
    assert not _python_list_hits(ast.parse('record(method="merge")'))
    assert not _python_list_hits(ast.parse("row['method'] == 'shadow_merge'"))


def test_the_scanner_leaves_mixed_rows_and_the_order_side_alone():
    assert not _python_list_hits(ast.parse("X = ('merge', 0.3, 'demo')"))
    assert not _python_list_hits(ast.parse("sides = ('buy', 'sell')"))


# --- the pins -------------------------------------------------------------------

def test_every_record_is_written_or_marked_legacy():
    for record in cm.METHODS:
        exactly_one = (record.writer is not None) != record.legacy
        assert exactly_one, (
            f"{record.name}: needs exactly one of a writer or legacy=True")


def _writer_node(dotted: str) -> tuple[ast.AST, str] | None:
    """Find the node a dotted writer path points at, plus the file source.

    shortcut: writers are ``core_brain.<module>.<node>`` or
    ``core_brain.<module>.<Class>.<method>``; a deeper package path would
    need the module part walked as a package.
    """
    parts = dotted.split(".")
    assert parts[0] == "core_brain" and len(parts) in (3, 4), dotted
    path = ROOT / "core_brain" / f"{parts[1]}.py"
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    if len(parts) == 3:
        wanted = parts[2]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.name == wanted:
                return node, source
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == parts[2]:
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef,
                                    ast.AsyncFunctionDef)) and sub.name == parts[3]:
                    return sub, source
    return None


def test_every_writer_really_writes_its_method():
    """Each writer path points at a live node that still holds the string."""
    for record in cm.METHODS:
        if record.writer is None:
            continue
        found = _writer_node(record.writer)
        assert found is not None, f"{record.name}: no node at {record.writer}"
        node, source = found
        literals = {n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert record.name in literals, (
            f"{record.name}: {record.writer} never names the string")
        segment = ast.get_source_segment(source, node) or ""
        assert any(tok in segment for tok in ("CloseRecord", "log_close", "method")), (
            f"{record.name}: {record.writer} does not touch a close record")


def test_the_module_is_a_standard_library_leaf():
    source = CANONICAL.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            top = module.split(".")[0]
            assert top in sys.stdlib_module_names, f"non-stdlib import: {module}"
            assert top != "core_brain", "the leaf must not import core_brain"


def test_a_settlement_is_not_a_rescue():
    assert set(cm.SETTLEMENT_METHODS).isdisjoint(cm.RESCUE_METHODS)
    for record in cm.METHODS:
        if record.name in cm.SETTLEMENT_METHODS:
            assert record.bucket == cm.SETTLEMENT_BUCKET, record.name


def test_each_derived_list_equals_its_bucket():
    for bucket, derived in (
        (cm.MERGE_BUCKET, cm.MERGE_METHODS),
        (cm.RESCUE_BUCKET, cm.RESCUE_METHODS),
        (cm.SETTLEMENT_BUCKET, cm.SETTLEMENT_METHODS),
        (cm.BOOKKEEPING_BUCKET, cm.BOOKKEEPING_METHODS),
    ):
        expected = tuple(r.name for r in cm.METHODS if r.bucket == bucket)
        assert derived == expected


def test_every_declared_name_is_in_all_method_names():
    assert cm.ALL_METHOD_NAMES == frozenset(r.name for r in cm.METHODS)
    for record in cm.METHODS:
        assert record.name in cm.ALL_METHOD_NAMES


def test_the_derived_lists_match_todays_consumer_tuples():
    """The order #559/#560 will inherit — behaviour-preserving by contract."""
    assert cm.MERGE_METHODS == ("merge", "shadow_merge")
    assert cm.RESCUE_METHODS == ("single_buy_exit", "orphan_profit_exit", "naked_exit", "ladder_exit")
    assert cm.BOOKKEEPING_METHODS == ("venue_sync",)
    assert cm.DASHBOARD_FORCED_EXIT_METHODS == (
        "single_buy_exit", "orphan_profit_exit", "naked_exit", "ladder_exit",
        "aged_out_exit", "stop_loss_exit")
    assert cm.TAKER_RESOLVED_METHODS == (
        "shadow_merge", "single_buy_exit", "orphan_profit_exit", "naked_exit", "ladder_exit")


def test_app_js_exit_methods_is_pinned_to_the_leaf():
    """app.js cannot import the Python leaf, so its EXIT_METHODS is a pinned
    copy of DASHBOARD_FORCED_EXIT_METHODS. The pin keeps the copy honest: a
    change to the leaf's forced-exit set fails here until app.js follows."""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8-sig")
    match = re.search(r"const EXIT_METHODS = new Set\(\[([^\]]*)\]\)", app_js)
    assert match, "app.js EXIT_METHODS not found"
    values = [a or b for a, b in _JS_QUOTED.findall(match.group(1))]
    assert tuple(values) == cm.DASHBOARD_FORCED_EXIT_METHODS


# --- consumer routing pins (#559) -----------------------------------------------


def test_pnl_by_fill_path_routes_every_close_into_one_bucket():
    """#559: the fill-path split is total-preserving. A settled close and an
    unclassified close used to land in no bucket at all, so `total` was short
    of the run's realized PnL and every rendered share divided by a wrong one."""
    from core_brain.kpi import pnl_by_fill_path

    closes = [
        {"method": cm.MERGE, "realized_pnl": 0.30, "tx_hash": "pair-chain"},
        {"method": cm.MERGE, "realized_pnl": 0.20, "tx_hash": "pair-taker"},
        {"method": cm.SHADOW_MERGE, "realized_pnl": 0.10, "tx_hash": "pair-shadow"},
        {"method": cm.SINGLE_BUY_EXIT, "realized_pnl": -0.40},
        {"method": cm.SHADOW_SETTLEMENT, "realized_pnl": -0.50},
        {"method": cm.SELL, "realized_pnl": -0.10},
        {"method": cm.VENUE_SYNC, "realized_pnl": 9.99},
    ]

    res = pnl_by_fill_path(closes, taker_pairs={"pair-taker"})

    assert set(res["by_path"]) == set(res["pct"]) == {
        "maker_merged", "taker_completed", "single_buy_exit", "settled", "other"}
    assert res["by_path"]["maker_merged"] == pytest.approx(0.40)
    assert res["by_path"]["taker_completed"] == pytest.approx(0.20)
    assert res["by_path"]["single_buy_exit"] == pytest.approx(-0.40)
    assert res["by_path"]["settled"] == pytest.approx(-0.50)
    assert res["by_path"]["other"] == pytest.approx(-0.10)
    # `venue_sync` is bookkeeping, not a fill path, and nothing else is dropped.
    assert res["total"] == pytest.approx(-0.40)


def test_a_settled_close_is_never_counted_as_a_rescue():
    """#559: the memo's rescue block counts settlements on their own line, so
    "rescue" means the exits -- and only the exits -- in every report."""
    from statistical_validation_run.artifacts import rescue_stats

    stats = rescue_stats([
        {"method": cm.SHADOW_MERGE, "realized_pnl": 0.30},
        {"method": cm.SINGLE_BUY_EXIT, "realized_pnl": -0.60},
        {"method": cm.SHADOW_SETTLEMENT, "realized_pnl": -1.00},
    ])

    assert stats["merges"] == 1
    assert stats["exits"] == 1
    assert stats["settlements"] == 1
    assert stats["other_methods"] == []
    # The rescue denominator is unchanged: a settlement is not a completion and
    # not a rescue, so it never enters either rate.
    assert stats["completion_rate"] == pytest.approx(0.5)
    assert stats["exit_rate"] == pytest.approx(0.5)
