from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

VIN = "1HGCM82633A004352"


@pytest.fixture()
def office(initialised: Invoke, db_path: Path) -> Invoke:
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
    VehicleRepository(conn).add(Vehicle(vin=Vin.parse(VIN), make="Honda", model="Accord"))
    conn.close()
    return initialised


def through_the_block(run_cli: Invoke) -> None:
    run_cli("sale", "create", "2024-03-12", "--title", "Tuesday")
    run_cli("consign", "register", VIN, "d-100", "--floor", "3000.00")
    run_cli("consign", "check-in", VIN, "--keys", "2", "--miles", "40000")
    report = run_cli("inspect", "open", VIN, "--inspector", "j.hale").output.split()[1]
    run_cli("inspect", "sign", report, "--value", "12000.00")
    run_cli("sale", "catalogue", "S-20240312", "--lane", "A")
    run_cli("sale", "open", "S-20240312")
    run_cli("block", "bid", "S-20240312", "A001", "d-200", "8200.00")
    run_cli("block", "fall", "S-20240312", "A001")
    run_cli("sale", "close", "S-20240312")


def test_posting_the_sale_prints_both_sides(office: Invoke) -> None:
    through_the_block(office)
    output = office("settle", "post", "S-20240312").output
    assert "hammer 8,200.00" in output
    assert "buyer owes 8,545.00" in output
    assert "seller gets 7,965.00" in output
    assert "1 runs posted" in output


def test_posting_twice_posts_nothing_the_second_time(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    assert "0 runs posted" in office("settle", "post", "S-20240312").output


def test_a_buyer_invoice_and_a_seller_statement(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    invoice = office("settle", "statement", "d-200").output
    assert "8,545.00" in invoice
    assert "buyer statement" in invoice
    statement = office("settle", "statement", "d-100", "--side", "seller").output
    assert "7,965.00" in statement


def test_a_service_lands_on_the_right_side(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office("settle", "service", "S-20240312", "A001", "detail")
    assert "7,870.00" in office("settle", "statement", "d-100", "--side", "seller").output


def test_holds_read_out_over_a_phone(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    output = office("settle", "holds", VIN).output
    assert "the buyer still owes 8,545.00" in output
    assert "there is no title record" in output
    assert "2 holds" in output


def test_releasing_a_held_unit_exits_seven(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office("settle", "release", VIN, expect=7)


def test_the_whole_settlement_path(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office("settle", "pay", "d-200", "8545.00", "--method", "wire", "--reference", "40182")
    office("title", "expect", VIN)
    office("title", "receive", VIN, "--state", "TX")
    assert "0 holds" in office("settle", "holds", VIN).output
    assert "released 7,965.00" in office("settle", "release", VIN).output
    assert "balanced" in office("settle", "trial-balance").output


def test_a_lien_comes_off_before_the_seller(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office("settle", "pay", "d-200", "8545.00")
    office("title", "expect", VIN)
    office("title", "receive", VIN)
    released = office(
        "settle", "release", VIN, "--lien", "6000.00", "--lienholder", "d-100"
    ).output
    assert "released 1,965.00" in released


def test_the_queue_shows_what_is_ready(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    assert "0 ready, 1 held" in office("settle", "queue").output
    office("settle", "pay", "d-200", "8545.00")
    office("title", "expect", VIN)
    office("title", "receive", VIN)
    assert "1 ready, 0 held" in office("settle", "queue").output


def test_balances_list_both_sides(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    output = office("settle", "balances").output
    assert "d-200" in output
    assert "d-100" in output
    assert "2 accounts" in output


def test_exporting_the_sold_list_to_stdout(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    output = office("report", "export", "sold", "--sale", "S-20240312").output
    assert output.startswith("sale,run,vin")
    assert "8200.00" in output


def test_exporting_the_ledger_to_a_file(office: Invoke, tmp_path: Path) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    target = tmp_path / "ledger.csv"
    result = office("report", "export", "ledger", "--out", str(target)).output
    assert "3 rows to" in result
    assert target.read_text().startswith("entry,reference,memo")


def test_listing_the_exports(office: Invoke) -> None:
    assert "trial-balance" in office("report", "exports").output


def test_opening_and_reading_a_credit_line(office: Invoke) -> None:
    opened = office(
        "settle", "credit", "d-200", "--open", "L-1", "Ninth Street", "50000.00"
    ).output
    assert "L-1" in opened
    assert "50,000.00 of 50,000.00" in opened


def test_a_line_narrows_as_the_buyer_spends(office: Invoke) -> None:
    office("settle", "credit", "d-200", "--open", "L-1", "Ninth Street", "50000.00")
    through_the_block(office)
    office("settle", "post", "S-20240312")
    assert "41,455.00 of 50,000.00" in office("settle", "credit", "d-200").output


def test_suspending_and_reinstating_a_line(office: Invoke) -> None:
    office("settle", "credit", "d-200", "--open", "L-1", "Ninth Street", "50000.00")
    assert "suspended" in office("settle", "credit", "d-200", "--suspend", "L-1").output
    assert "active" in office("settle", "credit", "d-200", "--reinstate", "L-1").output


def test_moving_a_limit_from_the_command_line(office: Invoke) -> None:
    office("settle", "credit", "d-200", "--open", "L-1", "Ninth Street", "50000.00")
    moved = office("settle", "credit", "d-200", "--limit", "L-1", "75000.00").output
    assert "75,000.00" in moved


def test_opening_and_listing_a_period(office: Invoke) -> None:
    opened = office(
        "settle", "period", "open", "--from", "2024-03-01", "--to", "2024-03-31"
    ).output
    assert "P-202403 open" in opened
    assert "P-202403" in office("settle", "period", "list").output


def test_open_without_dates_is_a_usage_error(office: Invoke) -> None:
    office("settle", "period", "open", expect=2)


def test_checks_fail_on_an_unposted_sale(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "period", "open", "--from", "2024-03-01", "--to", "2024-03-31")
    result = office("settle", "period", "checks", "--id", "P-202403", expect=1)
    assert "FAIL" in result.output
    assert "checks failed" in result.output


def test_the_whole_close(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office("settle", "period", "open", "--from", "2024-03-01", "--to", "2024-03-31")
    assert "0 checks failed" in office("settle", "period", "checks", "--id", "P-202403").output
    assert "is closing" in office("settle", "period", "begin-close", "--id", "P-202403").output
    closed = office("settle", "period", "close", "--id", "P-202403", "--by", "m.byrne").output
    assert "closed by m.byrne" in closed


def test_the_notice_queue_starts_empty(office: Invoke) -> None:
    assert "nothing queued" in office("settle", "notices").output


def test_reconciling_a_bank_file(office: Invoke, tmp_path: Path) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    bank = tmp_path / "bank.csv"
    bank.write_text("date,amount,reference\n2024-03-14,8545.00,WIRE-1\n")
    output = office("settle", "reconcile", str(bank)).output
    assert "1 lines, 1 matched" in output
    assert "d-200" in output


def test_applying_a_reconciliation(office: Invoke, tmp_path: Path) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    bank = tmp_path / "bank.csv"
    bank.write_text("date,amount,reference\n2024-03-14,8545.00,WIRE-1\n")
    assert "took in 1 payments" in office("settle", "reconcile", str(bank), "--apply").output
    assert "0 holds" not in office("settle", "holds", VIN).output


def test_an_unmatched_line_is_reported(office: Invoke, tmp_path: Path) -> None:
    bank = tmp_path / "bank.csv"
    bank.write_text("date,amount,reference\n2024-03-14,77.00,MYSTERY\n")
    output = office("settle", "reconcile", str(bank)).output
    assert "unmatched" in output
    assert "1 for somebody to look at" in output


def test_a_credit_note_from_the_command_line(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    output = office(
        "settle",
        "adjust",
        "credit_note",
        "d-200",
        "100.00",
        "--reason",
        "fee charged twice",
        "--by",
        "p.dunne",
    ).output
    assert "credit_note" in output
    assert "100.00" in output
    assert "written off in total" in office("settle", "adjustments", "d-200").output


def test_a_large_adjustment_needs_a_second_name(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    office(
        "settle",
        "adjust",
        "credit_note",
        "d-200",
        "900.00",
        "--reason",
        "goodwill",
        "--by",
        "p.dunne",
        expect=7,
    )
    office(
        "settle",
        "adjust",
        "credit_note",
        "d-200",
        "900.00",
        "--reason",
        "goodwill",
        "--by",
        "p.dunne",
        "--approved-by",
        "m.byrne",
    )


def test_the_dashboard_prints(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    output = office("report", "dashboard", "--sale", "S-20240312").output
    assert "the sale" in output
    assert "the money" in output
    assert "8,545.00" in output


def test_the_dispatch_job_is_safe_to_run_twice(office: Invoke) -> None:
    through_the_block(office)
    office("settle", "post", "S-20240312")
    first = office("settle", "dispatch").output
    assert "queued" in first
    assert "0 queued" in office("settle", "dispatch").output
