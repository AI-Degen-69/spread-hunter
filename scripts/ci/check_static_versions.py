"""Gate cache-busting versions on dashboard static assets.

``dashboard/static/*.js`` and ``*.css`` are served cacheable; the ``?v=N``
query on each ``<script>``/``<link>`` tag is what makes browsers fetch the
new copy. A static change without its ``?v=`` bump ships invisibly: the
author sees it (hard refresh), everyone else keeps the old file.

This check compares the ``?v=`` each changed asset carries at ``--base``
vs ``--head`` and fails when a changed asset is unversioned or its version
did not move. HTML files are exempt (served ``no-cache``). An asset whose
reference was removed needs no bump (nothing serves it anymore).

Usage (CI runs under ``shell: bash`` on Windows)::

    python scripts/ci/check_static_versions.py --base origin/main --head HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = REPO_ROOT / "dashboard" / "static"
VERSIONED_SUFFIXES = (".js", ".css")

_REF_RE = re.compile(r"/static/([A-Za-z0-9_.\-]+)(?:\?v=(\d+))?")


def asset_versions(html: str) -> dict[str, int | None]:
    """Every ``/static/`` asset referenced, mapped to its ``?v=`` or None."""
    found: dict[str, int | None] = {}
    for match in _REF_RE.finditer(html or ""):
        name, version = match.group(1), match.group(2)
        found[name] = int(version) if version is not None else None
    return found


def _bare_ref(html: str | None, asset: str) -> bool:
    """Whether ``/static/<asset>`` appears at all, versioned or not."""
    return re.search(r"/static/" + re.escape(asset) + r"(?![A-Za-z0-9_.\-])",
                     html or "") is not None


def check_changes(changed_assets: list[str],
                  base_html: dict[str, str],
                  head_html: dict[str, str]) -> list[str]:
    """Violation messages for changed assets whose version did not move.

    ``base_html``/``head_html`` map HTML filename to file content. Empty
    means the change is safe (or uncheckable, which passes).
    """
    violations: list[str] = []
    for asset in changed_assets:
        old_version: int | None = None
        new_version: int | None = None
        bare_at_head = False
        for name in sorted(set(base_html) | set(head_html)):
            old = asset_versions(base_html.get(name)).get(asset)
            new = asset_versions(head_html.get(name)).get(asset)
            if old is not None:
                old_version = old if old_version is None else max(old_version, old)
            if new is not None:
                new_version = new if new_version is None else max(new_version, new)
            if _bare_ref(head_html.get(name), asset):
                bare_at_head = True
        if new_version is not None and new_version != old_version:
            continue
        if new_version is None and not bare_at_head:
            continue
        if new_version is None:
            violations.append(
                f"dashboard/static/{asset} changed but is referenced without "
                f"?v= versioning; add ?v=N to its <script>/<link> tag.")
        else:
            violations.append(
                f"dashboard/static/{asset} changed but its ?v={new_version} "
                f"did not move; bump it so browsers fetch the new copy.")
    return violations


def _git(args: list[str]) -> str:
    # UTF-8 with replacement: this repo's history holds mojibake bytes the
    # Windows locale codec cannot decode, and a gate must never die on them.
    done = subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=str(REPO_ROOT))
    if done.returncode != 0:
        raise OSError(done.stderr.strip())
    return done.stdout


def _content_at(ref: str, path: str) -> str:
    try:
        return _git(["show", f"{ref}:./{path}"])
    except (OSError, ValueError):
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    try:
        names = _git(["diff", "--name-only", "--diff-filter=ACMR",
                      f"{args.base}...{args.head}", "--",
                      "dashboard/static"]).split()
    except (OSError, ValueError) as exc:
        print(f"check_static_versions: cannot diff "
              f"{args.base}...{args.head} ({exc}); passing.")
        return 0

    assets = sorted(
        Path(n).name for n in names
        if Path(n).parent.as_posix() == "dashboard/static"
        and Path(n).suffix.lower() in VERSIONED_SUFFIXES
    )
    # Always read every HTML file: the bump may live in one the diff missed
    # (unchanged since base) while the asset changed.
    html_names = sorted(
        {p.name for p in STATIC_DIR.glob("*.html")}
        | {Path(n).name for n in names
           if Path(n).parent.as_posix() == "dashboard/static"
           and Path(n).suffix.lower() == ".html"}
    )

    base_html = {n: _content_at(args.base, f"dashboard/static/{n}") for n in html_names}
    head_html = {n: _content_at(args.head, f"dashboard/static/{n}") for n in html_names}

    violations = check_changes(assets, base_html, head_html)
    for violation in violations:
        print(f"check_static_versions: FAIL: {violation}", file=sys.stderr)
    if violations:
        return 1
    print(f"check_static_versions: ok ({len(assets)} changed asset(s) checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
