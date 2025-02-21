from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"


@pytest.fixture()
def stocked(initialised: Invoke, db_path: Path) -> Invoke:
    conn: sqlite3.Connection = connect(db_path)
    AccountRepository(conn).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-44192",
            state="TX",
        )
    )
    vehicles = VehicleRepository(conn)
    vehicles.add(Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord", model_year=2019))
    vehicles.add(Vehicle(vin=Vin.parse(MODEL3), make="Tesla", model="Model 3"))
    conn.close()
    return initialised


def test_registering_from_the_command_line(stocked: Invoke) -> None:
    result = stocked("consign", "register", ACCORD.lower(), "d-100", "--floor", "4500.00")
    assert "registered C-" in result.output
    assert ACCORD in result.output


def test_registering_a_vin_that_is_not_on_file_exits_four(stocked: Invoke) -> None:
    stocked("consign", "register", "JTDKN3DU8A0000001", "d-100", expect=4)


def test_checking_in_by_vin_prints_the_gate_pass(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    result = stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    assert "gate pass" in result.output


def test_a_second_check_in_exits_seven(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930", expect=7)


def test_show_puts_the_whole_unit_on_one_screen(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100", "--floor", "4500.00")
    stocked("consign", "check-in", ACCORD, "--keys", "0", "--miles", "41930")
    output = stocked("consign", "show", ACCORD).output
    assert "2019 Honda Accord" in output
    assert "4,500.00" in output
    assert "41930" in output


def test_listing_what_is_on_the_ground(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "register", MODEL3, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    on_ground = stocked("consign", "list").output
    assert ACCORD in on_ground
    assert MODEL3 not in on_ground
    assert "1 units" in on_ground
    registered = stocked("consign", "list", "--status", "registered").output
    assert MODEL3 in registered


def test_listing_for_a_seller(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    assert "1 units" in stocked("consign", "list", "--seller", "d-100").output
    assert "0 units" in stocked("consign", "list", "--seller", "d-999").output


def test_moving_the_floor(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100", "--floor", "4500.00")
    assert "3,900.00" in stocked("consign", "floor", ACCORD, "3900.00").output


def test_withdrawing(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    assert "withdrew" in stocked("consign", "withdraw", ACCORD, "sold retail").output


def test_the_whole_inspection_path(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    opened = stocked("inspect", "open", ACCORD, "--inspector", "j.hale").output
    report = opened.split()[1]
    stocked(
        "inspect",
        "damage",
        report,
        "--panel",
        "rail",
        "--kind",
        "bent",
        "--severity",
        "2",
        "--estimate",
        "100.00",
    )
    listing = stocked("inspect", "damage-list", report).output
    assert "rail" in listing
    assert "1 findings" in listing
    signed = stocked("inspect", "sign", report, "--value", "12000.00").output
    assert "grade 3.0" in signed
    assert "unibody damage" in signed


def test_signing_a_body_on_frame_unit_calls_frame_damage(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    report = stocked("inspect", "open", ACCORD, "--inspector", "j.hale").output.split()[1]
    stocked(
        "inspect",
        "damage",
        report,
        "--panel",
        "rail",
        "--kind",
        "bent",
        "--severity",
        "2",
        "--estimate",
        "100.00",
    )
    signed = stocked("inspect", "sign", report, "--body-on-frame").output
    assert "frame damage" in signed


def test_an_unknown_panel_is_rejected_by_click(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    report = stocked("inspect", "open", ACCORD, "--inspector", "j.hale").output.split()[1]
    stocked(
        "inspect",
        "damage",
        report,
        "--panel",
        "spoiler",
        "--kind",
        "dent",
        "--severity",
        "2",
        expect=2,
    )


def test_the_light_shows_on_the_unit_screen_after_signing(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    report = stocked("inspect", "open", ACCORD, "--inspector", "j.hale").output.split()[1]
    stocked(
        "inspect",
        "damage",
        report,
        "--panel",
        "rail",
        "--kind",
        "bent",
        "--severity",
        "2",
        "--estimate",
        "100.00",
    )
    stocked("inspect", "sign", report)
    output = stocked("consign", "show", ACCORD).output
    assert "yellow" in output
    assert "unibody damage" in output


FLEET_CSV = (
    "vin,make,model,year,floor,title,note\n"
    "1HGCM82633A004352,Honda,Accord,2019,4500.00,yes,clean unit\n"
    "5YJ3E1EA0KF000316,Tesla,Model 3,2019,18000.00,yes,\n"
)


def test_a_dry_run_writes_nothing(stocked: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "fleet.csv"
    target.write_text(FLEET_CSV)
    result = stocked("consign", "import", str(target), "d-100", "--dry-run")
    assert "2 rows would import cleanly" in result.output
    assert "0 units" in stocked("consign", "list").output


def test_a_real_import_registers_everything(stocked: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "fleet.csv"
    target.write_text(FLEET_CSV)
    result = stocked("consign", "import", str(target), "d-100")
    assert "2 units registered" in result.output
    assert "2 units" in stocked("consign", "list", "--status", "registered").output


def test_a_bad_file_exits_two_and_writes_nothing(stocked: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "fleet.csv"
    target.write_text(FLEET_CSV + "NOTAVIN,Honda,Accord,2019,,,\n")
    result = stocked("consign", "import", str(target), "d-100", expect=2)
    assert "nothing imported" in result.output
    assert "0 units" in stocked("consign", "list", "--status", "registered").output


def test_a_file_that_is_not_there(stocked: Invoke, tmp_path: Path) -> None:
    stocked("consign", "import", str(tmp_path / "nope.csv"), "d-100", expect=2)


def test_the_unit_history(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    stocked("consign", "withdraw", ACCORD, "sold retail")
    stocked("consign", "register", ACCORD, "d-100")
    output = stocked("consign", "history", ACCORD).output
    assert "2 visits" in output
    assert "1 odometer readings" in output
    assert "41930" in output


def test_the_inspection_queue(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    output = stocked("inspect", "queue").output
    assert "1 waiting" in output
    assert ACCORD in output


def test_the_queue_empties_when_the_report_is_signed(stocked: Invoke) -> None:
    stocked("consign", "register", ACCORD, "d-100")
    stocked("consign", "check-in", ACCORD, "--keys", "2", "--miles", "41930")
    report = stocked("inspect", "open", ACCORD, "--inspector", "j.hale").output.split()[1]
    assert "half done" in stocked("inspect", "queue").output
    stocked("inspect", "sign", report)
    assert "0 waiting" in stocked("inspect", "queue").output
