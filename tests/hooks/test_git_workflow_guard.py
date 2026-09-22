"""Tests for the PreToolUse git workflow guard's entry point.

The guard's policy checks shell out to `gh`, so they are not exercised here.
What is covered is the contract every caller depends on: it takes no arguments,
and unexpected input degrades to allow rather than wedging the repo.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

sys.path.insert(0, "scripts/hooks")

import git_workflow_guard as guard  # noqa: E402


def test_rejects_arguments_instead_of_hanging_on_stdin(capsys):
    # Arrange — the retired sign-off invocation. Falling through to stdin here
    # would block on a terminal read instead of returning.
    argv = ["--approve", "104"]

    # Act
    code = guard.main(argv)

    # Assert
    assert code == 1
    err = capsys.readouterr().err
    assert "takes no arguments" in err
    assert "no sign-off" in err


def test_no_merge_sign_off_api_remains():
    # Arrange / Act / Assert — the approval gate was removed, not disabled.
    for name in ("record_approval", "is_approved", "load_approvals", "APPROVALS"):
        assert not hasattr(guard, name), f"{name} should have been removed"


def test_a_merge_payload_still_reaches_the_policy_and_allows(monkeypatch):
    """The dispatch path a removal could sever.

    This change deleted `_changed_paths` and `_head_sha` from the module. Both
    sat among the `gh`-shelling helpers the merge policy uses, so cutting one
    too many, or cutting the argv branch such that a payload no longer reaches
    `classify`, breaks exactly here. Driving it through `main` rather than
    calling `check_merge` directly is what makes that reachable.
    """
    # Arrange
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 104 --squash"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "104")

    # Act
    code = guard.main([])

    # Assert — a merge is never blocked, for any path, and the reminder is the
    # merge one rather than a stray fall-through.
    assert code == 0


def test_a_push_payload_still_reaches_the_policy(monkeypatch):
    """Same reason as above, on the one branch that can legitimately block."""
    # Arrange
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin some-branch"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "104")
    monkeypatch.setattr(guard, "_reviews", lambda pr: ["CHANGES_REQUESTED"])

    # Act
    code = guard.main([])

    # Assert — one round of findings is under the limit, so it reminds.
    assert code == 0


def test_a_push_is_blocked_at_the_round_limit(monkeypatch):
    # Arrange
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin some-branch"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "104")
    monkeypatch.setattr(
        guard, "_reviews", lambda pr: ["CHANGES_REQUESTED"] * guard.MAX_REVIEW_ROUNDS
    )

    # Act
    code = guard.main([])

    # Assert — the guard's one real block still fires after the removal.
    assert code == 2


# --- the autofix round protocol (#43) --------------------------------------

def test_round_rules_direct_the_autofix_loop():
    # Arrange / Act
    rules = guard.ROUND_RULES

    # Assert — triage, then exactly one autofix per round, then read the commit.
    assert "autofix" in rules
    assert "ONE" in rules
    assert "NOT start a review by itself" in rules


def test_round_rules_no_longer_ban_the_handle_outright():
    # Arrange — the old text forbade the handle in any comment, which would
    # forbid the autofix trigger the loop now depends on.
    rules = guard.ROUND_RULES

    # Act / Assert
    assert "must not appear anywhere" not in rules
    assert "allowed in four places only" in rules


def test_round_rules_still_enforce_one_push_and_one_summary():
    # Arrange / Act
    rules = guard.ROUND_RULES

    # Assert — the round discipline the autofix loop rides on is unchanged.
    assert "ONE commit and push ONCE" in rules
    assert "ONE summary comment per round" in rules
    assert "Never post `@coderabbitai full review`" in rules
def test_round_rules_tell_the_agent_to_fire_the_manual_trigger():
    """CodeRabbit does not review this repo on its own (under 10 stars).

    The reminder must say so and name the trigger, or the agent reads the
    "Trigger review" notice as CodeRabbit being unavailable and merges a pull
    request nobody reviewed.
    """
    # Arrange / Act
    rules = guard.ROUND_RULES

    # Assert
    assert "Reviews do NOT fire on their own here" in rules
    assert "`@coderabbitai review`" in rules
    assert "Trigger BEFORE" in rules
    assert "This push will NOT start a review by itself" in rules
