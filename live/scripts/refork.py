"""Re-record live/FORKED_FROM.json after reviewing a root-side change.

`live/tests/test_fork_drift.py` fails when a simulation module that live/ forked
has moved at the repo root. This script clears that failure -- but only after a
human has read the diff and decided what the live copy should do, which is why
it does not copy anything by default.

    python live/scripts/refork.py                    # re-record hashes only
    python live/scripts/refork.py --pull config.py   # also overwrite the live copy

`--pull` is the "yes, take the root version wholesale" path. Without it the live
file is left exactly as it is and only the recorded hash advances, which is the
"reviewed it, live keeps its own version" path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent
MANIFEST = LIVE_ROOT / "FORKED_FROM.json"


def normalised_sha256(path: Path) -> str:
    """Hash with CRLF folded to LF -- must match test_fork_drift._normalised_sha256."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pull", action="append", default=[], metavar="NAME",
                    help="overwrite live/engine/NAME with the root copy before re-recording")
    args = ap.parse_args(argv)

    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True).stdout.strip()

    pulls = {p.lstrip("/").split("/")[-1] for p in args.pull}
    unknown = pulls - {Path(k).name for k in doc["forks"]}
    if unknown:
        print(f"not a recorded fork: {sorted(unknown)}", file=sys.stderr)
        return 2

    for live_rel, entry in doc["forks"].items():
        source = REPO_ROOT / entry["forked_from"]
        if not source.is_file():
            print(f"MISSING  {entry['forked_from']} -- remove it from the manifest by hand")
            continue

        if Path(live_rel).name in pulls:
            shutil.copyfile(source, REPO_ROOT / live_rel)
            print(f"PULLED   {entry['forked_from']} -> {live_rel}")

        new_sha = normalised_sha256(source)
        if new_sha == entry["source_sha256"]:
            print(f"UNCHANGED {entry['forked_from']}")
            continue
        entry["source_sha256"] = new_sha
        entry["at_commit"] = head
        print(f"RECORDED {entry['forked_from']} at {head[:8]}")

    MANIFEST.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
