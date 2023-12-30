from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from autozest.cli.main import cli

Invoke = Callable[..., Result]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sale.sqlite3"


@pytest.fixture()
def run(db_path: Path) -> Invoke:
    """Invoke the command line against a throwaway database."""
    runner = CliRunner()

    def invoke(*args: str, expect: int = 0) -> Result:
        result = runner.invoke(cli, ["--db", str(db_path), *args], catch_exceptions=False)
        assert result.exit_code == expect, (
            f"az {' '.join(args)} exited {result.exit_code}\n{result.output}"
        )
        return result

    return invoke


@pytest.fixture()
def initialised(run: Invoke) -> Invoke:
    run("init")
    return run
