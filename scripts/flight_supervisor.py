"""Keep the read-only shadow monitor stack healthy during an unattended window.

This supervisor starts credential-free shadow rehearsals, local dashboards, and
supporting readers. It never starts a live Trader. New children are held in a
Windows job object so a Task Scheduler restart cannot orphan them; pre-existing
processes are adopted read-only and never killed or duplicated implicitly.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import errno
import logging
import math
import os
import socket
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.publish_flight_snapshot import deploy_to_vercel, fetch_json, take_snapshot_and_render

RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_FILE = RUNTIME_DIR / "flight_supervisor.log"
LOCK_FILE = RUNTIME_DIR / "flight_supervisor.lock"
PYTHON_EXE = sys.executable
CHECK_INTERVAL_SEC = 15.0
INITIAL_PUBLISH_DELAY_SEC = 60.0
SNAPSHOT_INTERVAL_SEC = 2 * 60 * 60.0
LOOP_HEARTBEAT_STARTUP_GRACE_SEC = 300.0
LOOP_HEARTBEAT_STALE_MIN_SEC = 120.0
LOOP_FINISH_EXIT_GRACE_SEC = 60.0
PUBLISH_RETRY_BASE_SEC = 30.0
PUBLISH_RETRY_MAX_SEC = 15 * 60.0
CHILD_RETRY_BASE_SEC = 15.0
CHILD_RETRY_MAX_SEC = 15 * 60.0
DEFAULT_SUPERVISOR_HOURS = 100.08

INSTANCES = [
    {
        "id": "shadow-01",
        "port": 8801,
        "db": "data/01_shadow_12-09_00-58.db",
        "ring": "runtime/shadow-01.jsonl",
        "screener_args": ["-m", "scripts.filter_loop"],
        "loop_args": ["-m", "core_brain.shadow_run", "--minutes", "6000", "--db", "data/01_shadow_12-09_00-58.db", "--run-id", "shadow-01"],
        "observer_args": ["-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", "data/01_shadow_12-09_00-58.db", "--run-id", "shadow-01", "--data-dir", "data", "--interval", "5", "--max-hours", "100.08"],
        "watcher_args": ["-m", "scripts.global_stop_loss", "--db", "data/01_shadow_12-09_00-58.db", "--ring", "runtime/shadow-01.jsonl"],
    },
    {
        "id": "shadow-02",
        "port": 8802,
        "db": "data/02_shadow_exit-regime_23-09.db",
        "ring": "runtime/shadow-02.jsonl",
        # shadow-01 owns the shared graduated feed; don't run two screeners
        # concurrently writing runtime/markets.json.
        "screener_args": None,
        "loop_args": ["-m", "core_brain.shadow_run", "--minutes", "6000", "--db", "data/02_shadow_exit-regime_23-09.db", "--run-id", "shadow-02"],
        "observer_args": ["-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", "data/02_shadow_exit-regime_23-09.db", "--run-id", "shadow-02", "--data-dir", "data", "--interval", "5", "--max-hours", "100.08"],
        "watcher_args": ["-m", "scripts.global_stop_loss", "--db", "data/02_shadow_exit-regime_23-09.db", "--ring", "runtime/shadow-02.jsonl"],
    },
    {
        "id": "shadow-03",
        "port": 8803,
        "db": "data/03_shadow_24-09_00-04.db",
        "ring": "runtime/shadow-03.jsonl",
        "screener_args": ["-m", "scripts.filter_loop", "--trial-depth", "250", "--out-dir", "runtime/trials/shadow-03"],
        "loop_args": ["-m", "core_brain.shadow_run", "--minutes", "6000", "--db", "data/03_shadow_24-09_00-04.db", "--run-id", "shadow-03", "--markets-path", "runtime/trials/shadow-03/markets.json"],
        "observer_args": ["-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", "data/03_shadow_24-09_00-04.db", "--run-id", "shadow-03", "--data-dir", "data", "--interval", "5", "--max-hours", "100.08"],
        "watcher_args": ["-m", "scripts.global_stop_loss", "--db", "data/03_shadow_24-09_00-04.db", "--ring", "runtime/shadow-03.jsonl"],
    },
]

logger = logging.getLogger("flight_supervisor")


class ObservedProcess:
    """Read-only handle for a verified process launched outside this supervisor."""

    external = True

    def __init__(self, pid: int, started_at: float | None, heartbeat_path: Path | None = None):
        self.pid = int(pid)
        self.started_at = started_at
        self.heartbeat_path = heartbeat_path

    def poll(self) -> int | None:
        from dashboard.server import _is_pid_alive

        if _is_pid_alive(self.pid, self.started_at) is not False:
            return None
        if self.heartbeat_path is not None:
            try:
                hb = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
                if int(hb.get("pid", 0)) == self.pid and hb.get("finished") is True:
                    return 0
            except (OSError, ValueError, TypeError):
                pass
        return 1

    def terminate(self) -> None:
        raise RuntimeError("refusing to terminate a process not owned by this supervisor")

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("external process", timeout)
            time.sleep(0.1)

    def kill(self) -> None:
        raise RuntimeError("refusing to kill a process not owned by this supervisor")


def configure_logging() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(getattr(handler, "baseFilename", None) == str(LOG_FILE) for handler in root_logger.handlers):
        root_logger.addHandler(RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=4, encoding="utf-8"
        ))
    if not any(getattr(handler, "_flight_console", False) for handler in root_logger.handlers):
        console = logging.StreamHandler()
        console._flight_console = True  # type: ignore[attr-defined]
        root_logger.addHandler(console)


def acquire_instance_lock(path: Path = LOCK_FILE):
    """Hold a process lock for the supervisor lifetime; OS releases it on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b" ")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        stream.close()
        return None
    return stream


class SystemAwakeGuard:
    """Keep Windows awake while supervision is active, without forcing the display on."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self, *, windows: bool | None = None, api=None):
        self.windows = os.name == "nt" if windows is None else windows
        self.api = api
        self.active = False

    def start(self) -> None:
        if not self.windows:
            return
        if self.api is None:
            self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        set_state = self.api.SetThreadExecutionState
        try:
            set_state.argtypes = [ctypes.c_uint]
            set_state.restype = ctypes.c_uint
        except AttributeError:
            # Plain callables are useful for unit tests; ctypes functions need
            # the explicit signature above to avoid pointer-width ambiguity.
            pass
        result = set_state(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        try:
            self.api.SetThreadExecutionState(self.ES_CONTINUOUS)
        finally:
            self.active = False


class ChildProcessGroup:
    """On Windows, terminate every descendant when this supervisor exits."""

    def __init__(self) -> None:
        self._handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        if not ok:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, pid: int) -> None:
        if self._handle is None:
            return
        process = self._kernel32.OpenProcess(0x0001 | 0x0100, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class InstanceRunner:
    def __init__(self, cfg: dict[str, Any], child_group: ChildProcessGroup):
        self.cfg = cfg
        self.id = cfg["id"]
        self.port = cfg["port"]
        self.child_group = child_group
        self.procs: dict[str, subprocess.Popen | None] = {
            "dash": None, "screener": None, "loop": None,
            "observer": None, "watcher": None,
        }
        self.started_at: dict[str, float] = {}
        self.started_wall: dict[str, float] = {}
        self.retry_at: dict[str, float] = {}
        self.preflight_done = False
        self.preflight_blocked = False
        self.preflight_failures = 0
        self.preflight_retry_at = 0.0
        self.failures: dict[str, int] = {}
        self.exit_codes: dict[str, int | None] = {}
        self.finished = False
        self.attached_existing = False

    def _start_child(self, name: str, args: list[str]) -> subprocess.Popen:
        out_log = RUNTIME_DIR / f"supervisor_{self.id}_{name}.out.log"
        err_log = RUNTIME_DIR / f"supervisor_{self.id}_{name}.err.log"
        cmd = [PYTHON_EXE, *args]
        with out_log.open("a", encoding="utf-8") as f_out, err_log.open("a", encoding="utf-8") as f_err:
            proc = subprocess.Popen(
                cmd,
                stdout=f_out,
                stderr=f_err,
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        try:
            self.child_group.assign(proc.pid)
        except Exception:
            proc.terminate()
            proc.wait(timeout=10)
            raise
        self.started_at[name] = time.monotonic()
        self.started_wall[name] = time.time()
        logger.info("[%s] started %s (PID %s)", self.id, name, proc.pid)
        return proc

    def _schedule_retry(self, name: str, reason: str) -> None:
        failures = self.failures.get(name, 0) + 1
        self.failures[name] = failures
        delay = min(CHILD_RETRY_MAX_SEC, CHILD_RETRY_BASE_SEC * (2 ** min(failures - 1, 6)))
        self.retry_at[name] = time.monotonic() + delay
        logger.error("[%s] %s unavailable (%s); retry in %.0fs (failure %d)",
                     self.id, name, reason, delay, failures)

    def _loop_heartbeat(self, proc: subprocess.Popen) -> dict[str, Any] | None:
        """Read a heartbeat proven to belong to this launched loop process."""
        hb_path = RUNTIME_DIR / f"shadow_run_{self.id}.json"
        try:
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
            if not isinstance(hb, dict):
                return None
            pid = int(hb["pid"])
            heartbeat_ts = float(hb["heartbeat_ts"])
            started_at = float(hb["started_at"])
            interval = float(hb.get("interval") or 5.0)
            cycles = int(hb.get("cycle") or 0)
        except (OSError, ValueError, TypeError, KeyError):
            return None
        if (
            pid != proc.pid
            or hb.get("run_id") != self.id
            or not all(math.isfinite(n) for n in (heartbeat_ts, started_at, interval))
            or interval < 0
            or cycles < 0
            or started_at < self.started_wall.get("loop", 0.0) - 10.0
        ):
            return None
        return hb

    def _loop_heartbeat_stale(self, proc: subprocess.Popen) -> bool:
        """Detect a dead or wedged loop, including a missing heartbeat."""
        if time.monotonic() - self.started_at.get("loop", time.monotonic()) < LOOP_HEARTBEAT_STARTUP_GRACE_SEC:
            return False
        hb = self._loop_heartbeat(proc)
        if hb is None or hb.get("finished"):
            return hb is None
        heartbeat_ts = float(hb["heartbeat_ts"])
        started_at = float(hb["started_at"])
        interval = max(0.0, float(hb.get("interval") or 5.0))
        cycles = max(0, int(hb.get("cycle") or 0))
        cadence = max(interval, (heartbeat_ts - started_at) / cycles if cycles else interval)
        stale_after = max(LOOP_HEARTBEAT_STALE_MIN_SEC, 3.0 * cadence)
        return time.time() - heartbeat_ts > stale_after

    def _loop_finished_but_alive(self, proc: subprocess.Popen) -> bool:
        hb = self._loop_heartbeat(proc)
        if hb is None or not hb.get("finished"):
            return False
        try:
            return time.time() - float(hb["heartbeat_ts"]) > LOOP_FINISH_EXIT_GRACE_SEC
        except (TypeError, ValueError):
            return False

    def _preflight(self) -> None:
        """Adopt matching existing shadow processes or fail closed on conflicts."""
        from dashboard.server import _is_pid_alive, _process_start_time

        hb_path = RUNTIME_DIR / f"shadow_run_{self.id}.json"
        try:
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
            if not isinstance(hb, dict):
                hb = {}
        except (OSError, ValueError):
            hb = {}

        expected_db = (PROJECT_ROOT / self.cfg["db"]).resolve()
        hb_db = hb.get("db_path")
        try:
            heartbeat_db_matches = bool(hb_db) and Path(hb_db).resolve() == expected_db
        except (OSError, TypeError, ValueError):
            heartbeat_db_matches = False

        try:
            connection = socket.create_connection(("127.0.0.1", int(self.port)), timeout=0.25)
        except OSError as exc:
            if exc.errno != errno.ECONNREFUSED and getattr(exc, "winerror", None) != 10061:
                raise RuntimeError(
                    f"could not verify whether port {self.port} is free; refusing to start {self.id}"
                ) from exc
            connection = None

        if connection is not None:
            connection.close()
            status = fetch_json(f"http://127.0.0.1:{self.port}/api/system/status")
            if not isinstance(status, dict):
                raise RuntimeError(
                    f"port {self.port} is occupied but its dashboard status is unavailable"
                )
            try:
                reported_db = status.get("db_path")
                served_db = Path(reported_db)
                if not served_db.is_absolute():
                    served_db = PROJECT_ROOT / served_db
                dashboard_matches = (
                    served_db.resolve() == expected_db
                    and status.get("db_is_production") is False
                )
            except (OSError, TypeError, ValueError):
                dashboard_matches = False
            if not dashboard_matches:
                raise RuntimeError(
                    f"port {self.port} is occupied by a dashboard for another or unknown store"
                )

            services = status.get("services")
            dash_service = services.get("dash") if isinstance(services, dict) else None
            dash_pid = dash_service.get("pid") if isinstance(dash_service, dict) else None
            dash_started = dash_service.get("started_at") if isinstance(dash_service, dict) else None
            if dash_pid is not None:
                try:
                    dash_pid = int(dash_pid)
                    dash_started = float(dash_started)
                except (TypeError, ValueError):
                    raise RuntimeError(f"port {self.port} dashboard process identity is unavailable")
                if _is_pid_alive(dash_pid, dash_started) is not True:
                    raise RuntimeError(f"port {self.port} dashboard process identity could not be verified")
                self.procs["dash"] = ObservedProcess(dash_pid, dash_started)

            run_status = status.get("shadow_run")
            if not isinstance(run_status, dict):
                run_status = {}
            dashboard_run_active = run_status.get("running") is True
            heartbeat_run_id = hb.get("run_id")
            heartbeat_pid = hb.get("pid")
            try:
                heartbeat_pid = int(heartbeat_pid)
            except (TypeError, ValueError):
                heartbeat_pid = 0
            process_started = hb.get("process_started_at")
            try:
                process_started = float(process_started)
            except (TypeError, ValueError):
                process_started = _process_start_time(heartbeat_pid) if heartbeat_pid > 0 else None

            reported_run_id = run_status.get("run_id")
            reported_loop_pid = run_status.get("pid")
            try:
                reported_loop_pid = int(reported_loop_pid)
            except (TypeError, ValueError):
                reported_loop_pid = 0
            heartbeat_process_alive = (
                heartbeat_db_matches
                and heartbeat_pid > 0
                and _is_pid_alive(heartbeat_pid, process_started)
            )
            if run_status.get("running") is True and (
                not heartbeat_db_matches
                or heartbeat_run_id != self.id
                or reported_run_id != self.id
                or heartbeat_pid <= 0
                or reported_loop_pid != heartbeat_pid
            ):
                raise RuntimeError(
                    f"dashboard reports an active but unverifiable rehearsal on port {self.port}"
                )
            if heartbeat_process_alive is not False:
                if (
                    heartbeat_run_id != self.id
                    or reported_run_id != self.id
                    or reported_loop_pid != heartbeat_pid
                    or heartbeat_process_alive is not True
                ):
                    raise RuntimeError(
                        f"a live shadow process for this store cannot be verified as {self.id}; "
                        "refusing to start a duplicate writer"
                    )
                run_started = hb.get("started_at")
                heartbeat_ts = hb.get("heartbeat_ts")
                try:
                    run_started = float(run_started)
                    heartbeat_ts = float(heartbeat_ts)
                except (TypeError, ValueError):
                    raise RuntimeError(f"{self.id} rehearsal start/heartbeat time is unavailable")
                if (
                    not math.isfinite(run_started)
                    or not math.isfinite(heartbeat_ts)
                    or heartbeat_ts > time.time() + 30.0
                    or (
                        process_started is not None
                        and not -5.0 <= run_started - process_started <= 120.0
                    )
                ):
                    raise RuntimeError(
                        f"{self.id} heartbeat process identity is ambiguous; refusing duplicate loop"
                    )
                self.procs["loop"] = ObservedProcess(heartbeat_pid, process_started, hb_path)
                self.started_wall["loop"] = run_started
                self.started_at["loop"] = time.monotonic() - max(0.0, time.time() - run_started)
                self._adopt_session_children()
                self.attached_existing = True
                if hb.get("finished") is True:
                    self.finished = True
                elif time.time() - heartbeat_ts > LOOP_HEARTBEAT_STALE_MIN_SEC:
                    logger.warning(
                        "[%s] adopting live loop PID %s with stale heartbeat; refusing duplicate writer",
                        self.id, heartbeat_pid,
                    )
            elif run_status.get("running") is True:
                raise RuntimeError(
                    f"dashboard reports an active rehearsal but its process is unverifiable on port {self.port}"
                )
            elif heartbeat_db_matches:
                session_path = RUNTIME_DIR / f"shadow-session-{self.id}.json"
                try:
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    session = {}
                if isinstance(session, dict) and session.get("run_id") == self.id:
                    self._adopt_session_children()
                if any(self.procs[name] is not None for name in ("screener", "observer", "watcher")):
                    self.attached_existing = True
        elif heartbeat_db_matches:
            # Adopt a running menu/shell rehearsal even when its dashboard died;
            # restarting that loop against the same store would duplicate writes.
            try:
                loop_pid = int(hb.get("pid", 0))
                loop_started = hb.get("process_started_at")
                loop_started = float(loop_started) if loop_started is not None else _process_start_time(loop_pid)
                run_started = float(hb.get("started_at"))
            except (TypeError, ValueError):
                loop_pid = 0
                loop_started = None
                run_started = 0.0
            process_alive = loop_pid > 0 and _is_pid_alive(loop_pid, loop_started)
            if process_alive is not False:
                heartbeat_ts = hb.get("heartbeat_ts")
                try:
                    heartbeat_ts = float(heartbeat_ts)
                except (TypeError, ValueError):
                    heartbeat_ts = 0.0
                if (
                    hb.get("run_id") != self.id
                    or not math.isfinite(run_started)
                    or not math.isfinite(heartbeat_ts)
                    or process_alive is not True
                    or (
                        loop_started is not None
                        and not -5.0 <= run_started - loop_started <= 120.0
                    )
                ):
                    raise RuntimeError(
                        f"{self.id} loop process identity is ambiguous; refusing duplicate"
                    )
                self.procs["loop"] = ObservedProcess(loop_pid, loop_started, hb_path)
                self.started_wall["loop"] = run_started
                self.started_at["loop"] = time.monotonic() - max(0.0, time.time() - run_started)
                self._adopt_session_children()
                self.attached_existing = True
                if hb.get("finished") is True:
                    self.finished = True


        if not self.attached_existing:
            session_path = RUNTIME_DIR / f"shadow-session-{self.id}.json"
            try:
                session = json.loads(session_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session = {}

        self.preflight_done = True
        self.preflight_failures = 0
        self.preflight_retry_at = 0.0

    def _adopt_session_children(self) -> None:
        """Adopt existing menu-launched processes only after store and PID checks."""
        from dashboard.server import _is_pid_alive, _process_start_time

        session_path = RUNTIME_DIR / f"shadow-session-{self.id}.json"
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(session, dict) or session.get("run_id") != self.id:
            return
        session_db = session.get("shadow_db") or session.get("ShadowDbPath")
        expected_db = (PROJECT_ROOT / self.cfg["db"]).resolve()
        try:
            session_matches = bool(session_db) and Path(session_db).resolve() == expected_db
        except (OSError, TypeError, ValueError):
            session_matches = False
        if not session_matches:
            raise RuntimeError(
                f"{self.id} session store does not match the configured shadow database"
            )

        records = [(name, session.get(name)) for name in ("loop", "screener", "observer", "watcher")]
        for name, record in records:
            if not isinstance(record, dict) or record.get("pid") is None:
                continue
            try:
                pid = int(record["pid"])
                recorded_ticks = int(record["started_ticks"])
            except (TypeError, ValueError, KeyError):
                raise RuntimeError(
                    f"{self.id} session has an incomplete {name} process identity; refusing duplicate"
                )
            process_started = _process_start_time(pid)
            if process_started is None:
                if _is_pid_alive(pid) is not False:
                    raise RuntimeError(
                        f"{self.id} {name} PID {pid} is live or unverifiable; refusing duplicate"
                    )
                continue
            # PowerShell StartTime ticks are .NET ticks since year 0001; the
            # cross-platform helper returns Unix seconds. Refuse PID reuse.
            recorded_unix = recorded_ticks / 10_000_000.0 - 62_135_596_800.0
            if abs(process_started - recorded_unix) > 2.0:
                if _is_pid_alive(pid, process_started) is not False:
                    raise RuntimeError(
                        f"{self.id} {name} PID {pid} does not match its saved start time"
                    )
                continue
            if _is_pid_alive(pid, process_started) is not True:
                raise RuntimeError(
                    f"{self.id} {name} PID {pid} identity is ambiguous; refusing duplicate"
                )
            existing = self.procs.get(name)
            if existing is not None and existing.pid != pid:
                raise RuntimeError(
                    f"{self.id} heartbeat/session disagree about the {name} PID; refusing duplicate"
                )
            if existing is None:
                self.procs[name] = ObservedProcess(
                    pid, process_started,
                    session_path if name == "loop" else None,
                )
            self.started_wall[name] = process_started
            self.started_at[name] = time.monotonic() - max(0.0, time.time() - process_started)

    def _ensure_child(self, name: str, args: list[str] | None, *, required: bool = True) -> None:
        if args is None:
            return
        proc = self.procs[name]
        if proc is not None:
            return_code = proc.poll()
            if return_code is None:
                if name == "loop" and self._loop_finished_but_alive(proc):
                    logger.warning("[%s] loop published a finished heartbeat but did not exit; stopping it",
                                   self.id)
                    if getattr(proc, "external", False):
                        self.exit_codes[name] = 0
                    self._stop_child("loop")
                    self.finished = True
                    for worker in ("screener", "observer", "watcher"):
                        self._stop_child(worker)
                    return
                if name == "loop" and self._loop_heartbeat_stale(proc):
                    if getattr(proc, "external", False):
                        logger.error("[%s] adopted loop PID %s heartbeat is stale; leaving external process untouched",
                                     self.id, proc.pid)
                        return
                    logger.error("[%s] loop PID %s heartbeat is stale; terminating wedged process",
                                 self.id, proc.pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
                    self.procs[name] = None
                    self._schedule_retry(name, "stale heartbeat")
                    return
                if time.monotonic() - self.started_at.get(name, time.monotonic()) >= 120:
                    self.failures[name] = 0
                return

            was_external = getattr(proc, "external", False)
            self.procs[name] = None
            self.exit_codes[name] = return_code
            if was_external:
                self.attached_existing = False
                self.retry_at.pop(name, None)
            if name == "loop" and return_code == 0:
                self.finished = True
                logger.info("[%s] shadow loop completed its timebox; stopping its support workers",
                            self.id)
                for worker in ("screener", "observer", "watcher"):
                    self._stop_child(worker)
                return
            self._schedule_retry(name, f"exit code {return_code}")

        if not required or self.finished or time.monotonic() < self.retry_at.get(name, 0.0):
            return
        if name == "loop" and self.exit_codes.get(name) == 0:
            return
        try:
            self.procs[name] = self._start_child(name, args)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._schedule_retry(name, f"start failed: {exc}")

    def _stop_child(self, name: str) -> None:
        proc = self.procs.get(name)
        if proc is None or proc.poll() is not None:
            self.procs[name] = None
            return
        if getattr(proc, "external", False):
            logger.info("[%s] leaving adopted %s PID %s running (not supervisor-owned)",
                        self.id, name, proc.pid)
            # An adopted process that the supervisor intends to restart must
            # first be observed exiting. Never start a second writer merely
            # because a heartbeat went stale or the scheduled window ended.
            self.retry_at[name] = float("inf")
            self.procs[name] = None
            return
        logger.info("[%s] stopping %s (PID %s)", self.id, name, proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        self.procs[name] = None

    def ensure_running(self) -> None:
        if not self.preflight_done:
            if time.monotonic() < self.preflight_retry_at:
                return
            try:
                self._preflight()
            except RuntimeError as exc:
                self.preflight_failures += 1
                delay = min(
                    CHILD_RETRY_MAX_SEC,
                    CHILD_RETRY_BASE_SEC * (2 ** min(self.preflight_failures - 1, 6)),
                )
                self.preflight_retry_at = time.monotonic() + delay
                logger.error("[%s] startup preflight blocked (%s); retry in %.0fs",
                             self.id, exc, delay)
                return
        self._ensure_child("dash", [
            "-m", "dashboard.server", "--db", self.cfg["db"], "--port", str(self.port),
        ])
        if self.procs["dash"] is None and self.procs["loop"] is not None and getattr(self.procs["loop"], "external", False):
            self._ensure_child("dash", [
                "-m", "dashboard.server", "--db", self.cfg["db"], "--port", str(self.port),
            ])
        self._ensure_child("loop", self.cfg["loop_args"])
        self._ensure_child("screener", self.cfg.get("screener_args"))
        self._ensure_child("observer", self.cfg["observer_args"])
        ring_path = PROJECT_ROOT / self.cfg["ring"]
        self._ensure_child("watcher", self.cfg["watcher_args"], required=ring_path.exists())

    def stop_all(self) -> None:
        for name in self.procs:
            self._stop_child(name)

    def status(self) -> dict[str, Any]:
        """Local process/heartbeat summary, including PIDs for local diagnostics."""
        children = {}
        for name, proc in self.procs.items():
            children[name] = {
                "running": proc is not None and proc.poll() is None,
                "pid": proc.pid if proc is not None and proc.poll() is None else None,
                "exit_code": self.exit_codes.get(name),
                "restart_failures": self.failures.get(name, 0),
            }
        loop = self.procs.get("loop")
        loop_running = loop is not None and loop.poll() is None
        hb = self._loop_heartbeat(loop) if loop_running else None
        loop_stale = self._loop_heartbeat_stale(loop) if loop_running else None
        return {
            "id": self.id,
            "port": self.port,
            "started": self.preflight_done,
            "blocked": not self.preflight_done,
            "finished": self.finished,
            "children": children,
            "loop_heartbeat_stale": loop_stale,
            "loop_heartbeat_age_sec": (
                round(max(0.0, time.time() - float(hb["heartbeat_ts"])), 1)
                if hb is not None else None
            ),
        }

    def monitor_snapshot(self) -> dict[str, Any]:
        """Health data safe to publish; do not expose PIDs or host paths."""
        status = self.status()
        return {
            "id": status["id"],
            "port": status["port"],
            "started": status["started"],
            "blocked": status["blocked"],
            "finished": status["finished"],
            "loop_heartbeat_stale": status["loop_heartbeat_stale"],
            "children": {
                name: {
                    "running": child["running"],
                    "exit_code": child["exit_code"],
                    "restart_failures": child["restart_failures"],
                }
                for name, child in status["children"].items()
            },
            "loop_heartbeat_age_sec": status["loop_heartbeat_age_sec"],
        }


def publish_cycle(
    state: dict[str, Any], now: float,
    *, supervisor_health: list[dict[str, Any]] | None = None,
) -> float:
    """Take and deploy fresh telemetry, retrying failures with bounded backoff."""
    try:
        if supervisor_health is None:
            snapshot = take_snapshot_and_render()
        else:
            snapshot = take_snapshot_and_render(supervisor_health=supervisor_health)
        url = deploy_to_vercel()
        state["publish_failures"] = 0
        logger.info("flight snapshot deployed (%d runs): %s", len(snapshot.get("runs", [])), url)
        return now + SNAPSHOT_INTERVAL_SEC
    except Exception:
        failures = int(state.get("publish_failures", 0)) + 1
        state["publish_failures"] = failures
        retry = min(PUBLISH_RETRY_MAX_SEC, PUBLISH_RETRY_BASE_SEC * (2 ** min(failures - 1, 5)))
        logger.exception("flight snapshot publish failed; retrying in %.0fs", retry)
        return now + retry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Supervise credential-free shadow rehearsals and publish read-only snapshots."
    )
    parser.add_argument(
        "--duration-hours", type=float, default=DEFAULT_SUPERVISOR_HOURS,
        help=f"stop cleanly after this many hours (default: {DEFAULT_SUPERVISOR_HOURS})",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration_hours) or args.duration_hours <= 0:
        parser.error("--duration-hours must be a positive finite number")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    lock = acquire_instance_lock()
    if lock is None:
        logger.error("another flight supervisor already holds the runtime lock; exiting")
        return 2

    child_group = None
    awake_guard = SystemAwakeGuard()
    runners: list[InstanceRunner] = []
    try:
        awake_guard.start()
        child_group = ChildProcessGroup()
        runners = [InstanceRunner(cfg, child_group) for cfg in INSTANCES]
        logger.info("flight supervisor started (PID %s)", os.getpid())
        logger.info("shadow-only window: %.2f hours; snapshots every 2 hours", args.duration_hours)
        publish_state: dict[str, Any] = {"publish_failures": 0}
        started = time.monotonic()
        deadline = started + args.duration_hours * 3600.0
        next_publish_at = started + INITIAL_PUBLISH_DELAY_SEC

        while time.monotonic() < deadline:
            for runner in runners:
                try:
                    runner.ensure_running()
                except Exception:
                    logger.exception("error supervising %s", runner.id)

            now = time.monotonic()
            if now >= next_publish_at:
                next_publish_at = publish_cycle(
                    publish_state, now,
                    supervisor_health=[runner.monitor_snapshot() for runner in runners],
                )
            time.sleep(min(CHECK_INTERVAL_SEC, max(0.0, deadline - time.monotonic())))
        logger.info("supervised window completed; shutting down cleanly")
    except KeyboardInterrupt:
        logger.info("received interrupt; shutting down")
    finally:
        for runner in runners:
            try:
                runner.stop_all()
            except Exception:
                logger.exception("error stopping children for %s", runner.id)
        # Closing a Windows job kills *all* remaining descendants, including
        # grandchildren started by helper modules rather than this process.
        if child_group is not None:
            child_group.close()
        awake_guard.stop()
        lock.close()
        logger.info("flight supervisor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
