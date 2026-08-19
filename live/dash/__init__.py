"""The live-cycle dashboard.

Named `dash`, not `server`, because the repo root already has a `server`
package (the simulation fleet dashboards). Two regular packages sharing one
importable name resolve by whichever parent sits earlier on `sys.path` -- the
same class of ambiguity the `strategy`/`engine` rename removed from this tree
on 2026-08-18. A distinct name has no such failure mode.
"""
