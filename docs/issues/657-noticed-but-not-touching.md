# 657 — noticed but not touching (Station II, 2026-10-18)

| # | Noticed | Why out of scope | Owner | When | Status | Resolved-by |
|---|---|---|---|---|---|---|
| N1 | `market_stream.py:261,291-292` already sends differential `operation` frames; `live_marks.py` re-implements instead of sharing a helper | Cross-module helper is a wider refactor than #657's one-method change; duplication is deliberate and recorded | you | this-round-only | open | — |
| N2 | Venue acceptance of `operation` frames on the marks channel is [UNVERIFIED-in-repo] (issue cites a #648 probe with no repo record) | Needs a live wire probe, which the agent must not run; flagged in plan + PR instead | you | when-a-live-probe-runs | open | — |
