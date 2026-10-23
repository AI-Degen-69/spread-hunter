"""Docs layout guard (issue #366): every HTML doc lives in docs/issues/.

HTML under `docs/` belongs to `docs/issues/` per the layout contract in
`docs/agents/architecture.md` ("Where generated files go"). This guard walks
every folder under `docs/` and fails while any `.html` file sits outside the
`docs/issues/` subtree, and while the leftover `project/` directory exists.

The scan is recursive since #719: two showcases sat one folder down, in
`docs/explanations/` and `docs/presentations/`, and a top-level-only scan stayed
green while they did.

Every `.html` is guarded now, not only files named `<id>-presentation-*.html`:
`docs/explanations/` filled up with ten ad-hoc explainers because no writer
owned that folder and no guard could see them -- `explain` suggested the path,
nothing else in the repo named it, and the showcase regex skipped them. The
three naming forms are pinned by `test_the_naming_contracts_are_alive`:

  <id>-presentation-<slug>.html   present-pr, ticketed work (Station VI commits it)
  <id>-showcase-<slug>.html       legacy form, still legal
  <topic-slug>.html               explain-mode, no ticket yet
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
ISSUES_DIR = DOCS_DIR / "issues"

# The one legal home: docs/issues/ itself and every folder below it.
ALLOWED_DOC_SUBTREE = ISSUES_DIR.name

# Numeric issue id, then the showcase kind, then a slug: 297-presentation-foo.html.
# Both contract forms are covered -- docs/issues/ has held presentation and
# showcase files, so a presentation-only pattern would let a stray showcase slip past.
SHOWCASE_RE = re.compile(r"^\d+-(presentation|showcase)-.+\.html$")

# Explain-mode form: kebab-case slug, no id, no presentation/showcase word.
EXPLAIN_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.html$")

HTML_SUFFIX = ".html"


def _stray_html_docs(docs_dir: pathlib.Path = DOCS_DIR) -> list[str]:
    """POSIX paths, relative to `docs_dir`, of HTML outside the issues/ subtree.

    Every depth is scanned, so a file sitting directly under `docs/` is caught
    the same way as one buried five folders down. `docs_dir` is a parameter so a
    test can plant a whole tree in `tmp_path` instead of trusting the real one to
    stay broken.
    """
    offenders: list[str] = []
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() != HTML_SUFFIX:
                continue
            relative = path.relative_to(docs_dir)
            if relative.parts[0] != ALLOWED_DOC_SUBTREE:
                offenders.append(relative.as_posix())
    return offenders


def test_no_html_outside_docs_issues():
    offenders = _stray_html_docs()
    assert not offenders, (
        "HTML docs belong in docs/issues/ or a folder below it, nowhere else "
        "under docs/:\n" + "\n".join(f"docs/{path}" for path in offenders)
    )


def test_a_stray_html_at_any_depth_is_caught(tmp_path):
    """Regression (#719): a subfolder under docs/ was a blind spot.

    The helper used to iterate `docs/` one level deep, so the two showcases in
    `docs/explanations/` and `docs/presentations/` never reached it. This plants
    a stray at the top, one folder down, and two folders down, next to two legal
    showcases inside `docs/issues/`, and pins the exact offender list.
    """
    docs = tmp_path / "docs"
    (docs / "explanations").mkdir(parents=True)
    (docs / "presentations" / "old").mkdir(parents=True)
    (docs / "issues" / "nested").mkdir(parents=True)
    (docs / "agents").mkdir(parents=True)

    for stray in (
        "123-showcase-top-level.html",
        "entry-gate-job.html",  # the ad-hoc explainer form that had no guard
        "explanations/498-presentation-tournament-run.html",
        "presentations/old/695-showcase-retired-env.html",
    ):
        (docs / stray).write_text("<html></html>", encoding="utf-8")

    for kept in (
        "issues/297-presentation-instance-lock.html",
        "issues/nested/366-showcase-repo-tidy.html",
        "issues/skew-and-taker-cross.html",  # explain-mode, no ticket
    ):
        (docs / kept).write_text("<html></html>", encoding="utf-8")

    # Markdown is not this guard's business.
    (docs / "issues" / "219-noticed-but-not-touching.md").write_text("note", encoding="utf-8")
    (docs / "agents" / "architecture.md").write_text("note", encoding="utf-8")

    assert _stray_html_docs(docs) == [
        "123-showcase-top-level.html",
        "entry-gate-job.html",
        "explanations/498-presentation-tournament-run.html",
        "presentations/old/695-showcase-retired-env.html",
    ]


def test_project_directory_absent():
    assert not (ROOT / "project").exists(), (
        "leftover project/ directory is back; see the decision record in "
        "docs/agents/architecture.md"
    )


def test_the_naming_contracts_are_alive():
    """Sentinel: the naming contract is alive, independently of what is on disk.

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

    # Explain-mode names, which the ten orphaned explainers did not follow:
    # 471-statistical-go-gate.html and 641-event-stream-process-isolation.html
    # read as if they were ticketed, skew_and_taker_cross_hebrew.html used
    # underscores.
    assert EXPLAIN_RE.match("skew-and-taker-cross.html")
    assert not EXPLAIN_RE.match("skew_and_taker_cross_hebrew.html")
    assert not EXPLAIN_RE.match("entry-gate.html.bak")
