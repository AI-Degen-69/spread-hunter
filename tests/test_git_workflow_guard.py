"""Tests for the PreToolUse guard over git and GitHub commands.

The guard exists because the rules it enforces sit in a file AGENTS.md links to
rather than imports, and a partial read of that file misses them. It has to hold
when `gh` is missing, unauthenticated, or slow, so every helper degrades to
"allow" rather than blocking the repo.

The merge sign-off / `--approve` tests that used to live here were deleted with
the feature itself (#42, PR #107): merges are autonomous now, and
`tests/hooks/test_git_workflow_guard.py` asserts that API stays absent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "git_workflow_guard.py"


def _invoke(payload: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=60, check=False,
    )


def _bash(command: str, tool_name: str = "Bash") -> subprocess.CompletedProcess:
    return _invoke({"tool_name": tool_name, "tool_input": {"command": command}})


@pytest.fixture
def guard():
    """Import the guard module so its shell-outs can be stubbed."""
    sys.path.insert(0, str(GUARD.parent))
    import git_workflow_guard as mod
    yield mod
    sys.path.remove(str(GUARD.parent))


class TestClassify:
    """Routing has to survive global options and compound command lines.

    `git -C /repo push` and `gh --repo owner/name pr merge 15` are ordinary
    forms, and a guard that ignores them is a guard anyone steps around by
    accident.
    """

    @pytest.mark.parametrize("command", [
        "git push",
        "git push -u origin main",
        "git -C /repo push",
        "git -c user.name=x push",
        "git --git-dir=/repo/.git push",
        "cd /repo && git push",
        "git status; git push",
    ])
    def test_push_forms_are_recognised(self, guard, command):
        assert guard.classify(command)[0] == "push"

    @pytest.mark.parametrize("command,expected_pr,expected_repo", [
        ("gh pr merge 15 --merge", "15", None),
        ("gh pr merge --merge", None, None),
        ("gh --repo owner/name pr merge 15", "15", "owner/name"),
        ("gh -R owner/name pr merge 16 --squash", "16", "owner/name"),
        ("gh pr merge 18 --repo=owner/other", "18", "owner/other"),
        ("cd /repo && gh pr merge 17", "17", None),
    ])
    def test_merge_forms_are_recognised(self, guard, command, expected_pr,
                                       expected_repo):
        # The repository is carried, not dropped: a merge aimed at another
        # repository must not be checked against the one in the working tree.
        assert guard.classify(command) == ("merge", expected_pr, expected_repo)

    def test_merge_wins_over_push_on_one_line(self, guard):
        """Merge is the irreversible end of the list, so it takes precedence."""
        assert guard.classify("git push && gh pr merge 15")[0] == "merge"

    @pytest.mark.parametrize("command", [
        "ls -la",
        "git log --grep=push",
        "git status",
        "echo gh pr merge",
        "",
    ])
    def test_unrelated_commands_route_nowhere(self, guard, command):
        assert guard.classify(command)[0] == ""


class TestPayloadShapes:
    """Valid JSON of the wrong shape must allow, not crash with exit 1."""

    @pytest.mark.parametrize("payload", [
        [],
        [1, 2, 3],
        "a string",
        42,
        None,
        {"tool_name": "Bash", "tool_input": []},
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": {"command": 5}},
        {"tool_name": "Bash", "tool_input": {"command": "   "}},
        {"tool_name": "Bash"},
        {},
    ])
    def test_unexpected_shapes_allow(self, payload):
        res = _invoke(payload)
        assert res.returncode == 0, res.stderr
        assert res.stdout == ""

    def test_malformed_json_allows(self):
        res = subprocess.run(
            [sys.executable, str(GUARD)], input="not json",
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert res.returncode == 0

    def test_non_bash_tool_is_ignored(self):
        res = _bash("git push", tool_name="Read")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_unrelated_command_is_ignored(self):
        res = _bash("ls -la")
        assert res.returncode == 0
        assert res.stdout == ""

    def test_pr_create_surfaces_the_placeholder_rule(self):
        res = _bash("gh pr create --title x")
        assert res.returncode == 0
        assert "@coderabbitai summary" in res.stdout


class TestPush:
    def test_push_without_an_open_pr_is_silent(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: None)
        assert guard.check_push() == (0, "")

    def test_push_on_an_early_round_reminds_but_allows(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_reviews", lambda pr: ["CHANGES_REQUESTED"])
        code, message = guard.check_push()
        assert code == 0
        assert "ONE commit and push ONCE" in message

    def test_push_at_the_third_review_blocks(self, guard, monkeypatch):
        """Three rounds is the runaway guard: a fourth must not just happen."""
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "15")
        monkeypatch.setattr(guard, "_reviews",
                            lambda pr: ["CHANGES_REQUESTED"] * 3)
        code, message = guard.check_push()
        assert code == 2
        assert "BLOCKED" in message
        assert "fourth" in message

    def test_an_approval_is_not_a_runaway_round(self, guard, monkeypatch):
        """Counting an approval toward the threshold blocks the wrong push.

        It stops the commit that lands the fixes the review asked for, which
        is the opposite of what the stop condition exists to prevent.
        """
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "CHANGES_REQUESTED", "CHANGES_REQUESTED", "APPROVED",
        ])
        assert guard.check_push()[0] == 0

    def test_an_approval_followed_by_more_findings_still_blocks(self, guard,
                                                                monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "APPROVED", "CHANGES_REQUESTED", "CHANGES_REQUESTED",
            "CHANGES_REQUESTED",
        ])
        assert guard.check_push()[0] == 2

    def test_a_trailing_approval_does_not_hide_the_rounds(self, guard, monkeypatch):
        """The real pattern on PR #16: every round ended in an approval.

        Exempting "latest review is an approval" meant the block could never
        fire, because the reviewer posts CHANGES_REQUESTED and then APPROVED
        for the same commit. Five rounds ran with the guard installed.
        """
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_reviews", lambda pr: [
            "CHANGES_REQUESTED",
            "CHANGES_REQUESTED", "APPROVED",
            "CHANGES_REQUESTED", "APPROVED",
        ])
        code, message = guard.check_push()
        assert code == 2
        assert "3 rounds" in message

    def test_only_the_review_bot_counts(self, guard, monkeypatch):
        """A person requesting changes is not an automatic review round."""
        page = [
            {"user": {"login": guard.REVIEW_BOT}, "state": "CHANGES_REQUESTED"},
            {"user": {"login": "a-human"}, "state": "CHANGES_REQUESTED"},
            {"user": {"login": guard.REVIEW_BOT}, "state": "APPROVED"},
        ]
        monkeypatch.setattr(guard, "_run", lambda args: json.dumps([page]))
        assert guard._reviews("16") == ["CHANGES_REQUESTED", "APPROVED"]

    def test_every_page_of_reviews_is_read(self, guard, monkeypatch):
        """The endpoint returns 30 reviews a page.

        A pull request past that count is exactly the runaway this guard is
        for, so reading one page would undercount it into silence.
        """
        captured: list[list[str]] = []
        bot = {"user": {"login": guard.REVIEW_BOT}, "state": "CHANGES_REQUESTED"}
        pages = [[bot] * 30, [bot] * 5]

        def fake_run(args):
            captured.append(args)
            return json.dumps(pages)

        monkeypatch.setattr(guard, "_run", fake_run)
        assert len(guard._reviews("16")) == 35
        assert "--paginate" in captured[0]
        # Without --slurp, --paginate emits one JSON value per page rather than
        # a single array. The parse then fails and _reviews returns [], which
        # reads as no rounds and silently disables the block.
        assert "--slurp" in captured[0]
        # gh rejects --slurp alongside --jq, so the filtering is done in Python.
        assert "--jq" not in captured[0]

    @pytest.mark.parametrize("payload", [
        '{"not": "a list"}',
        '[{"no": "user"}]',
        '[[{"user": null, "state": "CHANGES_REQUESTED"}]]',
        '[[{"user": {"login": "coderabbitai[bot]"}, "state": 5}]]',
        '[["not a review object"]]',
        '["a page that is not a list"]',
    ])
    def test_unexpected_review_payloads_count_as_no_rounds(self, guard,
                                                           monkeypatch, payload):
        monkeypatch.setattr(guard, "_run", lambda args: payload)
        assert guard._reviews("16") == []

    def test_unreadable_review_states_do_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: "16")
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._reviews("16") == []
        assert guard.check_push()[0] == 0


class TestOutputDecoding:
    """Review bodies are arbitrary text, and the hook must survive all of it.

    `text=True` decodes with the locale encoding -- cp1252 on Windows. One emoji
    in a review body made the reader thread raise, left `stdout` as None, and
    killed the guard on `None.strip()`. Reading PR #844's real reviews hit
    exactly this.
    """

    def test_invalid_utf8_is_replaced_not_raised(self, guard):
        assert guard._decode(b"caf\xe9 \x9d") == "caf\ufffd \ufffd"

    def test_missing_output_decodes_to_an_empty_string(self, guard):
        assert guard._decode(None) == ""

    def test_a_review_body_with_an_emoji_survives(self, guard, monkeypatch):
        """Drive the real `_run`: stubbing it would skip the decode under test.

        `text=True` is what broke here, so the fake command returns raw bytes
        and the reader has to do its own decoding.
        """
        # The star is the character that matters: its UTF-8 bytes contain
        # 0x90, which cp1252 leaves undefined, so it is the one that used to
        # kill the reader thread. A plain emoji decodes to mojibake and would
        # have let the bug through.
        body = f"looks good \U0001f600 \u2b50"
        page = [{"id": 1, "user": {"login": guard.REVIEW_BOT},
                 "state": "CHANGES_REQUESTED", "commit_id": "abc",
                 "body": body}]
        # `ensure_ascii=False` matters: the default escapes the emoji into
        # `\ud83d\ude00`, which is plain ASCII and would decode fine under any
        # code page, so the test would pass without the fix it exists for.
        monkeypatch.setattr(
            guard.subprocess, "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args, 0,
                stdout=json.dumps([page], ensure_ascii=False).encode("utf-8")),
        )
        assert guard._review_records("844")[0]["id"] == 1
        # And the text itself arrives intact, not as cp1252 mojibake.
        assert "\u2b50" in guard._run(["gh"])


class TestMerge:
    def test_merge_of_a_pr_is_allowed_with_rules(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "dismiss_stale_reviews",
                            lambda pr, repo=None: {"dismissed": [], "failed": [],
                                                   "note": ""})
        code, message = guard.check_merge("15")
        assert code == 0
        assert "merge check" in message.lower()
        assert "Read the full diff" in message

    def test_merge_without_pr_is_allowed(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_open_pr_for_head", lambda: None)
        code, message = guard.check_merge(None)
        assert code == 0
        assert "Before merging" in message

    def test_a_dismissal_is_reported_in_the_merge_message(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "dismiss_stale_reviews",
                            lambda pr, repo=None: {"dismissed": [99], "failed": [],
                                                   "note": ""})
        code, message = guard.check_merge("15")
        assert code == 0
        assert "Dismissed stale CodeRabbit review(s) 99" in message
        assert "no open review thread" in message

    def test_the_merge_message_names_the_repository_it_checked(self, guard,
                                                              monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(
            guard, "dismiss_stale_reviews",
            lambda pr, repo=None: seen.append((pr, repo)) or {
                "dismissed": [], "failed": [], "note": ""})
        guard.check_merge("15", "owner/other")
        assert seen == [("15", "owner/other")]

    def test_a_held_dismissal_says_why_nothing_was_dismissed(self, guard,
                                                            monkeypatch):
        """Silence would read as 'nothing stale', which is a different fact."""
        note = "2 review thread(s) are still open, so no review was dismissed"
        monkeypatch.setattr(guard, "dismiss_stale_reviews",
                            lambda pr, repo=None: {"dismissed": [], "failed": [],
                                                   "note": note})
        code, message = guard.check_merge("15")
        assert code == 0
        assert note in message

    def test_a_failed_dismissal_names_the_manual_command(self, guard, monkeypatch):
        """A dismissal that cannot run must not become a silent deadlock."""
        monkeypatch.setattr(guard, "dismiss_stale_reviews",
                            lambda pr, repo=None: {"dismissed": [],
                                                   "failed": [99, 100],
                                                   "note": ""})
        code, message = guard.check_merge("15")
        assert code == 0
        assert "Could not dismiss" in message
        # Comma-space, not comma-space-mangling: this string is read by a person.
        assert "review(s) 99, 100" in message
        assert "pulls/15/reviews/<id>/dismissals" in message


class TestStaleReviewDismissal:
    """CodeRabbit can no longer supersede its own review here.

    It skips reviews on this OSS repository, so a `CHANGES_REQUESTED` from an
    earlier commit stays a merge blocker forever with nothing left to fix.
    PR #844 was merged only after that review was dismissed by hand.
    """

    HEAD = "bbbbbbb2"

    def _setup(self, guard, monkeypatch, reviews, ok=True, open_threads=0,
               head=HEAD):
        monkeypatch.setattr(guard, "_head_sha", lambda *args: head)
        monkeypatch.setattr(guard, "_review_records", lambda *args: reviews)
        monkeypatch.setattr(guard, "_unresolved_threads", lambda *args: open_threads)
        calls: list[list[str]] = []

        def fake_ok(args):
            calls.append(args)
            return ok

        monkeypatch.setattr(guard, "_ok", fake_ok)
        return calls

    def test_a_review_on_an_older_commit_is_dismissed(self, guard, monkeypatch):
        calls = self._setup(guard, monkeypatch, [
            {"id": 7, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ])

        result = guard.dismiss_stale_reviews("844")

        assert result["dismissed"] == [7]
        assert result["failed"] == []
        assert result["note"] == ""
        assert any(arg.endswith("pulls/844/reviews/7/dismissals") for arg in calls[0])
        assert "-X" in calls[0] and "PUT" in calls[0]
        # The message has to say why, so the PR timeline explains the dismissal.
        assert any("Superseded" in arg for arg in calls[0])

    def test_the_dismissal_addresses_the_named_repository(self, guard, monkeypatch):
        """`-R owner/other` must not be dismissed in the working tree's repo."""
        calls = self._setup(guard, monkeypatch, [
            {"id": 7, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ])

        guard.dismiss_stale_reviews("844", "owner/name")

        assert any("repos/owner/name/pulls/844/reviews/7/dismissals" == arg
                   for arg in calls[0]), calls[0]

    def test_an_open_thread_holds_every_dismissal(self, guard, monkeypatch):
        """An unanswered finding is not superseded by a new commit."""
        calls = self._setup(guard, monkeypatch, [
            {"id": 7, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ], open_threads=2)

        result = guard.dismiss_stale_reviews("844")

        assert result["dismissed"] == []
        assert calls == []
        assert "2 review thread(s) are still open" in result["note"]

    def test_unreadable_threads_hold_every_dismissal(self, guard, monkeypatch):
        """Unknown is not zero; a failed lookup must not read as 'all clear'."""
        calls = self._setup(guard, monkeypatch, [
            {"id": 7, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ], open_threads=None)

        result = guard.dismiss_stale_reviews("844")

        assert result["dismissed"] == []
        assert calls == []
        assert "Could not read the review threads" in result["note"]

    def test_an_unreadable_head_holds_every_dismissal(self, guard, monkeypatch):
        calls = self._setup(guard, monkeypatch, [
            {"id": 7, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ], head="")

        result = guard.dismiss_stale_reviews("844")

        assert result["dismissed"] == []
        assert calls == []
        assert "Could not read the head" in result["note"]

    def test_a_review_on_the_current_head_is_left_alone(self, guard, monkeypatch):
        """A live blocker is a live blocker. Fixing it is the answer."""
        calls = self._setup(guard, monkeypatch, [
            {"id": 8, "state": "CHANGES_REQUESTED", "commit_id": self.HEAD},
        ])

        assert guard.dismiss_stale_reviews("844")["dismissed"] == []
        assert calls == []

    def test_a_humans_review_is_never_dismissed(self, guard, monkeypatch):
        """_review_records is filtered to the bot, so a human record cannot
        reach the dismissal, but the stale filter must not widen that."""
        calls = self._setup(guard, monkeypatch, [])

        assert guard.dismiss_stale_reviews("844")["dismissed"] == []
        assert calls == []
        assert guard.REVIEW_BOT == "coderabbitai[bot]"

    def test_an_approved_review_on_an_old_commit_is_not_dismissed(
        self, guard, monkeypatch
    ):
        calls = self._setup(guard, monkeypatch, [
            {"id": 9, "state": "APPROVED", "commit_id": "aaaaaaa1"},
        ])

        assert guard.dismiss_stale_reviews("844")["dismissed"] == []
        assert calls == []

    @pytest.mark.parametrize("review", [
        {"id": None, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        {"id": 10, "state": "CHANGES_REQUESTED", "commit_id": None},
    ])
    def test_unprovable_staleness_is_not_dismissed(self, guard, monkeypatch,
                                                   review):
        """No id, no commit: nothing proves it is stale, so nothing is touched."""
        calls = self._setup(guard, monkeypatch, [review])

        assert guard.dismiss_stale_reviews("844")["dismissed"] == []
        assert calls == []

    def test_an_unknown_head_dismisses_nothing(self, guard, monkeypatch):
        """Without the head commit every review would look stale."""
        monkeypatch.setattr(guard, "_head_sha", lambda *args: "")
        monkeypatch.setattr(guard, "_review_records", lambda *args: [
            {"id": 11, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ])
        monkeypatch.setattr(guard, "_ok", lambda args: pytest.fail("dismissed"))

        assert guard.dismiss_stale_reviews("844")["dismissed"] == []

    def test_a_failed_dismissal_is_reported_not_raised(self, guard, monkeypatch):
        self._setup(guard, monkeypatch, [
            {"id": 12, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ], ok=False)

        result = guard.dismiss_stale_reviews("844")
        assert result["dismissed"] == []
        assert result["failed"] == [12]

    def test_every_stale_review_is_dismissed(self, guard, monkeypatch):
        calls = self._setup(guard, monkeypatch, [
            {"id": 1, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
            {"id": 2, "state": "APPROVED", "commit_id": "aaaaaaa1"},
            {"id": 3, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa2"},
        ])

        result = guard.dismiss_stale_reviews("844")

        assert result["dismissed"] == [1, 3]
        assert result["failed"] == []
        assert len(calls) == 2


class TestReviewThreads:
    """An open thread is the reviewer's finding still unanswered.

    Dismissing a blocking review while one is open would leave the merge
    message reading clean over a question nobody had answered.
    """

    def _payload(self, nodes, has_next=False):
        return json.dumps({"data": {"repository": {"pullRequest": {
            "reviewThreads": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": has_next}}}}}})

    def test_open_threads_are_counted(self, guard, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr(guard, "_repo_slug", lambda repo: "owner/name")
        monkeypatch.setattr(
            guard, "_run",
            lambda args: seen.append(args) or self._payload(
                [{"isResolved": True}, {"isResolved": False},
                 {"isResolved": False}]),
        )

        assert guard._unresolved_threads("844") == 2
        # GraphQL needs the real slug, which `gh api` does not substitute.
        assert "owner=owner" in seen[0] and "name=name" in seen[0]
        assert "number=844" in seen[0]

    def test_a_fully_resolved_pull_request_counts_zero(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_repo_slug", lambda repo: "owner/name")
        monkeypatch.setattr(guard, "_run",
                            lambda args: self._payload([{"isResolved": True}]))
        assert guard._unresolved_threads("844") == 0

    def test_a_truncated_page_is_unknown_not_zero(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_repo_slug", lambda repo: "owner/name")
        monkeypatch.setattr(
            guard, "_run",
            lambda args: self._payload([{"isResolved": True}], has_next=True),
        )
        assert guard._unresolved_threads("844") is None

    @pytest.mark.parametrize("raw", [
        "",
        "<html>login</html>",
        json.dumps({"errors": [{"message": "nope"}]}),
        json.dumps({"data": {"repository": None}}),
    ])
    def test_an_unreadable_answer_is_unknown_not_zero(self, guard, monkeypatch,
                                                     raw):
        monkeypatch.setattr(guard, "_repo_slug", lambda repo: "owner/name")
        monkeypatch.setattr(guard, "_run", lambda args: raw)
        assert guard._unresolved_threads("844") is None

    def test_no_repository_slug_means_unknown(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_repo_slug", lambda repo: "")
        monkeypatch.setattr(guard, "_run",
                            lambda args: pytest.fail("queried without a slug"))
        assert guard._unresolved_threads("844") is None

    def test_records_keep_the_commit_a_review_was_submitted_against(
        self, guard, monkeypatch
    ):
        page = [
            {"id": 5, "user": {"login": guard.REVIEW_BOT},
             "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
            {"id": 6, "user": {"login": "a-human"},
             "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ]
        monkeypatch.setattr(guard, "_run", lambda args: json.dumps([page]))

        assert guard._review_records("844") == [
            {"id": 5, "state": "CHANGES_REQUESTED", "commit_id": "aaaaaaa1"},
        ]
        # The round counter reads the same records, so it is unchanged.
        assert guard._reviews("844") == ["CHANGES_REQUESTED"]


class TestDegradesToAllow:
    def test_missing_gh_binary_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "")
        assert guard._open_pr_for_head() is None
        assert guard.check_push() == (0, "")

    def test_unparseable_gh_output_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "<html>login</html>")
        assert guard._open_pr_for_head() is None
        assert guard._reviews("15") == []
        # `_head_sha` went with the approval flow it served (#42, PR #107).

    def test_gh_returning_a_list_does_not_block(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "_run", lambda args: "[1, 2]")
        assert guard._open_pr_for_head() is None
