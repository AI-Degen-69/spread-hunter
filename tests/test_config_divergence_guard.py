"""#571: no shared setting may be re-declared with a different value.

The two config modules used to carry 69 copies of the same number, held together
by nothing but a test that compared a handful of them. `scoring.config` now
inherits every shared field, so the only way to bring a second declaration back
is to write one into the scoring class body. This file reads that class body
with `ast` to catch it, and compares the defaults themselves for the values.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from core_brain import config as core_config
from core_brain.config import PICKER_PROFILE
from scoring import config as scoring_config

SCORING_CLASS = "MakerConfig"

# The eight fields ONLY the ranker reads. Everything else declared in the
# scoring class body must be one of these or one of the five profile fields --
# a shared field restated here is a second number, whichever value it carries.
PICKER_ONLY = frozenset({
    "enforce_max_queue_minutes",
    "select_max_queue_minutes",
    "select_min_activity_ratio",
    "select_min_movement_usd",
    "select_min_range_cents",
    "select_movement_window_sec",
    "select_velocity_gate_enabled",
    "select_volatility_window_sec",
})

# The five values the picker deliberately runs at a different level. Spelled out
# here rather than read from the table, so adding a sixth to `PICKER_PROFILE`
# fails this file instead of quietly widening the disagreement.
PROFILE_NAMES = frozenset({
    "bankroll_usd",
    "max_naked_usd",
    "min_quote_shares",
    "max_spread_from_mid",
    "max_pair_cost",
})


def _scoring_class_body() -> list[ast.stmt]:
    """The real `scoring.config.MakerConfig` body, parsed from its own source."""
    tree = ast.parse(Path(scoring_config.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == SCORING_CLASS:
            return node.body
    raise AssertionError(f"no `class {SCORING_CLASS}` found in {scoring_config.__file__}")


def _declared_names() -> list[str]:
    """Annotated names in the scoring class body, in source order."""
    return [stmt.target.id for stmt in _scoring_class_body()
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)]


def _defaults(cls) -> dict[str, object]:
    return {f.name: getattr(cls(), f.name) for f in dataclasses.fields(cls)}


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
    scoring_fields = {f.name for f in dataclasses.fields(scoring_config.MakerConfig)}

    assert set(_defaults(core_config.MakerConfig)) <= scoring_fields


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
    """A shared field restated here is the drift this ticket exists to stop."""
    extra = set(_declared_names()) - PICKER_ONLY - PROFILE_NAMES

    assert not extra, (
        "scoring.config.MakerConfig re-declares shared field(s); delete them "
        f"and inherit instead: {sorted(extra)}")


def test_picker_only_fields_do_not_exist_on_core():
    """They are the picker's alone -- core declaring one means two numbers."""
    core_names = {f.name for f in dataclasses.fields(core_config.MakerConfig)}

    assert not (PICKER_ONLY & core_names), sorted(PICKER_ONLY & core_names)


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
