from pathlib import Path

from click.testing import CliRunner

from autozest import __version__
from autozest.cli.main import cli, run
from tests.cli.conftest import Invoke


def test_help_does_not_create_a_database(db_path: Path) -> None:
    result = CliRunner().invoke(cli, ["--db", str(db_path), "--help"])
    assert result.exit_code == 0
    assert "wholesale vehicle auction" in result.output
    assert not db_path.exists()


def test_the_version_is_the_package_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert __version__ in result.output


def test_init_creates_the_file_and_reports_the_schema_version(
    run: Invoke, db_path: Path
) -> None:
    result = run("init")
    assert db_path.exists()
    assert "schema version" in result.output


def test_init_twice_is_harmless(run: Invoke) -> None:
    first = run("init")
    second = run("init")
    assert first.output == second.output


def test_status_reports_the_auction_and_the_schema(initialised: Invoke) -> None:
    output = initialised("status").output
    assert "Autozest Remarketing" in output
    assert "AZ01" in output
    assert "schema" in output
    assert "pending" not in output


def test_doctor_is_quiet_on_a_healthy_database(initialised: Invoke) -> None:
    assert "no problems" in initialised("doctor").output


def test_a_command_against_a_missing_database_exits_three(db_path: Path) -> None:
    assert run(["--db", str(db_path), "status"]) == 3


def test_run_maps_a_config_error_to_its_status(tmp_path: Path) -> None:
    assert run(["--config", str(tmp_path / "nope.toml"), "status"]) == 3


def test_run_maps_an_unknown_command_to_the_click_status() -> None:
    assert run(["nosuchcommand"]) == 2


def test_run_of_help_exits_zero() -> None:
    assert run(["--help"]) == 0
