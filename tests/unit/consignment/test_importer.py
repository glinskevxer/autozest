import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.consignment.importer import ConsignmentImporter, parse
from autozest.errors import ValidationError
from autozest.money import Money

HEADER = "vin,make,model,year,floor,title,note\n"
ROW = "1HGCM82633A004352,Honda,Accord,2003,4500.00,yes,clean unit\n"
ROW2 = "5YJ3E1EA0KF000316,Tesla,Model 3,2019,18000.00,yes,\n"


@pytest.fixture()
def importer(db: sqlite3.Connection) -> ConsignmentImporter:
    AccountRepository(db).add(
        Account(
            id="f-100",
            name="Halloway Fleet",
            account_class=AccountClass.FLEET_LEASE,
            dealer_licence="TX-88121",
            state="TX",
        )
    )
    AccountRepository(db).add(
        Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC)
    )
    return ConsignmentImporter(db)


def test_an_empty_file() -> None:
    plan = parse("", "f-100")
    assert not plan.is_clean
    assert "the file is empty" in plan.report()[0]


def test_a_file_missing_a_column() -> None:
    plan = parse("vin,make\n1HGCM82633A004352,Honda\n", "f-100")
    assert "missing columns: model" in plan.report()[0]


def test_a_clean_file_plans_every_row() -> None:
    plan = parse(HEADER + ROW + ROW2, "f-100")
    assert plan.is_clean
    assert plan.count == 2
    assert plan.rows[0][2] == Money.parse("4500.00")


def test_every_problem_is_reported_not_just_the_first() -> None:
    text = HEADER + ",Honda,Accord,2003,,,\n" + "NOTAVIN,Honda,Accord,2003,,,\n"
    plan = parse(text, "f-100")
    assert len(plan.problems) >= 2
    assert any("vin" in problem for problem in plan.report())


def test_problems_carry_the_line_number_from_the_file() -> None:
    plan = parse(HEADER + ROW + ",Honda,Accord,,,,\n", "f-100")
    assert plan.problems[0].line == 3


def test_a_vin_twice_in_one_file() -> None:
    plan = parse(HEADER + ROW + ROW, "f-100")
    assert "appears twice" in plan.report()[0]


def test_a_year_that_is_not_a_year() -> None:
    plan = parse(HEADER + "1HGCM82633A004352,Honda,Accord,nineteen,,,\n", "f-100")
    assert "not a year" in plan.report()[0]


def test_a_year_nobody_believes() -> None:
    plan = parse(HEADER + "1HGCM82633A004352,Honda,Accord,1742,,,\n", "f-100")
    assert "believable" in plan.report()[0]


def test_a_floor_that_is_not_money() -> None:
    plan = parse(HEADER + "1HGCM82633A004352,Honda,Accord,2003,lots,,\n", "f-100")
    assert "floor" in plan.report()[0]


def test_a_missing_check_digit_is_tolerated() -> None:
    broken = "1HGCM82639A004352,Honda,Accord,2003,,,\n"
    assert parse(HEADER + broken, "f-100").is_clean


def test_unknown_columns_are_ignored() -> None:
    text = "vin,make,model,fleet_code\n1HGCM82633A004352,Honda,Accord,ZZ99\n"
    assert parse(text, "f-100").is_clean


def test_the_title_column_reads_as_a_flag() -> None:
    text = HEADER + "1HGCM82633A004352,Honda,Accord,2003,,no,\n"
    assert parse(text, "f-100").rows[0][4] is False


def test_importing_creates_vehicles_and_consignments(
    importer: ConsignmentImporter,
) -> None:
    created = importer.apply(importer.plan(HEADER + ROW + ROW2, "f-100"))
    assert len(created) == 2
    assert importer.vehicles.find("1HGCM82633A004352") is not None
    assert created[0].floor == Money.parse("4500.00")


def test_a_seller_who_cannot_consign(importer: ConsignmentImporter) -> None:
    plan = importer.plan(HEADER + ROW, "p-1")
    assert not plan.is_clean
    assert "seller" in plan.report()[0]


def test_a_vin_already_on_the_ground(importer: ConsignmentImporter) -> None:
    importer.apply(importer.plan(HEADER + ROW, "f-100"))
    plan = importer.plan(HEADER + ROW, "f-100")
    assert "already consigned as" in plan.report()[0]


def test_nothing_is_written_when_anything_is_wrong(
    importer: ConsignmentImporter,
) -> None:
    plan = importer.plan(HEADER + ROW + ",Honda,Accord,,,,\n", "f-100")
    with pytest.raises(ValidationError, match="none of it was imported"):
        importer.apply(plan)
    assert importer.intake.consignments.count() == 0


def test_importing_from_a_file(importer: ConsignmentImporter, tmp_path: Path) -> None:
    target = tmp_path / "fleet.csv"
    target.write_text(HEADER + ROW)
    assert len(importer.import_file(target, "f-100")) == 1
