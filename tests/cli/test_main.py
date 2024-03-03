from pathlib import Path

from autozest import __version__
from tests.cli.conftest import Invoke, invoke


def test_help_does_not_create_a_database(db_path: Path) -> None:
    result = invoke("--db", str(db_path), "--help")
    assert result.exit_code == 0
    assert "wholesale vehicle auction" in result.output
    assert not db_path.exists()


def test_the_version_is_the_package_version() -> None:
    assert __version__ in invoke("--version").output


def test_init_creates_the_file_and_reports_the_schema_version(
    run_cli: Invoke, db_path: Path
) -> None:
    result = run_cli("init")
    assert db_path.exists()
    assert "schema version" in result.output


def test_init_twice_is_harmless(run_cli: Invoke) -> None:
    first = run_cli("init")
    second = run_cli("init")
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
    assert invoke("--db", str(db_path), "status").exit_code == 3


def test_run_maps_a_config_error_to_its_status(tmp_path: Path) -> None:
    assert invoke("--config", str(tmp_path / "nope.toml"), "status").exit_code == 3


def test_run_maps_an_unknown_command_to_the_click_status() -> None:
    assert invoke("nosuchcommand").exit_code == 2


def test_run_of_help_exits_zero() -> None:
    assert invoke("--help").exit_code == 0
