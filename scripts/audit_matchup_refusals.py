"""Audit the 'not a primary Moneyline/Outright or Macro/Politics market' refusal bucket.

Standalone, read-only script that inspects rejected rows from a market_universe.json snapshot,
fetches raw Gamma market and event details, applies discovery normalization,
and produces machine-readable forensic summaries and classification tables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scoring.selector import (
    _BLOCKED_RE,
    _FRAGMENT_LABEL_RE,
    _MACRO_RE,
    _PRIMARY_RE,
    _SPORTS_SERIES_RE,
    identity_allowed,
)
from scripts.filter_markets import _first_event, _first_series_title, _str

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
TARGET_REASON = "not a primary Moneyline/Outright or Macro/Politics market"


def parse_outcomes(outcomes_val: Any) -> list[str]:
    if isinstance(outcomes_val, str):
        try:
            parsed = json.loads(outcomes_val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (ValueError, TypeError):
            return [outcomes_val]
    elif isinstance(outcomes_val, list):
        return [str(x) for x in outcomes_val]
    return []


def classify_market(
    title: str,
    slug: str,
    group: str,
    market_type: str,
    series_title: str,
    event_title: str,
    outcomes: list[str],
    siblings: list[dict[str, Any]],
) -> tuple[str, str, str]:
    """Classify row into (classification, shape_tag, decisive_evidence).

    Classifications:
    - 'main line'
    - 'series/finals submarket'
    - 'season/futures'
    - 'other submarket'
    - 'unsupported (insufficient evidence)'
    """
    text = f"{title} {slug} {group} {series_title} {event_title}".lower()

    # Check for O/U totals or spread tokens
    if re.search(r"\bO/?U\b|over/under|total", text) or _FRAGMENT_LABEL_RE.search(group) or _FRAGMENT_LABEL_RE.search(title):
        shape = "over/under total or numeric band"
        if group and not _FRAGMENT_LABEL_RE.search(group):
            shape = "O/U total in title, non-fragment group"
        elif not group:
            shape = "O/U total in title, empty group"
        return "other submarket", shape, f"Title/group/slug contains total/band token ({title})"

    # Check for series/finals/best-of wording
    if re.search(r"\bbest\s+of\b|\bseries\b|\bgame\s+\d|\bmap\s+\d|\bfinals?\b|\bplayoffs?\b", text):
        return "series/finals submarket", "series/best-of/finals wording", f"Text contains series/finals marker ({title})"

    # Check for season/futures
    if re.search(r"\bchampion|\bseason|\bmvp\b|\bstandings\b", text):
        return "season/futures", "season/futures wording", f"Text contains season/championship marker ({title})"

    # Check outcomes: if exactly 2 team outcomes for a single game and no submarket marker
    if len(outcomes) == 2 and ("yes" not in [o.lower() for o in outcomes]):
        if _SPORTS_SERIES_RE.search(text):
            if group:
                return "main line", "league word present, bare team group", f"2-outcome head-to-head with league series and team group ({group})"
            return "main line", "league word present, empty group", "2-outcome head-to-head with league series and empty group"
        else:
            if group:
                return "main line", "no league word, bare team group", f"2-outcome head-to-head without league series, group: {group}"
            return "main line", "no league word, empty group", "2-outcome head-to-head without league series, empty group"

    # If it is binary Yes/No:
    if len(outcomes) == 2 and any(o.lower() in ("yes", "no") for o in outcomes):
        # Is it a moneyline disguised as Yes/No or submarket?
        if _SPORTS_SERIES_RE.search(text):
            return "unsupported (insufficient evidence)", "yes/no outcome with league word", "Binary Yes/No outcome on matchup"
        return "unsupported (insufficient evidence)", "yes/no outcome without league word", "Binary Yes/No outcome without league word"

    return "unsupported (insufficient evidence)", "unsupported shape", f"Insufficient structure to verify main line: outcomes={outcomes}"


def audit_universe(
    universe_path: Path,
    out_dir: Path,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if session is None:
        session = requests.Session()

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_responses_path = out_dir / "raw_responses.json"
    summary_path = out_dir / "audit_summary.json"

    with open(universe_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = data.get("rows", []) if isinstance(data, dict) else data
    target_rows = [
        r for r in rows
        if r.get("reject_reason") == TARGET_REASON
    ]

    print(f"Loaded {len(rows)} universe rows. Found {len(target_rows)} rows refused with '{TARGET_REASON}'.")

    raw_data: dict[str, Any] = {}
    if raw_responses_path.exists():
        try:
            with open(raw_responses_path, encoding="utf-8") as rf:
                raw_data = json.load(rf)
            print(f"Loaded {len(raw_data)} cached raw responses from {raw_responses_path.name}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"Warning: Could not load {raw_responses_path.name}: {exc}; treating the cache as empty.")
            raw_data = {}

    audited_rows = []

    for r in target_rows:
        cid = r.get("cid") or r.get("condition_id")
        if not cid:
            continue

        cached = raw_data.get(cid)
        if cached:
            m_raw = cached.get("market")
            event_raw = cached.get("event")
            siblings_raw = cached.get("siblings", [])
        else:
            time.sleep(0.05)
            # Fetch market
            resp = session.get(GAMMA_MARKETS, params={"condition_ids": cid}, timeout=20)
            if resp.status_code != 200:
                audited_rows.append({"cid": cid, "fetch_error": resp.status_code})
                continue
            res_json = resp.json()
            m_raw = res_json[0] if (isinstance(res_json, list) and res_json) else {}

            event_raw = _first_event(m_raw) if m_raw else {}
            siblings_raw = []
            event_id = event_raw.get("id")
            if event_id:
                time.sleep(0.05)
                ev_resp = session.get(f"{GAMMA_EVENTS}/{event_id}", timeout=20)
                if ev_resp.status_code == 200:
                    ev_data = ev_resp.json()
                    siblings_raw = ev_data.get("markets", [])

            raw_data[cid] = {
                "market": m_raw,
                "event": event_raw,
                "siblings": siblings_raw,
            }

        # Normalize fields via filter_markets helpers
        title = _str(m_raw.get("question")) or r.get("title") or ""
        slug = _str(m_raw.get("slug")) or r.get("slug") or ""
        category = (_str(m_raw.get("category"))
                    or _str(m_raw.get("categorySlug")))
        market_type = m_raw.get("marketType") or m_raw.get("type") or ""
        market_group = m_raw.get("groupItemTitle") or ""
        series_title = _first_series_title(event_raw)
        event_title = _str(event_raw.get("title"))
        outcomes = parse_outcomes(m_raw.get("outcomes"))

        classification, shape_tag, evidence = classify_market(
            title=title,
            slug=slug,
            group=market_group,
            market_type=market_type,
            series_title=series_title,
            event_title=event_title,
            outcomes=outcomes,
            siblings=siblings_raw,
        )

        ok, reason = identity_allowed(
            title=title,
            slug=slug,
            category=category,
            market_type=market_type,
            market_group=market_group,
            series_title=series_title,
            event_title=event_title,
        )

        audited_rows.append({
            "cid": cid,
            "title": title,
            "slug": slug,
            "category": category,
            "market_type": market_type,
            "groupItemTitle": market_group,
            "series_title": series_title,
            "event_title": event_title,
            "outcomes": outcomes,
            "sibling_count": len(siblings_raw),
            "current_ok": ok,
            "current_reason": reason,
            "classification": classification,
            "shape_tag": shape_tag,
            "decisive_evidence": evidence,
        })

    # Save raw responses cache
    with open(raw_responses_path, "w", encoding="utf-8") as rf:
        json.dump(raw_data, rf, indent=2)

    # Classifications and shape summary
    class_counts: dict[str, int] = {}
    shape_counts: dict[str, int] = {}
    for ar in audited_rows:
        c = ar["classification"]
        s = ar["shape_tag"]
        class_counts[c] = class_counts.get(c, 0) + 1
        shape_counts[s] = shape_counts.get(s, 0) + 1

    summary = {
        "universe_path": str(universe_path),
        "total_universe_rows": len(rows),
        "target_refused_count": len(target_rows),
        "audited_count": len(audited_rows),
        "classification_counts": class_counts,
        "shape_counts": shape_counts,
        "rows": audited_rows,
    }

    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, indent=2)

    print(f"Saved audit summary to {summary_path}")
    print("Classification summary:")
    for c, cnt in sorted(class_counts.items(), key=lambda x: -x[1]):
        print(f"  - {c}: {cnt}")
    print("Shape summary:")
    for s, cnt in sorted(shape_counts.items(), key=lambda x: -x[1]):
        print(f"  - {s}: {cnt}")

    return summary


def generate_markdown_report(summary: dict[str, Any], output_md: Path) -> None:
    rows = summary.get("rows", [])
    class_counts = summary.get("classification_counts", {})
    shape_counts = summary.get("shape_counts", {})

    lines = [
        "# Matchup Refusal Audit Report (Issue #355)",
        "",
        f"- **Universe source**: `{summary.get('universe_path')}`",
        f"- **Total universe rows**: {summary.get('total_universe_rows')}",
        f"- **Target refused rows (`{TARGET_REASON}`)**: {summary.get('target_refused_count')}",
        f"- **Audited rows**: {summary.get('audited_count')}",
        f"- **Generated at**: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}",
        "",
        "## Summary by Classification",
        "",
        "| Classification | Count | Description |",
        "| --- | --- | --- |",
    ]
    for c, cnt in sorted(class_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {c} | {cnt} | |")

    lines.extend([
        "",
        "## Summary by Shape Tag",
        "",
        "| Shape Tag | Count |",
        "| --- | --- |",
    ])
    for s, cnt in sorted(shape_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {s} | {cnt} |")

    lines.extend([
        "",
        "## Detailed Audited Rows",
        "",
        "| CID | Title | Group | Category | Series / Event | Outcomes | Classification | Shape Tag | Decisive Evidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])

    for r in rows:
        cid_short = r["cid"][:10]
        title = r["title"].replace("|", "/")
        group = (r["groupItemTitle"] or "-").replace("|", "/")
        cat = (r["category"] or "-").replace("|", "/")
        ser_ev = f"{r['series_title'] or '-'} / {r['event_title'] or '-'}".replace("|", "/")
        outcomes = ", ".join(r["outcomes"]).replace("|", "/")
        cls = r["classification"]
        shape = r["shape_tag"]
        ev = r["decisive_evidence"].replace("|", "/")
        lines.append(f"| `{cid_short}` | {title} | {group} | {cat} | {ser_ev} | {outcomes} | **{cls}** | {shape} | {ev} |")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated markdown report at {output_md}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit matchup refusals in market universe.")
    parser.add_argument("--universe", type=Path, default=ROOT / "runtime" / "market_universe.json",
                        help="Path to market_universe.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "runtime" / "audit_355",
                        help="Output directory for raw responses and audit summary")
    parser.add_argument("--report-md", type=Path, default=ROOT / "docs" / "issues" / "355-matchup-refusal-audit.md",
                        help="Path to generate markdown report")
    args = parser.parse_args()

    summary = audit_universe(args.universe, args.out_dir)
    generate_markdown_report(summary, args.report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
