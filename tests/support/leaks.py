"""Watch for sqlite3 connections the suite opens and never closes.

Python 3.13 raises ResourceWarning when an unclosed connection is collected,
and this project runs warnings as errors, so a leaked connection is a failing
build. What makes it nasty is where it lands. Collection happens whenever the
collector feels like it, so the warning attaches to whichever test is running
at the time rather than the one that leaked, and the CI log names three
innocent tests. Worse, 3.12 does not emit that warning for sqlite3 at all, so
the whole class of bug is invisible on the version most of the work is done on.

This finds them by construction instead. Every connection is tagged with the
test that opened it, closing one clears the tag, and anything still tagged at
the end of the session is a leak with a name attached.

The tracking subclass is installed as sqlite3's default connection factory. A
caller that passes its own factory keeps it and goes untracked, which is the
right trade: the alternative is rewriting somebody else's class at import time.
"""

from __future__ import annotations

import itertools
import sqlite3
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Everything below here is ours. Frames from the standard library and from
#: installed packages are dropped, which leaves the call chain that matters.
ROOT = Path(__file__).resolve().parents[2]

#: How many frames of the opening stack to keep. Enough to see the call chain
#: into the storage layer, short enough to fit on one line of output.
FRAMES = 3


@dataclass(frozen=True)
class Leak:
    """One connection that was opened and never closed."""

    nodeid: str
    origin: str

    def __str__(self) -> str:
        return f"{self.nodeid}\n    opened at {self.origin}"


class ConnectionWatch:
    """Tracks open sqlite3 connections and which test opened each one.

    Not installed by default. ``install`` swaps in a patched ``sqlite3.connect``
    and ``uninstall`` puts the original back, so a test can drive one of these
    directly without disturbing the watch the session is running under.
    """

    def __init__(self) -> None:
        self._live: dict[int, Leak] = {}
        self._tokens = itertools.count(1)
        self._nodeid = "<session>"
        self._real: Callable[..., sqlite3.Connection] | None = None

    def install(self) -> None:
        if self._real is not None:
            raise RuntimeError("this watch is already installed")
        real = sqlite3.connect
        self._real = real
        watch = self

        class Tracked(sqlite3.Connection):
            #: Set by the wrapper below, and the key this connection is filed
            #: under. Its own id() cannot be the key. A leaked connection is
            #: freed when the test that made it goes out of scope, CPython
            #: hands the address straight back to the next allocation, and
            #: closing that one would clear the leak we are trying to report.
            token: int

            def close(self) -> None:
                watch._forget(getattr(self, "token", 0))
                super().close()

        def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            kwargs.setdefault("factory", Tracked)
            conn: sqlite3.Connection = real(*args, **kwargs)
            if isinstance(conn, Tracked):
                conn.token = next(watch._tokens)
                watch._remember(conn.token)
            return conn

        sqlite3.connect = connect  # type: ignore[assignment]

    def uninstall(self) -> None:
        if self._real is None:
            return
        sqlite3.connect = self._real  # type: ignore[assignment]
        self._real = None

    def entering(self, nodeid: str) -> None:
        """Name the test that connections opened from here belong to."""
        self._nodeid = nodeid

    def _remember(self, token: int) -> None:
        self._live[token] = Leak(self._nodeid, _origin())

    def _forget(self, token: int) -> None:
        self._live.pop(token, None)

    @property
    def outstanding(self) -> list[Leak]:
        return list(self._live.values())

    def report(self) -> str:
        """A message naming every leak, or an empty string when there are none."""
        if not self._live:
            return ""
        counted: dict[Leak, int] = {}
        for leak in self._live.values():
            counted[leak] = counted.get(leak, 0) + 1
        lines = [
            f"{len(self._live)} sqlite3 connections were never closed.",
            "Python 3.13 turns each one into a ResourceWarning, and warnings are",
            "errors here, so this fails the build on 3.13 and passes on 3.12.",
            "",
        ]
        lines += [
            f"  x{count} {leak}"
            for leak, count in sorted(counted.items(), key=lambda pair: -pair[1])
        ]
        return "\n".join(lines)


def _origin() -> str:
    """Where in the checkout the connection was opened, as a short chain.

    Paths come back relative to the repository root. Matching on the string
    "autozest" instead looks obvious and is wrong, because the checkout
    directory is usually called autozest too, so every frame matches and the
    chain comes back as three absolute paths through site-packages.
    """
    here = Path(__file__).resolve()
    ours = []
    for frame in traceback.extract_stack():
        path = Path(frame.filename).resolve()
        if path == here or "site-packages" in path.parts:
            continue
        try:
            ours.append(f"{path.relative_to(ROOT)}:{frame.lineno}")
        except ValueError:
            continue
    if not ours:
        return "outside the checkout"
    return " <- ".join(ours[-FRAMES:])
