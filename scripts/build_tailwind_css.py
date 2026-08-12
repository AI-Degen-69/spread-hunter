"""Regenerate the dashboard's static Tailwind CSS (Session 52).

The spread dashboard used to load `https://cdn.tailwindcss.com` -- the ~350KB
Play runtime -- which blocked first paint, fetched from a third-party CDN on
every cold load, and made the page dependent on that CDN being reachable
(the "Tailwind CDN / Google Fonts as offline dependencies" audit item). This
build replaces the runtime with a pre-generated, minified stylesheet covering
exactly the classes `server/spread_dash_html.py` emits, shipped inside the
page as an inline constant (`server/_tailwind_css.py`).

WHEN TO RE-RUN
--------------
After ANY change that adds or removes a Tailwind utility class in
`server/spread_dash_html.py` -- a new `hover:...`, an arbitrary value, a new
`md:` variant, anything. The stylesheet is a snapshot of the template's class
set; a class added to the template but not to the stylesheet silently renders
unstyled. There is no runtime fallback anymore (that is the point).

HOW IT WORKS
------------
1. Writes a v4 input stylesheet whose only `@source` is spread_dash_html.py,
   so Tailwind scans the exact file that emits the classes.
2. Runs `npx @tailwindcss/cli` (pinned to the spread-hunter version).
3. Writes `server/_tailwind_css.py` as a raw-string constant `TAILWIND_CSS`.

Run from the repo root:  python -m scripts.build_tailwind_css
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "server" / "spread_dash_html.py"
OUT = ROOT / "server" / "_tailwind_css.py"
PIN = "4.1.17"          # matches spread-hunter/package.json's ^4.1.17


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found on PATH -- the build needs Node/{name} "
                         "(it runs the Tailwind v4 CLI, which is a build-time "
                         "dependency only; the generated CSS ships in the repo)")
    return found


def build() -> str:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # @tailwindcss/cli needs its `tailwindcss` peer installed in the same
        # tree -- bare `npx @tailwindcss/cli` cannot resolve it. A throwaway
        # npm install per build is a few seconds and touches nothing in the
        # repo (npm caches the packages, so repeat builds are fast).
        npm = _tool("npm")
        r = subprocess.run([npm, "init", "-y"], cwd=td, capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise SystemExit(f"npm init failed:\n{r.stderr}")
        r = subprocess.run(
            [npm, "i", "--no-audit", "--no-fund", "-D",
             f"tailwindcss@{PIN}", f"@tailwindcss/cli@{PIN}"],
            cwd=td, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"npm install failed:\n{r.stderr}")
        # Tailwind v4's @source expects forward slashes on every platform
        # (as_posix() yields them on Windows too) -- the old backslash
        # replacement corrupted the path on POSIX, so the build scanned
        # nothing and silently shipped an unstyled class set (coderabbit).
        src = SOURCE.as_posix()
        (td / "input.css").write_text(
            '@import "tailwindcss";\n'
            f'@source "{src}";\n', encoding="utf-8")
        out_css = td / "tailwind.css"
        npx = _tool("npx")
        r = subprocess.run(
            [npx, "--no-install", "tailwindcss", "-i",
             str(td / "input.css"), "-o", str(out_css), "--minify"],
            cwd=td, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"tailwind build failed:\n{r.stderr}")
        return out_css.read_text(encoding="utf-8")


def main() -> int:
    css = build()
    if '"""' in css:
        raise SystemExit("generated CSS contains triple quotes -- cannot "
                         "embed as a raw string constant")
    OUT.write_text(
        f'"""Generated asset -- do not edit by hand.\n\n'
        f'Regenerate with:  python -m scripts.build_tailwind_css\n'
        f'See scripts/build_tailwind_css.py for why this exists (Session 52:\n'
        f'the dashboard\'s Tailwind CDN runtime was replaced with a static,\n'
        f'minified stylesheet built from the classes spread_dash_html.py emits).\n"""\n\n'
        f'TAILWIND_CSS = r"""{css}"""\n', encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(css) / 1024:.1f} KB minified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
