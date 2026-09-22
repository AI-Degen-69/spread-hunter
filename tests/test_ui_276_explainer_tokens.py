"""Issue #276: strategy_explainer.html follows DESIGN.md tokens and decoration rules.

The explainer page drifted its own tokens (--bg-primary, --text-main),
a radial-gradient body background, a glow shadow, and missed the Big
Shoulders Display brand face. DESIGN.md says: dark-only slate base, no
gradients, no glow, color is never decorative, Big Shoulders for headers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

EXPLAINER = (
    Path(__file__).resolve().parent.parent
    / "dashboard"
    / "static"
    / "strategy_explainer.html"
)


@pytest.fixture(scope="module")
def page() -> str:
    return EXPLAINER.read_text(encoding="utf-8")


def test_no_radial_or_linear_gradients(page):
    lowered = page.lower()
    assert "radial-gradient" not in lowered
    assert "linear-gradient" not in lowered


def test_no_glow_shadow(page):
    lowered = page.lower()
    assert "--shadow-glow" not in lowered
    assert "0 0 25px" not in lowered


def test_root_tokens_match_design_md(page):
    assert "--bg-base" in page and "#080c14" in page
    assert "--text-primary" in page and "#f8fafc" in page
    assert "--bg-primary" not in page
    assert "--text-main" not in page


def test_headers_use_brand_face(page):
    assert "Big Shoulders Display" in page
    assert "font-display" in page
