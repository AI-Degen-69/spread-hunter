"""The rerank loop's ranker invocation, including the staged gate trials.

`rerank_loop` regenerates run/markets.json on a fixed interval. When the depth
trial (U32) or volume trial (U36) is configured (env MAKER_DEPTH_TRIAL_USD /
MAKER_VOLUME_TRIAL_USD), the loop must pass the bar through so adopted
markets are tagged `trial_depth_usd` / `trial_volume_usd` and their markouts
become the decision evidence -- otherwise a configured trial would silently
never run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.rerank_loop import _rank_cmd  # noqa: E402


def test_rank_cmd_is_plain_when_no_trials_are_configured(monkeypatch):
    # Hermetic: fleet-start.ps1 exports exactly these vars for the live
    # fleet, so a test run launched from that shell must still see "plain".
    monkeypatch.delenv("MAKER_DEPTH_TRIAL_USD", raising=False)
    monkeypatch.delenv("MAKER_VOLUME_TRIAL_USD", raising=False)
    cmd = _rank_cmd(20)
    assert cmd == [sys.executable, "-m", "scripts.rank_markets",
                   "--top", "20"]


def test_rank_cmd_appends_configured_trials(monkeypatch):
    monkeypatch.setenv("MAKER_DEPTH_TRIAL_USD", "750")
    monkeypatch.setenv("MAKER_VOLUME_TRIAL_USD", "200000")
    cmd = _rank_cmd(20)
    assert cmd == [sys.executable, "-m", "scripts.rank_markets",
                   "--top", "20",
                   "--trial-depth", "750.0", "--trial-volume", "200000.0"]


def test_rank_cmd_keeps_top_override():
    cmd = _rank_cmd(5)
    assert "--top" in cmd and cmd[cmd.index("--top") + 1] == "5"
