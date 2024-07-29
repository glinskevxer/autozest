"""Driving the command line the way an operator does.

The tests go through :func:`autozest.cli.main.run` rather than clicking the
group directly, because run() is where a domain error becomes an exit status
and that mapping is most of what these tests are checking.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from autozest.cli.main import run
from autozest.storage.db import connect
from autozest.storage.migrator import migrate

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


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A migrated database file, built once for the whole run.

    Every command line test needs one and running the migrations for each of
    them was most of what the suite spent its time on. Copying the file is the
    same thing and is not measurable.
    """
    target = tmp_path_factory.mktemp("template") / "template.sqlite3"
    conn = connect(target)
    migrate(conn)
    conn.close()
    return target


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
def initialised(run_cli: Invoke, db_path: Path, migrated_template: Path) -> Invoke:
    """A command line pointed at a database that is already migrated."""
    shutil.copy(migrated_template, db_path)
    return run_cli
