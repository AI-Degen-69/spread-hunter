"""One shared market-metadata resolver for KPI and registry state.

Active Markets showed `Uncategorized` for two compounding reasons: KPI matched
a condition id only against the current top-20 `markets.json`, and registry
state resolved titles a second way with no category at all. Both delegate
here now: the current feed first, the scored universe for departed markets,
slug/title keywords last. Category is display-only -- it never feeds
selection, ranking, or risk.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from core_brain.runtime_paths import resolve_runtime_file

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shown when neither the ranker feed nor its fallbacks name a category. A named
# bucket groups honestly; a blank cell reads as missing data.
UNCATEGORIZED = "Uncategorized"

ESPORTS_LABEL = "E-Sports"
POLITICS_LABEL = "Politics"

# Display-only vocabulary, seeded from the selection lists in
# scoring/selector.py (`_SPORTS_SERIES_RE` / `_MACRO_RE`) and kept local on
# purpose: importing the private selection regexes would couple a descriptive
# label to the screening rules it must never influence.
_ESPORTS_RE = re.compile(
    r"\b(?:esports?|lol|league\s*of\s*legends|dota|"
    r"counter[- ]strike|cs2|valorant)\b",
    re.IGNORECASE,
)
_POLITICS_RE = re.compile(
    r"\b(?:politics?|political|election|president(?:ial)?|"
    r"senate|congress|governor|prime\s*minister)\b",
    re.IGNORECASE,
)


def classify_display_category(title: Any, event_title: Any,
                              slug: Any) -> Optional[str]:
    """E-Sports, Politics, or None -- pure word-boundary match, no venue I/O."""
    text = " ".join((
        str(title or ""),
        str(event_title or ""),
        str(slug or "").replace("-", " ").replace("_", " "),
    )).lower()
    if not text.strip():
        return None
    if _ESPORTS_RE.search(text):
        return ESPORTS_LABEL
    if _POLITICS_RE.search(text):
        return POLITICS_LABEL
    return None


def _feed_rows(name: str, root: Path | str) -> list[dict]:
    """Rows of a runtime feed file; unreadable or misshapen reads as empty."""
    try:
        path = resolve_runtime_file(name, root=root)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows") or []
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]
    except Exception:
        return []


def _find_row(rows: list[dict], cid: str) -> Optional[dict]:
    for row in rows:
        if (row.get("cid") or "").lower() == cid.lower():
            return row
    return None


def _first_tag_label(row: dict) -> str:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        if isinstance(tag, dict):
            label = tag.get("label") or tag.get("slug")
            if label:
                return str(label).strip()
        elif isinstance(tag, str) and tag.strip():
            return tag.strip()
    return ""


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").title()


def resolve_market_meta(cid: str, closes: Optional[list[dict]] = None,
                        quotes: Optional[list[dict]] = None, *,
                        root: Optional[Path | str] = None) -> dict[str, Any]:
    """Human-readable title, slug, link, and category for a condition id.

    `root` is the caller's repo root (KPI and registry state each pass their
    own, which is also how tests redirect the feed). Precedence for category:
    feed `category` verbatim, `series_title`, `market_group`, first tag label,
    keyword fallback, `Uncategorized`.
    """
    out: dict[str, Any] = {
        "condition_id": cid,
        "title": None,
        "slug": None,
        "url": None,
        "category": None,
        "days_to_resolve": None,
        "min_size": None,
        "volume_24h": None,
        "source": None,
    }
    if not cid:
        out["category"] = UNCATEGORIZED
        return out

    base = REPO_ROOT if root is None else root
    row = _find_row(_feed_rows("markets.json", base), cid)
    if row is None:
        row = _find_row(_feed_rows("market_universe.json", base), cid)

    event_title = None
    if row is not None:
        event_title = row.get("event_title")
        out.update({
            "title": row.get("title") or event_title,
            "slug": row.get("slug"),
            # The live feed ships category="" on most rows, so the series,
            # the group, and the tags are the labels that actually survive.
            # An empty cell teaches the reader nothing.
            "category": (
                (row.get("category") or "").strip()
                or (row.get("series_title") or "").strip()
                or (row.get("market_group") or "").strip()
                or _first_tag_label(row)
                or None
            ),
            "days_to_resolve": row.get("days_to_resolve"),
            "min_size": row.get("min_size"),
            "volume_24h": row.get("volume_24h"),
            "source": row.get("source"),
        })

    # Fallback to closes or quotes market_slug
    if not out["slug"]:
        for c in closes or []:
            if c.get("condition_id") == cid and c.get("market_slug"):
                out["slug"] = c["market_slug"]
                break
    if not out["slug"]:
        for q in quotes or []:
            if q.get("condition_id") == cid and q.get("market_slug"):
                out["slug"] = q["market_slug"]
                break

    if not out["title"] and out["slug"]:
        out["title"] = _slug_to_title(out["slug"])
    elif not out["title"]:
        out["title"] = f"Market {cid[:10]}...{cid[-6:]}" if len(cid) > 16 else cid

    if out["slug"]:
        out["url"] = f"https://polymarket.com/market/{out['slug']}"
    if not out["category"]:
        out["category"] = (classify_display_category(
            out["title"], event_title, out["slug"]) or UNCATEGORIZED)
    return out
