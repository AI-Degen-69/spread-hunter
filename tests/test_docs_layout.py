"""Docs layout guard (issue #366): one home per artifact kind under docs/.

HTML has exactly two homes, and the name says which:

  docs/pr-presentations/<pr>-<slug>.html   a visual summary of a pull request
  docs/explanations/<topic-slug>.html      a visual explainer, no ticket

The contract is `docs/agents/naming.md`. This guard walks every folder under
`docs/` and fails on an `.html` file that is in neither home, and on one whose
name does not match the home it sits in. Everything else — the ledger, specs,
plans, interviews, ideas, references, agents — is Markdown in its own folder,
and `tests/test_naming.py` polices those names.

The scan is recursive since #719: two showcases sat one folder down, in
`docs/explanations/` and `docs/presentations/`, and a top-level-only scan stayed
green while they did.

One home per kind replaced `docs/issues/` (#780): a folder nobody reserves dies
unowned — no writer, no guard, no reader — and ten ad-hoc explainers accumulated
in `docs/explanations/` while it was outside the guard. The fix is ownership,
not a shorter allowlist.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"

PRESENTATIONS_DIR = "pr-presentations"
EXPLANATIONS_DIR = "explanations"

# <pr>-<slug>.html: the pull-request number first, because that is how the
# operator looks for the page, and because Station VI commits showcases by id.
# A legacy `-presentation-`/`-showcase-` slug stays legal; the word is just part
# of the slug.
PRESENTATION_NAME_RE = re.compile(r"^\d+-[a-z0-9]+(?:-[a-z0-9]+)*\.html$")

# A topic slug and nothing else: no id, no date, no underscore. A leading digit
# would read as a PR number, which is why the explainers named
# `471-statistical-go-gate.html` were ambiguous about whether they were ticketed.
EXPLAIN_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.html$")

#: folder under docs/ -> the name grammar its HTML must follow.
HTML_HOMES = {
    PRESENTATIONS_DIR: PRESENTATION_NAME_RE,
    EXPLANATIONS_DIR: EXPLAIN_NAME_RE,
}

HTML_SUFFIX = ".html"


def _html_offenders(docs_dir: pathlib.Path = DOCS_DIR) -> list[str]:
    """POSIX paths, relative to `docs_dir`, of HTML in the wrong folder or name.

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
            name_re = HTML_HOMES.get(relative.parts[0])
            if name_re is None or not name_re.match(path.name):
                offenders.append(relative.as_posix())
    return offenders


def test_every_html_has_one_home_and_a_legal_name():
    offenders = _html_offenders()
    assert not offenders, (
        "HTML belongs to docs/pr-presentations/<pr>-<slug>.html or "
        "docs/explanations/<topic-slug>.html, and nowhere else under docs/:\n"
        + "\n".join(f"docs/{path}" for path in offenders)
    )


def test_a_stray_html_at_any_depth_is_caught(tmp_path):
    """Regression (#719): a subfolder under docs/ was a blind spot.

    The helper used to iterate `docs/` one level deep, so the two showcases in
    `docs/explanations/` and `docs/presentations/` never reached it. This plants
    legal files in both homes plus strays at the top, one folder down and two
    folders down, and pins the exact offender list.
    """
    docs = tmp_path / "docs"
    (docs / PRESENTATIONS_DIR).mkdir(parents=True)
    (docs / EXPLANATIONS_DIR).mkdir(parents=True)
    (docs / "presentations" / "old").mkdir(parents=True)
    (docs / "issues" / "nested").mkdir(parents=True)
    (docs / "agents").mkdir(parents=True)

    for stray in (
        "123-showcase-top-level.html",
        "entry-gate-job.html",
        "issues/297-presentation-instance-lock.html",  # the retired home
        "presentations/old/695-showcase-retired-env.html",
        # Right folder, wrong name: the number is missing.
        f"{PRESENTATIONS_DIR}/one-naming-contract.html",
        # Right folder, wrong name: a digit reads as a PR number.
        f"{EXPLANATIONS_DIR}/471-statistical-go-gate.html",
        # Right folder, wrong shape.
        f"{EXPLANATIONS_DIR}/skew_and_taker_cross_hebrew.html",
    ):
        (docs / stray).write_text("<html></html>", encoding="utf-8")

    for kept in (
        f"{PRESENTATIONS_DIR}/780-one-naming-contract.html",
        f"{PRESENTATIONS_DIR}/297-presentation-instance-lock.html",  # legacy slug
        f"{EXPLANATIONS_DIR}/skew-and-taker-cross.html",
        f"{EXPLANATIONS_DIR}/entry-gate-job.html",
    ):
        (docs / kept).write_text("<html></html>", encoding="utf-8")

    # Markdown is not this guard's business.
    (docs / "backlog" / "i219-auth-timeout.md").parent.mkdir(parents=True)
    (docs / "backlog" / "i219-auth-timeout.md").write_text("note", encoding="utf-8")
    (docs / "agents" / "architecture.md").write_text("note", encoding="utf-8")

    assert _html_offenders(docs) == [
        "123-showcase-top-level.html",
        "entry-gate-job.html",
        f"{EXPLANATIONS_DIR}/471-statistical-go-gate.html",
        f"{EXPLANATIONS_DIR}/skew_and_taker_cross_hebrew.html",
        "issues/297-presentation-instance-lock.html",
        f"{PRESENTATIONS_DIR}/one-naming-contract.html",
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
    assert PRESENTATION_NAME_RE.match("780-one-naming-contract.html")
    assert PRESENTATION_NAME_RE.match("297-presentation-instance-lock.html")
    assert PRESENTATION_NAME_RE.match("297-showcase-instance-lock.html")
    assert not PRESENTATION_NAME_RE.match("one-naming-contract.html")
    assert not PRESENTATION_NAME_RE.match("presentation-297.html")
    assert not PRESENTATION_NAME_RE.match("297-slide_presentation.html")

    # Explain-mode names, which the ten orphaned explainers did not follow:
    # 471-statistical-go-gate.html read as ticketed, and
    # skew_and_taker_cross_hebrew.html used underscores.
    assert EXPLAIN_NAME_RE.match("skew-and-taker-cross.html")
    assert not EXPLAIN_NAME_RE.match("471-statistical-go-gate.html")
    assert not EXPLAIN_NAME_RE.match("skew_and_taker_cross_hebrew.html")
    assert not EXPLAIN_NAME_RE.match("entry-gate.html.bak")

    assert not (DOCS_DIR / "issues").exists(), "docs/issues/ is back; it has no owner"
