"""The one timestamp in every generated filename.

Names are part of the contract (`docs/agents/naming.md`), and two writers each
formatted their own stamp with `%d-%m_%H-%M`. That stamp has no year, so last
October and this October collide, and `21-10` sorts after `04-11`. Windows
forbids `:` in a filename, so the ISO 8601 basic form takes a hyphen in the
time: `2026-10-23T08-47` sorts lexicographically, carries the year, and stays
unambiguous across locales.

Writers call `stamp()`; nobody formats a date into a filename by hand.
"""
from __future__ import annotations

import datetime

#: `2026-10-23T08-47` -- ISO 8601 basic, colon replaced for Windows.
STAMP_FORMAT = "%Y-%m-%dT%H-%M"


def stamp(now: datetime.datetime | None = None) -> str:
    """The launch/file stamp for a generated name. Local time, not UTC.

    Local time because every reader of these names is the operator sitting in
    front of the machine: a report stamped 06-47 UTC for a 09-47 run is a name
    that lies about when the run happened.
    """
    return (now or datetime.datetime.now()).strftime(STAMP_FORMAT)
