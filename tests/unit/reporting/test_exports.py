from pathlib import Path

import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.reporting.exports import (
    Export,
    available,
    claims,
    ledger_lines,
    named,
    plain,
    sold_units,
    trial_balance,
)
from autozest.settlement.postings import PostingService
from tests.conftest import Floor


def sold(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    return run_id


def test_amounts_go_out_without_a_comma() -> None:
    assert plain(Money.parse("8200.00")) == "8200.00"
    assert plain(Money.parse("1234567.89")) == "1234567.89"
    assert plain(None) == ""
    assert plain(True) == "true"
    assert plain(False) == "false"


def test_a_row_that_does_not_fit_the_header() -> None:
    with pytest.raises(ValidationError, match="cells"):
        Export(name="x", header=["a", "b"], rows=[["only one"]])


def test_an_export_writes_a_header_even_with_no_rows() -> None:
    text = Export(name="x", header=["a", "b"], rows=[]).to_text()
    assert text == "a,b\n"


def test_quoting_is_left_to_the_csv_module() -> None:
    text = Export(name="x", header=["a"], rows=[["one, two"]]).to_text()
    assert '"one, two"' in text


def test_the_sold_list(floor: Floor) -> None:
    sold(floor)
    export = sold_units(floor.conn, floor.code)
    assert export.row_count == 1
    lines = export.to_text().splitlines()
    assert lines[0].startswith("sale,run,vin")
    assert "8200.00" in lines[1]
    assert "d-200" in lines[1]


def test_the_sold_list_leaves_out_what_did_not_sell(floor: Floor) -> None:
    floor.catalogue()
    floor.sales.open_sale(floor.code)
    assert sold_units(floor.conn, floor.code).row_count == 0


def test_the_ledger_export(floor: Floor) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    export = ledger_lines(floor.conn)
    assert export.row_count == 3
    assert "buyer_receivable" in export.to_text()


def test_the_ledger_export_narrows_by_reference(floor: Floor) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    assert ledger_lines(floor.conn, floor.code).row_count == 3
    assert ledger_lines(floor.conn, "S-19990101").row_count == 0


def test_the_claims_export_is_empty_when_nobody_complained(floor: Floor) -> None:
    sold(floor)
    assert claims(floor.conn).row_count == 0
    assert claims(floor.conn).to_text().startswith("claim,sale,run")


def test_the_trial_balance_export(floor: Floor) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    export = trial_balance(floor.conn)
    assert export.row_count == 3
    assert "fee_income" in export.to_text()


def test_writing_to_a_file(floor: Floor, tmp_path: Path) -> None:
    sold(floor)
    target = sold_units(floor.conn, floor.code).write(tmp_path / "nested" / "sold.csv")
    assert target.exists()
    assert target.read_text().startswith("sale,run,vin")


def test_the_export_registry() -> None:
    assert "sold" in available()
    assert "ledger" in available()
    assert named("sold") is sold_units
    with pytest.raises(ValidationError, match="is not an export"):
        named("everything")
