"""Issue #277: small controls miss the 44px touch target and focus ring.

DESIGN.md Accessibility requires touch targets >= 44px (`--touch-min`)
and keyboard reachability. `.analytics-ci-buttons button` was 36px,
`.analytics-chip` was 28px, and the focus outline did not use the
signal color the issue pins. Meta (read-only) chips stay compact but
are marked as non-controls; anything clickable gets the full target.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLES = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "styles.css"


@pytest.fixture(scope="module")
def css() -> str:
    return STYLES.read_text(encoding="utf-8")


def _blocks(css: str, selector: str) -> list[str]:
    return re.findall(
        rf"(?<![\w.-]){re.escape(selector)}\s*{{([^}}]*)}}", css
    )


def _min_height_px(block: str) -> int | None:
    if "var(--touch-min)" in block:
        return 44
    match = re.search(r"min-height\s*:\s*(\d+)px", block)
    return int(match.group(1)) if match else None


def test_ci_buttons_meet_touch_minimum(css):
    blocks = _blocks(css, ".analytics-ci-buttons button")
    assert blocks, ".analytics-ci-buttons button has no CSS rule"
    for block in blocks:
        height = _min_height_px(block)
        assert height is not None and height >= 44, (
            f".analytics-ci-buttons button block is below 44px: {block.strip()[:80]}"
        )


def test_interactive_chips_meet_touch_minimum(css):
    blocks = _blocks(css, ".analytics-chip-control")
    assert blocks, "no .analytics-chip-control interactive variant exists"
    for block in blocks:
        height = _min_height_px(block)
        assert height is not None and height >= 44, (
            "interactive chips must be >= 44px tall"
        )


def test_meta_chips_stay_compact_and_marked_non_interactive(css):
    blocks = _blocks(css, ".analytics-chip")
    assert blocks, ".analytics-chip has no CSS rule"
    base = blocks[0]
    height = _min_height_px(base)
    assert height is not None and height < 44, (
        "meta .analytics-chip must stay compact, not grow to a touch target"
    )
    assert "cursor" in base and "default" in base, (
        "meta .analytics-chip must be marked non-interactive (cursor: default)"
    )


def test_focus_ring_uses_signal_color_and_offset(css):
    assert re.search(
        r"button:focus-visible[^{]*\{[^}]*outline\s*:\s*2px\s+solid\s+var\(--signal\)",
        css,
    ), "no button :focus-visible rule with 2px solid var(--signal)"
    assert re.search(
        r"button:focus-visible[^{]*\{[^}]*outline-offset\s*:\s*2px", css
    ), "no button :focus-visible rule with 2px outline-offset"
