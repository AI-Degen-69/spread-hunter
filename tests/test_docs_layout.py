"""Docs layout guard (issue #366): showcases live in docs/issues/, never in docs/.

Issue showcases belong in `docs/issues/` per the layout contract in
`docs/agents/architecture.md` ("Where generated files go"). Four of them once
sat directly under `docs/`; this guard fails while any `<id>-presentation-*.html`
(or the sibling `<id>-showcase-*.html` form) sits there, and while the leftover
`project/` directory exists.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
ISSUES_DIR = DOCS_DIR / "issues"

# Numeric issue id, then the showcase kind, then a slug: 297-presentation-foo.html.
# Both contract forms are covered -- docs/issues/ already holds presentation and
# showcase files, so a presentation-only pattern would let a stray showcase slip past.
SHOWCASE_RE = re.compile(r"^\d+-(presentation|showcase)-.+\.html$")

RELOCATED_SHOWCASES = (
    "297-presentation-instance-lock.html",
    "304-presentation-queue-hold.html",
    "310-presentation-rescue-exit-forensics.html",
    "343-presentation-live-ladder-pricing.html",
)


def _direct_showcases() -> list[str]:
    """Relative POSIX paths of showcase files sitting directly under docs/."""
    offenders: list[str] = []
    if DOCS_DIR.is_dir():
        for path in sorted(DOCS_DIR.iterdir()):
            if path.is_file() and SHOWCASE_RE.match(path.name):
                offenders.append(path.relative_to(ROOT).as_posix())
    return offenders


def test_no_showcase_directly_under_docs():
    offenders = _direct_showcases()
    assert not offenders, (
        "issue showcases belong in docs/issues/, not directly under docs/:\n"
        + "\n".join(offenders)
    )


def test_project_directory_absent():
    assert not (ROOT / "project").exists(), (
        "leftover project/ directory is back; see the decision record in "
        "docs/agents/architecture.md"
    )


def test_showcases_live_in_docs_issues():
    """Sentinel: the other two tests must not pass with nothing to check."""
    assert ISSUES_DIR.is_dir(), "docs/issues/ is missing entirely"
    present = {
        p.name
        for p in ISSUES_DIR.iterdir()
        if p.is_file() and SHOWCASE_RE.match(p.name)
    }
    assert present, "guard scanned no showcases in docs/issues/ -- it would pass vacuously"
    missing = [name for name in RELOCATED_SHOWCASES if name not in present]
    assert not missing, (
        "relocated showcases missing from docs/issues/:\n" + "\n".join(missing)
    )
