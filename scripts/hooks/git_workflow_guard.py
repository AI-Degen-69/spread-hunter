"""PreToolUse guard for the git and GitHub rules in docs/agents/git-workflow.md.

Those rules govern actions that reach GitHub: a push starts a paid review round,
a merge lands on `main`.

The agent has full autonomy over branches, commits, pushes, and merges.
This guard serves to remind on round discipline (batching fixes into one push)
and protect against runaway review loops (blocking after 3 review rounds).

Contract: reads the PreToolUse JSON payload on stdin. Exit 0 allows the command
and prints any reminder to stdout; exit 2 blocks it and prints the reason to
stderr. Anything unexpected exits 0, because a broken guard must not wedge the
repo -- every shell-out and every parse degrades to allow.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# A fourth round of findings means stop, not grind.
MAX_REVIEW_ROUNDS = 3

# Only this account's reviews are the automatic ones the rule counts.
REVIEW_BOT = "coderabbitai[bot]"

DOC = "docs/agents/git-workflow.md"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Global options accepted before a subcommand. True when the option consumes
# the following token as its argument.
GLOBAL_OPTS = {
    "-C": True, "-c": True, "--git-dir": True, "--work-tree": True,
    "--namespace": True, "--exec-path": True, "--config-env": True,
    "-R": True, "--repo": True,
    "--no-pager": False, "--paginate": False, "--bare": False,
    "--literal-pathspecs": False,
}

ROUND_RULES = f"""\
Pushing to a branch with an open pull request starts another incremental review
once the review has been triggered. Before this push, from {DOC}:

  * Batch every accepted fix into ONE commit and push ONCE. Each push is its own
    incremental review, so four pushes cost four reviews for one round.
  * Post ONE summary comment per round: what changed, what you declined, why.
  * Do NOT invoke `@coderabbitai autofix` -- the agent applies accepted fixes
    and performs the verification itself (AGENTS.md rule 2).
  * Post `@coderabbitai resolve` as a SEPARATE comment.
  * The handle is allowed in four places only: the body summary line,
    `review`, `resolve` -- and nowhere else.
  * Reviews do NOT fire on their own here. When a pull request opens with the
    "fewer than 10 stars / Trigger review" notice, post `@coderabbitai review`
    as its own comment, wait 30 seconds, and read the reply. Trigger BEFORE
    merging -- a closed pull request refuses it.
  * This push will NOT start a review by itself. Fire the trigger again after
    it, and if the reply is `Review rate limited`, do the agent review instead
    of waiting.
  * Never post `@coderabbitai full review`. That is the expensive one.
"""

MERGE_RULES = f"""\
Before merging, from {DOC}:

  * Read the full diff -- `gh pr diff <n>` -- not just the checks.
  * Check what else is in the stack. A stacked merge can carry another pull
    request onto `main` with it.
  * Ensure CI is green and CodeRabbit review blockers are resolved.
  * A CodeRabbit `CHANGES_REQUESTED` on a commit the branch has since left is
    stale, not a finding. CodeRabbit skips reviews here now, so it can never
    supersede one; the guard dismisses those automatically at merge time.
    Review threads on the CURRENT head are live blockers -- fix or decline them.
  * Never dismiss a person's review. Only the automatic reviewer's superseded
    ones are dismissed, and only because they can no longer be re-reviewed.
  * Report concise status upon merge (e.g. `Merged PR #...`).
"""


# ---------------------------------------------------------------------------
# command parsing
# ---------------------------------------------------------------------------

def split_segments(command: str) -> list[str]:
    """Split a shell line on the operators that separate whole commands.

    `cd x && git push` has to route on its second segment rather than fail to
    match because the line does not begin with `git`.
    """
    return [seg for seg in re.split(r"&&|\|\||[;|\n]", command) if seg.strip()]


def tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=False)
    except ValueError:
        return segment.split()


def strip_global_opts(tokens: list[str]) -> list[str]:
    """Drop global options so `git -C /repo push` routes like `git push`."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            index += 2 if ("=" not in token and GLOBAL_OPTS.get(name, False)) else 1
            continue
        out.append(token)
        index += 1
    return out


def _program(token: str) -> str:
    name = Path(token.strip("\"'")).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def classify(command: str) -> tuple[str, str | None]:
    """Return the action a command performs, plus any pull request number.

    One of "merge", "push", "create" or "". Merge wins if a line somehow holds
    more than one, being the irreversible end of the list.
    """
    action = ""
    pr: str | None = None
    for segment in split_segments(command):
        words = strip_global_opts(tokenize(segment))
        if not words:
            continue
        program, rest = _program(words[0]), words[1:]
        if program == "git" and rest[:1] == ["push"]:
            action = action or "push"
        elif program == "gh" and rest[:2] == ["pr", "merge"]:
            action = "merge"
            pr = next((w for w in rest[2:] if w.isdigit()), None)
        elif program == "gh" and rest[:2] == ["pr", "create"]:
            action = action or "create"
    return action, pr


# ---------------------------------------------------------------------------
# github lookups -- every one degrades to allow
# ---------------------------------------------------------------------------

def _decode(data: bytes | None) -> str:
    """Decode command output as UTF-8 regardless of the console code page.

    `text=True` decodes with the locale encoding, which on Windows is cp1252.
    Review bodies are arbitrary user text: a single emoji or CJK character made
    the reader thread raise, `stdout` come back as None, and the guard die on
    `None.strip()` -- in a hook, that is a wedged repo. Invalid bytes are
    replaced rather than raised; a mangled character is harmless, a crash is
    not.
    """
    return (data or b"").decode("utf-8", errors="replace")


def _run(args: list[str]) -> str:
    """Return stdout, or an empty string if the command fails for any reason."""
    try:
        out = subprocess.run(args, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return _decode(out.stdout) if out.returncode == 0 else ""


def _pr_json(pr: str | None, fields: str) -> dict:
    args = ["gh", "pr", "view"] + ([pr] if pr else []) + ["--json", fields]
    raw = _run(args)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _open_pr_for_head() -> str | None:
    data = _pr_json(None, "number,state")
    if data.get("state") != "OPEN":
        return None
    number = data.get("number")
    return str(number) if number is not None else None


def _review_records(pr: str) -> list[dict]:
    """The automatic reviewer's reviews on a pull request, oldest first.

    Each record keeps the review id and the commit it was submitted against,
    which is what tells a live blocker from one its author can no longer
    refresh.
    """
    raw = _run(["gh", "api", "--paginate", "--slurp",
                f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews"])
    try:
        pages = json.loads(raw.strip() or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(pages, list):
        return []

    records: list[dict] = []
    for page in pages:
        for review in page if isinstance(page, list) else []:
            if not isinstance(review, dict):
                continue
            user = review.get("user")
            if not isinstance(user, dict) or user.get("login") != REVIEW_BOT:
                continue
            state = review.get("state")
            if isinstance(state, str):
                records.append({
                    "id": review.get("id"),
                    "state": state,
                    "commit_id": review.get("commit_id"),
                })
    return records


def _reviews(pr: str) -> list[str]:
    """States of the automatic reviews on a pull request, oldest first.

    Only the review bot's own reviews. A human review is not a round, and
    counting one would trip the runaway block on a pull request a person had
    merely commented on.

    `--paginate` matters here rather than being tidiness: the endpoint returns
    30 reviews a page, and a pull request past that count is precisely the
    runaway this guard exists to stop. Reading one page would undercount it
    into silence. `--slurp` gives a list of page-lists and cannot be combined
    with `--jq`, so the filtering happens below.
    """
    return [record["state"] for record in _review_records(pr)]


def _head_sha(pr: str) -> str:
    """The commit the branch points at now, or "" if that cannot be read."""
    return str(_pr_json(pr, "headRefOid").get("headRefOid") or "")


def _ok(args: list[str]) -> bool:
    """True only when the command genuinely succeeded.

    `_run` cannot answer this: it returns an empty string for both a failed
    call and a successful one with no body, and a dismissal that silently did
    not happen is the deadlock this module exists to end.
    """
    try:
        done = subprocess.run(args, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def dismiss_stale_reviews(pr: str) -> tuple[list[int], list[int]]:
    """Dismiss the automatic reviewer's changes-requested reviews on old commits.

    A review submitted against a commit the branch has since left is stale by
    definition: its findings were fixed in the commit that followed, or
    declined with a reason on the thread. CodeRabbit can no longer supersede
    such a review here -- it skips reviews on this OSS repository -- so the
    frozen `CHANGES_REQUESTED` blocks every merge attempt with nothing left to
    fix. PR #844 spent its whole merge on exactly that; dismissing by hand was
    the only way out.

    Three things are never touched: a human's review, a review on the current
    head (that is a live blocker, and fixing it is the answer), and anything
    whose staleness cannot be proven. Returns the dismissed review ids and the
    ids whose dismissal failed, so the caller can name what happened. A failed
    dismissal must still leave the merge allowed -- an expired token is not a
    reason to wedge the repo.
    """
    head = _head_sha(pr)
    if not head:
        return [], []

    stale = [
        record for record in _review_records(pr)
        if record["state"] == "CHANGES_REQUESTED"
        and record["id"] is not None
        and isinstance(record["commit_id"], str)
        and record["commit_id"] != head
    ]

    dismissed: list[int] = []
    failed: list[int] = []
    for record in stale:
        review_id = record["id"]
        message = (f"Superseded: submitted against {record['commit_id'][:7]}, "
                   f"the branch now points at {head[:7]}.")
        if _ok(["gh", "api", "-X", "PUT",
                f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews/{review_id}/dismissals",
                "-f", f"message={message}"]):
            dismissed.append(review_id)
        else:
            failed.append(review_id)
    return dismissed, failed


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def check_push() -> tuple[int, str]:
    pr = _open_pr_for_head()
    if pr is None:
        return 0, ""
    # Count rounds of findings, not review objects. The reviewer posts a
    # CHANGES_REQUESTED and then an APPROVED for the same commit, so counting
    # objects inflates the total, and exempting "latest is an approval" hides
    # it completely -- the last review is an approval every time, and the block
    # can never fire. Both mistakes shipped before this; see PR #16.
    rounds = _reviews(pr).count("CHANGES_REQUESTED")
    if rounds >= MAX_REVIEW_ROUNDS:
        return 2, (
            f"BLOCKED: pull request #{pr} has had {rounds} rounds of review "
            f"findings. A fourth means stop, not grind ({DOC}, "
            "'Stop condition').\n\n"
            "Decline the remaining minors in one summary comment and merge, or "
            "explain to the operator why another round is warranted and ask "
            "them to confirm this push.\n\n" + ROUND_RULES
        )
    return 0, (f"Pull request #{pr} has had {rounds} round(s) of review "
               f"findings.\n\n{ROUND_RULES}")


def check_merge(pr: str | None) -> tuple[int, str]:
    pr = pr or _open_pr_for_head()
    if pr is None:
        return 0, MERGE_RULES

    dismissed, failed = dismiss_stale_reviews(pr)
    stale_note = ""
    if dismissed:
        ids = ", ".join(str(i) for i in dismissed)
        stale_note += (
            f"\nDismissed stale CodeRabbit review(s) {ids}: each was submitted "
            "against a commit the branch has left, so it could never be "
            "superseded and nothing was left to fix.\n"
        )
    if failed:
        ids = " ,".join(str(i) for i in failed)
        stale_note += (
            f"\nCould not dismiss stale CodeRabbit review(s) {ids}. If the merge "
            "is still refused for them, dismiss by hand and say so:\n\n"
            f"  gh api -X PUT repos/{{owner}}/{{repo}}/pulls/{pr}/reviews/<id>/dismissals "
            "-f message=\"Superseded commit\"\n\n"
            "Never dismiss a person's review that way.\n"
        )
    return 0, f"Pull request #{pr} merge check:\n{stale_note}\n{MERGE_RULES}"



def _command_from_stdin() -> str | None:
    """The Bash command in a PreToolUse payload, or None for anything else.

    Valid JSON of an unexpected shape is still unexpected input, so every
    branch here returns None and the caller allows the command.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command.strip():
        return None
    return command


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        # This guard takes no arguments. Falling through to stdin would leave
        # anyone who typed the retired `--approve <pr>` staring at a hang, so
        # refuse loudly instead. There is no merge sign-off to record.
        print(f"git_workflow_guard.py takes no arguments (got {argv[0]!r}); "
              "it reads a PreToolUse payload on stdin. Merges need no sign-off "
              f"({DOC}).", file=sys.stderr)
        return 1

    command = _command_from_stdin()
    if command is None:
        return 0

    action, pr = classify(command)
    if action == "merge":
        code, message = check_merge(pr)
    elif action == "push":
        code, message = check_push()
    elif action == "create":
        code, message = 0, (
            "Opening a pull request: the title is the literal string "
            "`@coderabbitai` and the body must contain the line "
            f"`@coderabbitai summary`. Write the body file outside the repo. ({DOC})"
        )
    else:
        return 0

    if message:
        print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
