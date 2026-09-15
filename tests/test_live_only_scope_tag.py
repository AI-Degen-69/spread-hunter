"""Live-stack-only services say so on their own card.

A shadow rehearsal (`python -m core_brain.shadow_run`) runs the poll and quote
code in-process against a signer-less client, so `Venue Engine & Order Poller`
and `Execution Loop & Maker Quoter` stay STOPPED for the whole rehearsal. Two
gray cards next to two green ones read as a half-dead stack unless the cards
say which stack they belong to, so each carries a LIVE ONLY scope tag and a
title that names `core_brain.shadow_run` as the thing covering them instead.

The tag is scope, not state: DESIGN.md reserves every saturated hue for the
live-state vocabulary, so it renders in the quiet gray band.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
APP_JS = _STATIC / "app.js"
STYLES_CSS = _STATIC / "styles.css"
HARNESS = Path(__file__).resolve().parent / "js" / "live_only_scope_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")

LIVE_ONLY = ("query", "decide")
ALWAYS_ON = ("guardrail", "filter")


@pytest.fixture(scope="module")
def scoped() -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), str(APP_JS)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)["scoped"]


@requires_node
@pytest.mark.parametrize("key", LIVE_ONLY)
def test_live_only_services_carry_the_scope_tag(scoped, key):
    # Arrange — a shadow rehearsal: screener up, live stack down.
    card = scoped[key]

    # Act / Assert
    assert card["found"] is True
    assert card["hasScopePill"] is True
    assert card["hasLiveOnlyText"] is True


@requires_node
@pytest.mark.parametrize("key", LIVE_ONLY)
def test_the_scope_tag_names_shadow_run_as_what_covers_them(scoped, key):
    # The tag alone says "not this stack"; the title says what runs instead.
    assert scoped[key]["mentionsShadowRun"] is True


@requires_node
@pytest.mark.parametrize("key", LIVE_ONLY + ALWAYS_ON)
def test_the_scope_tag_reaches_the_live_only_cards_and_no_others(scoped, key):
    # One assertion over every card, so the tag's absence on the guardrail and
    # screener is checked against its presence next door rather than against a
    # template that carried no scope tag at all.
    card = scoped[key]

    assert card["found"] is True
    assert card["hasScopePill"] is (key in LIVE_ONLY)
    assert card["hasLiveOnlyText"] is (key in LIVE_ONLY)


@requires_node
@pytest.mark.parametrize("key", LIVE_ONLY + ALWAYS_ON)
def test_every_card_keeps_its_own_role_tag(scoped, key):
    # The scope tag is added beside the role tag, never in place of it: on a
    # live-only card both must be present at once.
    card = scoped[key]

    assert card["keepsOwnTag"] is True
    if key in LIVE_ONLY:
        assert card["hasScopePill"] is True


def test_the_scope_tag_stays_out_of_the_state_colour_vocabulary():
    # DESIGN.md: a hue may never appear without a state meaning behind it.
    css = STYLES_CSS.read_text(encoding="utf-8")
    start = css.index(".svc-scope-pill {")
    rule = css[start:css.index("}", start)]

    assert "var(--text-muted)" in rule
    for hue in ("--signal", "--warn", "--loss", "--open",
                "--green-", "--amber-", "--red-", "--blue-"):
        assert hue not in rule
