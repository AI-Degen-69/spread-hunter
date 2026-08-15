"""Keep the fleet running when nobody is watching.

On 2026-07-29 the fleet died of a ZeroDivisionError at 13:40 and nobody
noticed for three and a half hours. Everything downstream of that -- the
markout samples, the reward samples, the whole measurement the run exists to
produce -- was simply not collected. A strategy running on a process that
dies silently is worth nothing, however good the strategy is.

This owns both processes and restarts either one when it exits. It does NOT
survive a reboot: that needs Task Scheduler, and is a separate decision.

    set HUNTER_DB=run/fleet.db
    python -m strategy.supervisor
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
(ROOT / "logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "supervisor.log",
                                  encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("supervisor")

# How long a child must stay up before its next death stops counting as part
# of the same crash loop.
STABLE_SEC = 60.0
POLL_SEC = 2.0

CHILDREN = {
    "fleet": [sys.executable, "-m", "strategy.fleet"],
    # THE CANONICAL DASHBOARD (2026-08-12). `server.spread_dash` is the
    # Spread Hunter design migration wired to real fleet data -- it is now
    # THE dashboard, on the well-known port 8800. `server.fleet_dash` (the
    # prior dashboard) is demoted to 8801 and serves ONLY its market-scan
    # funnel (?view=scan); the fleet page there was removed as redundant.
    "dash": [sys.executable, "-m", "uvicorn", "server.spread_dash:app",
             "--host", "127.0.0.1", "--port", "8800", "--reload", "--reload-dir", "server"],
    "scan": [sys.executable, "-m", "uvicorn", "server.fleet_dash:app",
             "--host", "127.0.0.1", "--port", "8801", "--reload", "--reload-dir", "server"],
    # THE RANKER, which `fleet-start.ps1` used to start as an UNSUPERVISED
    # sibling. It died on 2026-08-03 at 17:08 and nothing restarted it, so
    # run/markets.json went 28.5 hours without a rewrite while the fleet
    # re-read it every cycle. The U6 universe is short-dated by construction:
    # half of it had settled, `visit` was returning "closed / not accepting
    # orders" for 10 of 20 markets, and the loop, the heartbeat and the sweep
    # line all read perfectly healthy at $0.00/day. That is the same silent
    # death this supervisor was written for; the ranker belongs under it.
    "rerank": [sys.executable, "-m", "scripts.rerank_loop"],
    # THE UNIVERSE WATCHER, an observer started 2026-08-08 to log the evening
    # slate without anyone sitting at the terminal: every 5 minutes it records
    # picked-market count, the latest ranker census, and live esports book
    # depth/spread against the selector bars (logs/universe_watch.log). It is
    # read-only -- it never writes run/markets.json or the DB -- so losing it
    # costs nothing but the log; keeping it supervised means the log simply
    # keeps accruing across crashes and reboots instead of silently stopping.
    # It was first started detached; the supervisor owns it so the stack and
    # the observer cannot drift apart.
    "watch": [sys.executable, "-m", "scripts.watch_universe"],
}


def next_restart_delay(consecutive_crashes: int) -> float:
    """Seconds to wait before restarting a child.

    Flat 5s for an isolated crash -- that is the common case, and the fleet
    should be back before the next sweep. Doubling past that so a child that
    dies on startup (a bad markets.json, a port already bound) does not spin
    the CPU rewriting the same traceback forever. Capped at a minute so an
    outage that lasted hours is still recovered from quickly.
    """
    if consecutive_crashes <= 1:
        return 5.0
    return min(60.0, 5.0 * (2 ** (consecutive_crashes - 1)))


class Child:
    def __init__(self, name: str, cmd: list[str]):
        self.name = name
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None
        self.crashes = 0
        self.started = 0.0
        self.restart_at = 0.0

    def start(self) -> None:
        # stdout/stderr are inherited: each child already writes its own log
        # file, and inheriting means a traceback still reaches the console the
        # supervisor runs in instead of vanishing into a pipe nobody reads.
        self.proc = subprocess.Popen(self.cmd, cwd=str(ROOT))
        self.started = time.time()
        self.restart_at = 0.0
        log.info("started %s (pid %d)", self.name, self.proc.pid)

    def check(self, now: float) -> None:
        if self.proc is None:
            if now >= self.restart_at:
                self.start()
            return
        code = self.proc.poll()
        if code is None:
            # Alive. A child that has been up a while is not in a crash loop.
            if self.crashes and (now - self.started) >= STABLE_SEC:
                log.info("%s stable, clearing crash count", self.name)
                self.crashes = 0
            return
        self.crashes += 1
        delay = next_restart_delay(self.crashes)
        log.error("%s EXITED code=%s after %.0fs (crash #%d) -- restarting "
                  "in %.0fs", self.name, code, now - self.started,
                  self.crashes, delay)
        self.proc = None
        self.restart_at = now + delay

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        log.info("stopped %s", self.name)


def main() -> None:
    if not os.environ.get("HUNTER_DB"):
        raise SystemExit("HUNTER_DB is not set -- the children would write to a "
                         "different database than the one you are reading. "
                         "Set it (e.g. run/fleet.db) and try again.")
    children = [Child(n, c) for n, c in CHILDREN.items()]
    for ch in children:
        ch.start()
    log.info("supervising %d children | HUNTER_DB=%s",
             len(children), os.environ["HUNTER_DB"])
    try:
        while True:
            now = time.time()
            for ch in children:
                ch.check(now)
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log.info("interrupted -- stopping children")
    finally:
        for ch in children:
            ch.stop()


if __name__ == "__main__":
    main()
