from __future__ import annotations

import pytest

from tests.cli.conftest import Invoke


@pytest.fixture()
def sheet(initialised: Invoke) -> Invoke:
    initialised("sale", "create", "2024-03-12", "--title", "Tuesday", "--lanes", "A:50,B:50")
    initialised("roster", "add", "s-1", "Della Voss", "auctioneer")
    initialised("roster", "add", "s-2", "Ray Okafor", "ring")
    return initialised


def test_adding_and_listing_people(sheet: Invoke) -> None:
    output = sheet("roster", "people").output
    assert "Della Voss" in output
    assert "2 people" in output
    assert "1 people" in sheet("roster", "people", "--role", "ring").output


def test_assigning_a_lane(sheet: Invoke) -> None:
    assert (
        "on lane A as auctioneer" in sheet("roster", "assign", "S-20240312", "A", "s-1").output
    )
    assert "Della Voss" in sheet("roster", "lane", "S-20240312", "A").output


def test_double_booking_exits_seven(sheet: Invoke) -> None:
    sheet("roster", "assign", "S-20240312", "A", "s-1")
    sheet("roster", "assign", "S-20240312", "B", "s-1", expect=7)


def test_gaps_exit_non_zero_until_the_lanes_are_covered(sheet: Invoke) -> None:
    result = sheet("roster", "gaps", "S-20240312", expect=1)
    assert "lane A needs 1 auctioneer, 1 ring" in result.output
    assert "2 lanes short" in result.output


def test_a_covered_sale_reports_nothing_short(sheet: Invoke) -> None:
    sheet("roster", "add", "s-3", "Mel Byrne", "auctioneer")
    sheet("roster", "add", "s-4", "Jo Hale", "ring")
    sheet("roster", "assign", "S-20240312", "A", "s-1")
    sheet("roster", "assign", "S-20240312", "A", "s-2")
    sheet("roster", "assign", "S-20240312", "B", "s-3")
    sheet("roster", "assign", "S-20240312", "B", "s-4")
    assert "0 lanes short" in sheet("roster", "gaps", "S-20240312").output


def test_unassigning_and_retiring(sheet: Invoke) -> None:
    sheet("roster", "assign", "S-20240312", "A", "s-1")
    assert "off lane A" in sheet("roster", "unassign", "S-20240312", "A", "s-1").output
    assert "off the roster" in sheet("roster", "retire", "s-1").output
    sheet("roster", "assign", "S-20240312", "A", "s-1", expect=7)
