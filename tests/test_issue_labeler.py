"""The triage labeler must be shippable, not merely present.

`github/issue-labeler` reads three files and fails the job on the first thing it
cannot do, so each of the draft's mistakes would have surfaced as a red check on
somebody's issue instead of here:

- a rule keyed `critical` when the tracker has no such label (the action cannot
  create one -- it rethrows the 404);
- a bare regex, which the action passes straight to `String.match` and is
  therefore case-sensitive, so "Crash" never matches `crash`;
- `enable-versioned-regex: 1` with `body-missing-regex-label`, which labels
  every issue that lacks the `issue_labeler_regex_version=N` marker, using a
  label the tracker does not have either;
- a labels-sync that watches `.github/labels.yml` while the file shipped as
  `labels.yaml`, so it could never fire and would fail if dispatched.

The mail was cross-checked against the action's source (`src/main.ts` at
v3.5): `/pattern/flags` is the only form that carries flags, and several
patterns under one label are ANDed. No YAML parser is used -- `PyYAML` is not a
dependency of this project, and a test that quietly needs one would fail in CI.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github"
LABELER_RULES = GITHUB / "labeler.yml"
LABELS = GITHUB / "labels.yaml"
WORKFLOW = GITHUB / "workflows" / "labeler.yml"
SYNC_WORKFLOW = GITHUB / "workflows" / "labels-sync.yaml"

# `label:` at column 0, then `  - '<pattern>'` items.
_RULE = re.compile(r"^([A-Za-z][\w-]*):\s*$")
_ITEM = re.compile(r"^\s*-\s*'(.*)'\s*$")
# The action's own splitter: /(.+)/(.*)
_PATTERN = re.compile(r"^/(.+)/(.*)$")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _rules() -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    current: str | None = None
    for line in _read(LABELER_RULES).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        label = _RULE.match(line)
        if label:
            current = label.group(1)
            rules[current] = []
            continue
        item = _ITEM.match(line)
        if item and current is not None:
            rules[current].append(item.group(1))
    return rules


def _declared_labels() -> set[str]:
    names: set[str] = set()
    for line in _read(LABELS).splitlines():
        match = re.match(r'^- name:\s*"?(.*?)"?\s*$', line)
        if match:
            names.add(match.group(1))
    return names


def _workflow() -> str:
    return _read(WORKFLOW)


def test_the_labeler_files_exist():
    for path in (LABELER_RULES, LABELS, WORKFLOW, SYNC_WORKFLOW):
        assert path.is_file(), f"missing {path.relative_to(ROOT)}"


def test_the_labeler_only_runs_for_issues():
    """A PR trigger would mint red checks: fork PR tokens cannot write labels."""
    body = _workflow()
    on_block = body.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "issues:" in on_block
    assert "pull_request" not in on_block
    assert "issues: write" in body


def test_the_labeler_action_is_pinned_and_versioning_is_off():
    body = _workflow()
    assert "github/issue-labeler@v3.5" in body
    # Required by the action even when unused; `1` enables the
    # issue_labeler_regex_version machinery this repo does not use.
    assert re.search(r"enable-versioned-regex:\s*0\b", body)
    # The names are mentioned in the file's own comment; what matters is that
    # neither is passed as an input.
    for unwanted in ("body-missing-regex-label", "not-before", "versioned-regex:"):
        assert not re.search(rf"^\s*{re.escape(unwanted)}", body, re.M), (
            f"{unwanted} must not be passed to the action"
        )


def test_every_rule_names_a_label_the_tracker_declares():
    """The action rethrows on an unknown label, so a typo is a failed triage run."""
    rules = _rules()
    assert rules, "no rules parsed -- the file's shape changed"
    declared = _declared_labels()
    assert declared, "no labels parsed from labels.yaml"
    unknown = sorted(set(rules) - declared)
    assert not unknown, f"rules for labels that do not exist: {unknown}"


def test_every_pattern_carries_its_case_insensitive_flag():
    """A bare string goes to `String.match` and matches case-sensitively."""
    for label, patterns in _rules().items():
        assert patterns, f"{label} has no pattern"
        for pattern in patterns:
            match = _PATTERN.match(pattern)
            assert match, (
                f"{label}: {pattern!r} is not in `/pattern/flags` form, so its "
                "case-sensitivity cannot be set"
            )
            assert "i" in match.group(2), (
                f"{label}: {pattern!r} has no `i` flag -- `Crash` would not match"
            )


def test_the_sync_watches_the_file_it_reads():
    body = _read(SYNC_WORKFLOW)
    assert "paths:" in body
    assert ".github/labels.yaml" in body
    assert re.search(r"yaml-file:\s*\.github/labels\.yaml", body)
    assert ".github/labels.yml" not in body, "the .yml spelling is the upstream default, not this file"
    assert "skip-delete: true" in body, "a sync must never prune a label made in the UI"
    assert "crazy-max/ghaction-github-labeler@v6" in body


def test_every_declared_label_carries_a_six_digit_colour():
    colours = []
    for line in _read(LABELS).splitlines():
        match = re.match(r'^\s{2}color:\s*"?([^"]*)"?\s*$', line)
        if match:
            colours.append(match.group(1))
    assert colours, "no colours parsed"
    bad = [c for c in colours if not re.fullmatch(r"[0-9a-fA-F]{6}", c)]
    assert not bad, f"colours must be six hex digits, no '#' (got {bad})"
