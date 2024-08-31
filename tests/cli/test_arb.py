from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

VINS = ["1HGCM82633A004352", "5YJ3E1EA0KF000316"]


@pytest.fixture()
def desk(initialised: Invoke, db_path: Path) -> Invoke:
    conn: sqlite3.Connection = connect(db_path)
    accounts = AccountRepository(conn)
    for account_id, name in (("d-100", "Ridgeline Motors"), ("d-200", "Kestrel Auto")):
        accounts.add(
            Account(
                id=account_id,
                name=name,
                account_class=AccountClass.INDEPENDENT_DEALER,
                dealer_licence=f"TX-{account_id}",
                state="TX",
            )
        )
    vehicles = VehicleRepository(conn)
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))
    conn.close()
    return initialised


def sold(run_cli: Invoke, vin: str = VINS[0], price: str = "8200.00") -> None:
    """Put a unit all the way through to sold, under a green light.

    The floor sits on the as-is threshold so the light stays green, which means
    a bid under it goes on if and has to be accepted to land as a sale.
    """
    run_cli("sale", "create", "2024-03-12", "--title", "Tuesday")
    run_cli("consign", "register", vin, "d-100", "--floor", "3000.00")
    run_cli("consign", "check-in", vin, "--keys", "2", "--miles", "40000")
    report = run_cli("inspect", "open", vin, "--inspector", "j.hale").output.split()[1]
    run_cli("inspect", "sign", report, "--value", "12000.00")
    run_cli("sale", "catalogue", "S-20240312", "--lane", "A")
    run_cli("sale", "open", "S-20240312")
    run_cli("block", "bid", "S-20240312", "A001", "d-200", price)
    if "on if" in run_cli("block", "fall", "S-20240312", "A001").output:
        run_cli("block", "accept", "S-20240312", "A001")


def test_assess_lists_what_is_open(desk: Invoke) -> None:
    sold(desk)
    output = desk("arb", "assess", "S-20240312", "A001").output
    assert "engine" in output
    assert "body_cosmetic" not in output
    assert "classes open" in output


def test_assess_of_one_class_says_when_it_closes(desk: Invoke) -> None:
    sold(desk)
    assert (
        "window closes"
        in desk("arb", "assess", "S-20240312", "A001", "--class", "engine").output
    )


def test_assess_of_a_class_that_is_shut_exits_seven(desk: Invoke) -> None:
    sold(desk)
    result = desk("arb", "assess", "S-20240312", "A001", "--class", "body_cosmetic", expect=7)
    assert "not an arbitrable class" in result.output


def test_the_desk_takes_and_resolves_a_claim(desk: Invoke) -> None:
    sold(desk)
    opened = desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking cold",
        "--claimed",
        "1800.00",
    ).output
    claim = opened.split()[1]
    desk("arb", "take", claim, "--note", "rang the seller")
    resolved = desk(
        "arb",
        "resolve",
        claim,
        "repair_allowance",
        "--amount",
        "1200.00",
        "--reason",
        "shop quote",
    ).output
    assert "repair_allowance 1,200.00" in resolved
    assert "seller pays" in resolved


def test_an_adjustment_moves_the_hammer_on_the_history(desk: Invoke) -> None:
    sold(desk)
    claim = desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
    ).output.split()[1]
    desk("arb", "take", claim)
    desk(
        "arb", "resolve", claim, "price_adjustment", "--amount", "1200.00", "--reason", "agreed"
    )
    history = desk("arb", "history", "S-20240312", "A001").output
    assert "hammer now 7,000.00" in history
    assert "1 classes used up" in history


def test_a_claim_from_someone_who_did_not_buy_it(desk: Invoke) -> None:
    sold(desk)
    desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-100",
        "--class",
        "engine",
        "--summary",
        "knocking",
        expect=7,
    )


def test_a_claim_under_the_threshold(desk: Invoke) -> None:
    sold(desk, price="2000.00")
    result = desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
        expect=7,
    )
    assert "3,500.00 or more" in result.output


def test_the_queue_and_one_claim_in_full(desk: Invoke) -> None:
    sold(desk)
    claim = desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
    ).output.split()[1]
    assert "1 claims" in desk("arb", "queue").output
    shown = desk("arb", "show", claim).output
    assert "history" in shown
    assert "opened" in shown
    assert "knocking" in shown


def test_withdrawing_from_the_command_line(desk: Invoke) -> None:
    sold(desk)
    claim = desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
    ).output.split()[1]
    assert "withdrew" in desk("arb", "withdraw", claim, "seller sorted it").output
    assert "0 claims" in desk("arb", "queue").output


def test_ordering_and_completing_an_inspection(desk: Invoke) -> None:
    sold(desk)
    assert "clock is stopped" in desk("arb", "inspect", "S-20240312", "A001").output
    done = desk(
        "arb",
        "inspect",
        "S-20240312",
        "A001",
        "--complete",
        "--failed",
        "--findings",
        "compression low",
    ).output
    assert "failed" in done


def test_the_class_table_prints_the_policy(desk: Invoke) -> None:
    output = desk("arb", "classes").output
    assert "48 business hours" in output
    assert "policy 2024.1" in output
    assert "3,500.00" in output


def decided_claim(desk_cli: Invoke) -> str:
    sold(desk_cli)
    claim = desk_cli(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
    ).output.split()[1]
    desk_cli("arb", "take", claim)
    desk_cli(
        "arb", "resolve", claim, "repair_allowance", "--amount", "600.00", "--reason", "quote"
    )
    return claim


def test_raising_and_upholding_an_appeal(desk: Invoke) -> None:
    claim = decided_claim(desk)
    raised = desk(
        "arb", "appeal", claim, "--by", "d-200", "--grounds", "wrong engine quoted"
    ).output
    reference = raised.split()[0]
    assert "raised on" in raised
    assert "1 open appeals" in desk("arb", "appeals").output
    settled = desk(
        "arb", "appeal-decide", reference, "upheld", "--by", "m.byrne", "--note", "stands"
    ).output
    assert "upheld" in settled
    assert "600.00" in settled


def test_varying_an_appeal_from_the_command_line(desk: Invoke) -> None:
    claim = decided_claim(desk)
    reference = desk(
        "arb", "appeal", claim, "--by", "d-100", "--grounds", "too much"
    ).output.split()[0]
    settled = desk(
        "arb",
        "appeal-decide",
        reference,
        "varied",
        "--by",
        "m.byrne",
        "--note",
        "split it",
        "--amount",
        "300.00",
    ).output
    assert "300.00" in settled


def test_a_stranger_cannot_appeal(desk: Invoke) -> None:
    claim = decided_claim(desk)
    desk("arb", "appeal", claim, "--by", "d-999", "--grounds", "curious", expect=7)


def test_the_worklist_prints(desk: Invoke) -> None:
    sold(desk)
    desk(
        "arb",
        "open",
        "S-20240312",
        "A001",
        "--claimant",
        "d-200",
        "--class",
        "engine",
        "--summary",
        "knocking",
    )
    output = desk("arb", "worklist").output
    assert "arbitration queue" in output
    assert "1 open" in output
