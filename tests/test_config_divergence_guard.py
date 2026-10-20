"""#571/#652: no shared setting may be re-declared with a different value.

The two config modules used to carry 69 copies of the same number, held together
by nothing but a test that compared a handful of them. `scoring.config` now
inherits every shared field, so the only way to bring a second declaration back
is to write one into the scoring class body. This file reads that class body
with `ast` to catch it, and compares the defaults themselves for the values.

#652 moved the last eight bars -- the picker's filter bars -- onto core too. The
scoring class body now declares the five `PICKER_PROFILE` fields and `db_path`,
nothing else.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from core_brain import config as core_config
from core_brain.config import PICKER_PROFILE
from scoring import config as scoring_config

SCORING_CLASS = "MakerConfig"

# The eight picker filter bars that now live on core (#652). Each one must be a
# real core dataclass field, reachable on the picker at the same default, and
# reachable by its old picker name through the alias table. This set is used by
# the affirmative checks only -- it is NEVER an excuse list for the class body.
CORE_OWNED_PICKER_BARS = frozenset({
    "filter_live_vel_lookback_sec",
    "filter_live_vel_min_usd",
    "filter_live_gate_velocity_on",
    "filter_live_move_lookback_sec",
    "filter_live_move_min_range_cents",
    "filter_live_activity_min_ratio",
    "filter_std_queue_max_clear_min",
    "filter_std_gate_queue_max_on",
})

# The old picker name of each bar, pinned old -> canonical. Spelled out instead
# of read from a table, so that a bar cannot quietly change meaning. These names
# are GONE: the purge removed the alias table, so each one must now be neither a
# field nor a readable attribute.
PICKER_BAR_ALIASES = {
    "select_max_queue_minutes": "filter_std_queue_max_clear_min",
    "enforce_max_queue_minutes": "filter_std_gate_queue_max_on",
    "select_movement_window_sec": "filter_live_vel_lookback_sec",
    "select_min_movement_usd": "filter_live_vel_min_usd",
    "select_velocity_gate_enabled": "filter_live_gate_velocity_on",
    "select_min_activity_ratio": "filter_live_activity_min_ratio",
    "select_min_range_cents": "filter_live_move_min_range_cents",
    "select_volatility_window_sec": "filter_live_move_lookback_sec",
}

# The five values the picker deliberately runs at a different level. Spelled out
# here rather than read from the table, so adding a sixth to `PICKER_PROFILE`
# fails this file instead of quietly widening the disagreement.
PROFILE_NAMES = frozenset({
    "bot_quote_bank_baseline_usd",
    "bot_risk_exposure_max_naked_usd",
    "bot_quote_size_min_shares",
    "bot_quote_book_max_spread_from_mid",
    "bot_pos_price_max_pair_cost_usd",
})

# The one method the scoring class body is allowed to keep: the `db_path()`
# duplicate of core's, pinned by `test_config_single_source.py` under three env
# states. Anything else declared as a function here could shadow a core field.
SCORING_OWN_METHODS = frozenset({"db_path"})


def _scoring_class_body() -> list[ast.stmt]:
    """The real `scoring.config.MakerConfig` body, parsed from its own source."""
    tree = ast.parse(Path(scoring_config.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == SCORING_CLASS:
            return node.body
    raise AssertionError(f"no `class {SCORING_CLASS}` found in {scoring_config.__file__}")


def _declared_names(body: list[ast.stmt] | None = None) -> list[str]:
    """Assigned or annotated names in the scoring class body, in source order."""
    if body is None:
        body = _scoring_class_body()
    names: list[str] = []
    for stmt in body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return names


def _defined_function_names(body: list[ast.stmt] | None = None) -> list[str]:
    """Functions defined in the scoring class body, in source order."""
    if body is None:
        body = _scoring_class_body()
    return [
        stmt.name for stmt in body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _defaults(cls) -> dict[str, object]:
    return {f.name: getattr(cls(), f.name) for f in dataclasses.fields(cls)}


def _field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_the_picker_is_core_config():
    """A subclass, not a copy: this is what makes the move a single source."""
    assert issubclass(scoring_config.MakerConfig, core_config.MakerConfig)
    assert scoring_config.MakerConfig is not core_config.MakerConfig


def test_no_shared_setting_has_diverged():
    """Every shared field agrees with core -- except the five on the profile.

    Read off the LIVE classes, never a copy of the expected table: the point is
    to compare the two shipped configs, so a field that exists on both sides and
    holds two numbers fails here naming both of them.
    """
    core_defaults = _defaults(core_config.MakerConfig)
    scoring_defaults = _defaults(scoring_config.MakerConfig)

    diverged = {}
    for name, core_value in core_defaults.items():
        if name not in scoring_defaults:
            continue
        expected = PICKER_PROFILE[name] if name in PROFILE_NAMES else core_value
        if scoring_defaults[name] != expected:
            diverged[name] = (expected, scoring_defaults[name])

    assert not diverged, (
        "shared setting(s) re-declared at a different value "
        "(field: (expected, scoring)): " + repr(diverged))


def test_every_core_setting_is_still_reachable_on_the_picker():
    """Inheritance has to hand the picker the whole surface, not most of it."""
    assert _field_names(core_config.MakerConfig) <= _field_names(scoring_config.MakerConfig)


def test_the_picker_declares_no_field_core_does_not_have():
    """The reverse direction: nothing may be left behind on the picker alone.

    #652's end state is one field set, not two that happen to overlap -- a
    leftover declaration is a second number whatever its value.
    """
    extra = _field_names(scoring_config.MakerConfig) - _field_names(core_config.MakerConfig)

    assert not extra, (
        "scoring.config.MakerConfig declares field(s) core does not have; "
        f"they belong on core: {sorted(extra)}")


def test_the_profile_is_exactly_these_five_fields():
    assert set(PICKER_PROFILE) == PROFILE_NAMES

    scoring_defaults = _defaults(scoring_config.MakerConfig)
    core_defaults = _defaults(core_config.MakerConfig)
    for name, value in PICKER_PROFILE.items():
        assert scoring_defaults[name] == value, name
        # A profile entry that equals core's value is not a difference: it is a
        # knob that moved out of core for no reason, and it belongs back there.
        assert value != core_defaults[name], name


def test_the_class_body_declares_only_the_pickers_own_fields():
    """A shared field restated here is the drift this ticket exists to stop.

    The allowed set is `PROFILE_NAMES` alone -- deliberately NOT
    `CORE_OWNED_PICKER_BARS`. Those names are core fields now, so allowing them
    here would let a re-declared bar with the identical value through, which is
    exactly the second declaration this guard exists to catch.
    """
    extra = set(_declared_names()) - PROFILE_NAMES

    assert not extra, (
        "scoring.config.MakerConfig re-declares shared field(s); delete them "
        f"and inherit instead: {sorted(extra)}")


def test_the_class_body_defines_no_other_method_or_property():
    """A function here can shadow a core field. Only `db_path` may stay."""
    defined = set(_defined_function_names())

    assert defined <= SCORING_OWN_METHODS, (
        "scoring.config.MakerConfig defines method(s) beside the allowed "
        f"{sorted(SCORING_OWN_METHODS)}: {sorted(defined - SCORING_OWN_METHODS)}")


def test_the_core_owned_picker_bars_exist_once_on_core():
    """Each bar is a real core dataclass field, and there is no second copy."""
    core_names = _field_names(core_config.MakerConfig)

    assert CORE_OWNED_PICKER_BARS <= core_names, sorted(CORE_OWNED_PICKER_BARS - core_names)


def test_the_core_owned_picker_bars_are_inherited_at_the_same_default():
    """Reachable on the picker, same default on both sides, no local override."""
    scoring_defaults = _defaults(scoring_config.MakerConfig)
    core_defaults = _defaults(core_config.MakerConfig)

    for name in sorted(CORE_OWNED_PICKER_BARS):
        assert name in scoring_defaults, name
        assert scoring_defaults[name] == core_defaults[name], name


def test_the_old_picker_names_are_gone():
    """The retired picker names are neither fields nor readable attributes."""
    core_names = _field_names(core_config.MakerConfig)
    scoring_names = _field_names(scoring_config.MakerConfig)
    core_cfg = core_config.MakerConfig()
    scoring_cfg = scoring_config.MakerConfig()

    for old, canonical in sorted(PICKER_BAR_ALIASES.items()):
        assert canonical in CORE_OWNED_PICKER_BARS, old
        assert old not in core_names, old
        assert old not in scoring_names, old
        with pytest.raises(AttributeError):
            getattr(core_cfg, old)
        with pytest.raises(AttributeError):
            getattr(scoring_cfg, old)


def test_profile_defaults_read_the_table_instead_of_a_literal():
    """5.0 written as `1000.0` again is the copy this ticket removed."""
    by_name = {stmt.target.id: stmt.value for stmt in _scoring_class_body()
               if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}

    wrong = sorted(
        name for name in PROFILE_NAMES
        if not (isinstance(by_name.get(name), ast.Subscript)
                and isinstance(by_name[name].value, ast.Name)
                and by_name[name].value.id == "PICKER_PROFILE")
    )

    assert not wrong, (
        "profile field(s) hold a literal instead of reading PICKER_PROFILE: "
        f"{wrong}")


def test_declared_names_catches_unannotated_assigns():
    """Unannotated assignments in class body must also be captured."""
    src = "class Foo:\n    x: int = 1\n    y = 2\n"
    cls_def = ast.parse(src).body[0]
    assert isinstance(cls_def, ast.ClassDef)
    assert _declared_names(cls_def.body) == ["x", "y"]


def test_defined_function_names_catches_methods_and_properties():
    """The method guard must see plain functions, `async def` and properties."""
    src = (
        "class Foo:\n"
        "    x: int = 1\n"
        "    def a(self):\n"
        "        pass\n"
        "    async def b(self):\n"
        "        pass\n"
        "    @property\n"
        "    def c(self):\n"
        "        return 1\n"
    )
    cls_def = ast.parse(src).body[0]
    assert isinstance(cls_def, ast.ClassDef)
    assert _defined_function_names(cls_def.body) == ["a", "b", "c"]
