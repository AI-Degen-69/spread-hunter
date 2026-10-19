"""Single source of truth for retired environment variables.

Shared inventory of retired HUNTER_* knobs, canonical BOT_* replacement guidance,
and runtime warning specifications. Consumed by core_brain/config.py (runtime warnings)
and scripts/shadow_tournament.py (plan-time refusal guards).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class RetiredEnvKey:
    """Specification of a retired environment variable.

    Attributes:
        name: Environment variable name (e.g. 'HUNTER_STOP_LOSS_TRIGGER_USD_CENTS').
        plan_guidance: Guidance for tournament planner / static refusal checks.
        runtime_warning: Optional format string for runtime logger warning. None if purged without warning.
        runtime_warning_args: Arguments passed to runtime logger warning alongside name.
    """
    name: str
    plan_guidance: str
    runtime_warning: Optional[str] = None
    runtime_warning_args: tuple[str, ...] = ()


RETIRED_ENV_KEYS: tuple[RetiredEnvKey, ...] = (
    # 1. HUNTER_SINGLE_BUY_GRACE_SEC (warned, #619) -> BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC
    RetiredEnvKey(
        name="HUNTER_SINGLE_BUY_GRACE_SEC",
        plan_guidance=(
            "retired (#619) with no equivalent; "
            "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC is a different timing setting, "
            "review it before using it"
        ),
        runtime_warning="%s is retired and ignored (#619); set %s instead.",
        runtime_warning_args=("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC (patience)",),
    ),
    # 2. HUNTER_STOP_LOSS_TRIGGER_USD_CENTS (warned, #619) -> BOT_RISK_STOP_ADVERSE_DROP_CENTS / BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    RetiredEnvKey(
        name="HUNTER_STOP_LOSS_TRIGGER_USD_CENTS",
        plan_guidance=(
            "retired (#619); the stop is entry-relative now: set "
            "BOT_RISK_STOP_MODE=ABSOLUTE_DROP plus "
            "BOT_RISK_STOP_ADVERSE_DROP_CENTS (dollars per share, so 0.05 means "
            "five cents)"
        ),
        runtime_warning="%s is retired and ignored (#619); set %s instead.",
        runtime_warning_args=("BOT_RISK_STOP_ADVERSE_DROP_CENTS (dollars) or BOT_RISK_STOP_MAX_DRAWDOWN_PCT",),
    ),
    # 3. HUNTER_STOP_LOSS_TRIGGER_PCT (warned, #619) -> BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    RetiredEnvKey(
        name="HUNTER_STOP_LOSS_TRIGGER_PCT",
        plan_guidance=(
            "retired (#619); the stop is entry-relative now: set "
            "BOT_RISK_STOP_MODE=PERCENTAGE_DROP plus "
            "BOT_RISK_STOP_MAX_DRAWDOWN_PCT (percent of the entry)"
        ),
        runtime_warning="%s is retired and ignored (#619); set %s instead.",
        runtime_warning_args=("BOT_RISK_STOP_MAX_DRAWDOWN_PCT",),
    ),
    # 4. HUNTER_REFUSAL_HOLD_POLICY (warned, #622) -> no replacement
    RetiredEnvKey(
        name="HUNTER_REFUSAL_HOLD_POLICY",
        plan_guidance="retired (#622) with no replacement; remove it",
        runtime_warning="%s is retired and ignored (#622); it has no replacement.",
        runtime_warning_args=(),
    ),
    # 5. HUNTER_REFUSAL_HOLD_DRIFT (warned, #622) -> no replacement
    RetiredEnvKey(
        name="HUNTER_REFUSAL_HOLD_DRIFT",
        plan_guidance="retired (#622) with no replacement; remove it",
        runtime_warning="%s is retired and ignored (#622); it has no replacement.",
        runtime_warning_args=(),
    ),
    # 6. HUNTER_SINGLE_BUY_MAX_LOSS_PCT (warned, #573/#619) -> BOT_RISK_STOP_MODE + BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    RetiredEnvKey(
        name="HUNTER_SINGLE_BUY_MAX_LOSS_PCT",
        plan_guidance=(
            "retired (#573 renamed it, #619 replaced it); set "
            "BOT_RISK_STOP_MODE=PERCENTAGE_DROP plus "
            "BOT_RISK_STOP_MAX_DRAWDOWN_PCT"
        ),
        runtime_warning=(
            "%s is deprecated and ignored (#573 renamed it); #619 replaced "
            "both with the entry-relative stop: set BOT_RISK_STOP_MODE "
            "plus BOT_RISK_STOP_ADVERSE_DROP_CENTS / BOT_RISK_STOP_MAX_DRAWDOWN_PCT instead."
        ),
        runtime_warning_args=(),
    ),
    # 7. HUNTER_SINGLE_BUY_MAX_LOSS_USD (warned, #573/#619) -> BOT_RISK_STOP_MODE + BOT_RISK_STOP_ADVERSE_DROP_CENTS
    RetiredEnvKey(
        name="HUNTER_SINGLE_BUY_MAX_LOSS_USD",
        plan_guidance=(
            "retired (#573 renamed it, #619 replaced it); set "
            "BOT_RISK_STOP_MODE=ABSOLUTE_DROP plus "
            "BOT_RISK_STOP_ADVERSE_DROP_CENTS"
        ),
        runtime_warning=(
            "%s is deprecated and ignored (#573 renamed it); #619 replaced "
            "both with the entry-relative stop: set BOT_RISK_STOP_MODE "
            "plus BOT_RISK_STOP_ADVERSE_DROP_CENTS / BOT_RISK_STOP_MAX_DRAWDOWN_PCT instead."
        ),
        runtime_warning_args=(),
    ),
    # 8. HUNTER_REWARD_OFFSET (purged 2026-10-12, no runtime warning)
    RetiredEnvKey(
        name="HUNTER_REWARD_OFFSET",
        plan_guidance=(
            "purged 2026-10-12 with no environment replacement; the static "
            "offset is a shipped field default, remove it"
        ),
        runtime_warning=None,
        runtime_warning_args=(),
    ),
)

RETIRED_ENV_OVERRIDES: dict[str, str] = {
    record.name: record.plan_guidance for record in RETIRED_ENV_KEYS
}


def iter_runtime_warnings() -> Iterator[tuple[str, str, tuple[str, ...]]]:
    """Yield (env_var_name, format_template, args) for keys configured with a runtime warning."""
    for record in RETIRED_ENV_KEYS:
        if record.runtime_warning is not None:
            yield (record.name, record.runtime_warning, record.runtime_warning_args)
