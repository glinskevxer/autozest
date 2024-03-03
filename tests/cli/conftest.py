"""Driving the command line the way an operator does.

The tests go through :func:`autozest.cli.main.run` rather than clicking the
group directly, because run() is where a domain error becomes an exit status
and that mapping is most of what these tests are checking.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from autozest.cli.main import run

Invoke = Callable[..., "Outcome"]


@dataclass(frozen=True)
class Outcome:
    """What one invocation printed and what it exited with."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def invoke(*args: str) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run(list(args))
    return Outcome(exit_code=code, stdout=out.getvalue(), stderr=err.getvalue())


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sale.sqlite3"


@pytest.fixture()
def run_cli(db_path: Path) -> Invoke:
    def call(*args: str, expect: int = 0) -> Outcome:
        outcome = invoke("--db", str(db_path), *args)
        assert outcome.exit_code == expect, (
            f"az {' '.join(args)} exited {outcome.exit_code}, wanted {expect}\n{outcome.output}"
        )
        return outcome

    return call


@pytest.fixture()
def initialised(run_cli: Invoke) -> Invoke:
    run_cli("init")
    return run_cli
