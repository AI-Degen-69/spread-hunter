"""#422: a shadow rehearsal launched from the menu starts at the fixed $100.

The menu launched the rehearsal as

    python -m core_brain.shadow_run --minutes N --db <shadow>.db --run-id shadow-NN

with no bankroll flag at all, so what a session rehearsed under came from
whatever `SPREAD_HUNTER_BANKROLL` happened to be exported in the operator's
shell. A rehearsal exists to be compared against another rehearsal, so the
starting bankroll is now stated on the command line instead of inherited.

Journeys under test:
1. As the operator, starting a fresh shadow run launches the loop with an
   explicit `--starting-bankroll-usd 100`.
2. As the operator, RESUME does the same on the existing store -- a resumed
   session must rehearse under the same bankroll as the session it continues.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

# The flag and its value as the menu writes them into an argument array. A
# narrated log line does not match this: it carries spacing, not `", "` pairs.
_STATED_BANKROLL = '"--starting-bankroll-usd", "100"'


def _function_source(name: str) -> str:
    """Lift just `function <name> { ... }` out of the menu script.

    The menu script starts a live control centre when dot-sourced, so tests
    inspect the one function under test on its own.
    """
    text = MENU.read_text(encoding="utf-8")
    match = re.search(
        rf"^function {re.escape(name)} \{{.*?^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name} is no longer defined in the menu script"
    return match.group(0)


def _launching_lines(function: str) -> list[str]:
    """Lines that actually build shadow_run's argument list."""
    body = _function_source(function)
    return [
        line for line in body.splitlines()
        if "core_brain.shadow_run" in line
        and ("ArgumentList" in line or "shadowArgs = @" in line)
    ]


@pytest.mark.parametrize("function", ["Start-NewShadowRun", "Resume-ShadowRun"])
def test_a_shadow_launch_states_its_starting_bankroll(function):
    launching = _launching_lines(function)

    assert launching, f"{function} no longer launches core_brain.shadow_run"
    for line in launching:
        assert _STATED_BANKROLL in line, (
            f"{function} launches the rehearsal without a stated $100 bankroll, "
            f"so an exported SPREAD_HUNTER_BANKROLL decides it: {line.strip()}"
        )
