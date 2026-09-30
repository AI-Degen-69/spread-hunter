"""D12 admission trial: pure trial identity classification.

Wraps the SHIPPED `identity_allowed` without changing it. The treatment arm
relaxes exactly two refusals -- a group-label refusal and a not-primary
refusal -- and only when the row still passes with the group label withheld
and `require_primary=False`. Blocked-keyword, resolved and unreadable rows
are never relaxed: those verdicts are about the market's own state, not its
shape in the venue's taxonomy.
"""
from __future__ import annotations

from scoring.selector import identity_allowed

MAINLINE = "mainline"
SUBMARKET = "submarket"
REFUSED = "refused"


def _text(*values: object) -> str:
    return " ".join(str(v or "") for v in values).strip()


def classify_identity(title: object = "", slug: object = "",
                      category: object = "", market_type: object = "",
                      market_group: object = "", series_title: object = "",
                      event_title: object = "") -> dict:
    """Shipped verdict plus the treatment role for one candidate.

    Returns `shipped_ok` / `shipped_reason` (the unchanged shipped decision)
    and `role`: MAINLINE (shipped admits, no group label), SUBMARKET (shipped
    admits a line-shaped row, or a relaxable refusal the re-check passes), or
    REFUSED. `role_reason` names the shipped reason behind a relaxed or refused
    row so the audit can tell them apart.
    """
    shipped_ok, shipped_reason = identity_allowed(
        title, slug, category, market_type, market_group,
        series_title, event_title, require_primary=True)
    if shipped_ok:
        role = MAINLINE if not _text(market_group) else SUBMARKET
        return {"shipped_ok": True, "shipped_reason": "",
                "role": role, "role_reason": ""}
    relaxable = (
        shipped_reason == "not a primary Moneyline/Outright or Macro/Politics market"
        or shipped_reason.startswith("carries a submarket group label"))
    if relaxable:
        again_ok, _ = identity_allowed(
            title, slug, category, market_type, "",
            series_title, event_title, require_primary=False)
        if again_ok:
            return {"shipped_ok": False, "shipped_reason": shipped_reason,
                    "role": SUBMARKET, "role_reason": shipped_reason}
    return {"shipped_ok": False, "shipped_reason": shipped_reason,
            "role": REFUSED, "role_reason": shipped_reason}
