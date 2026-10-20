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
    """Sentinel: the guard above must not pass with nothing to check.

    It used to require four named showcases to be present in `docs/issues/`.
    The e2f648e sweep deletes a closed issue's showcase artifacts, so "these
    four files must exist" is not a state this repo keeps any more -- and a
    sentinel that pins files the repo deliberately deletes is a sentinel that
    breaks the next time one is swept. The proof that the pattern is alive is
    the pattern itself: both contract forms must be recognised, and the
    near-misses must not be.
    """
    assert ISSUES_DIR.is_dir(), "docs/issues/ is missing entirely"
    assert SHOWCASE_RE.match("297-presentation-instance-lock.html")
    assert SHOWCASE_RE.match("297-showcase-instance-lock.html")
    assert not SHOWCASE_RE.match("297-presentation.html")
    assert not SHOWCASE_RE.match("presentation-297.html")
    assert not SHOWCASE_RE.match("297-slide-presentation-instance-lock.html")
