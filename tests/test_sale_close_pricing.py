"""Guard: the retired sale-close pricing knobs stay deleted (#588).

The knobs priced a close path no writer ever takes: the #520 writers table
records `sell` as a dataclass default with no writer, and the #569
observation run booked 12 closes with zero sales. They are named nowhere in
this file on purpose -- the acceptance criterion for #588 is that no
reference to them remains anywhere in the repo, so this asserts their
absence by field-name pattern instead of by name.
"""
from __future__ import annotations

import dataclasses

from core_brain.config import MakerConfig as CoreMakerConfig
from scoring.config import MakerConfig as ScoringMakerConfig


def test_no_sale_close_pricing_fields_survive() -> None:
    """Neither config may carry a sale-close pricing knob again."""
    for cfg_cls in (CoreMakerConfig, ScoringMakerConfig):
        names = {f.name for f in dataclasses.fields(cfg_cls)}
        leaked = sorted(n for n in names if "take" in n or "sale" in n)
        assert not leaked, f"sale-close pricing fields reintroduced: {leaked}"
