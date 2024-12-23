from datetime import date

import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.reconcile import MatchKind, Reconciler, parse
from autozest.settlement.statements import StatementService
from tests.conftest import Floor

HEADER = "date,amount,reference,detail\n"


def owing(floor: Floor, buyer: str = "d-200", price: str = "8200.00") -> None:
    """One unit sold to one buyer, posted, so the buyer owes."""
    owing_many(floor, [(buyer, price)])


def owing_many(floor: Floor, sales: list[tuple[str, str]]) -> None:
    """Several units in one go, catalogued before the sale opens."""
    run_ids = [floor.catalogue(floor_price="3000.00") for _ in sales]
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    posting = PostingService(floor.conn)
    for run_id, (buyer, price) in zip(run_ids, sales, strict=True):
        bidding.bid(run_id, buyer, Money.parse(price))
        bidding.fall(run_id)
        posting.post_sale(run_id)


def test_a_file_missing_a_column() -> None:
    with pytest.raises(ValidationError, match="missing amount"):
        parse("date,reference\n2024-03-14,x\n")
    with pytest.raises(ValidationError, match="is empty"):
        parse("")


def test_a_file_reads_into_lines() -> None:
    lines = parse(HEADER + "2024-03-14,8545.00,WIRE-40182,Kestrel Auto\n")
    assert len(lines) == 1
    assert lines[0].amount == Money.parse("8545.00")
    assert lines[0].on == date(2024, 3, 14)
    assert lines[0].detail == "Kestrel Auto"
    assert lines[0].line == 2


def test_a_reference_we_already_know_matches_hardest(floor: Floor) -> None:
    owing(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("100.00"), reference="WIRE-40182")
    lines = parse(HEADER + "2024-03-14,500.00,WIRE-40182,\n")
    match = Reconciler(floor.conn).match(lines).matches[0]
    assert match.kind is MatchKind.REFERENCE
    assert match.party_id == "d-200"


def test_an_exact_balance_matches_on_the_amount(floor: Floor) -> None:
    owing(floor)
    lines = parse(HEADER + "2024-03-14,8545.00,SOMETHING ELSE,\n")
    match = Reconciler(floor.conn).match(lines).matches[0]
    assert match.party_id == "d-200"
    assert match.kind is MatchKind.AMOUNT_AND_DATE


def test_an_amount_nobody_owes_is_left_alone(floor: Floor) -> None:
    owing(floor)
    lines = parse(HEADER + "2024-03-14,77.00,MYSTERY,\n")
    match = Reconciler(floor.conn).match(lines).matches[0]
    assert match.kind is MatchKind.NONE
    assert match.needs_a_person


def test_two_buyers_owing_the_same_is_ambiguous(floor: Floor) -> None:
    owing_many(floor, [("d-200", "8200.00"), ("d-300", "8200.00")])
    lines = parse(HEADER + "2024-03-14,8545.00,MYSTERY,\n")
    match = Reconciler(floor.conn).match(lines).matches[0]
    assert match.kind is MatchKind.AMBIGUOUS
    assert set(match.candidates) == {"d-200", "d-300"}
    assert match.needs_a_person


def test_the_summary_counts_both_ways(floor: Floor) -> None:
    owing(floor)
    lines = parse(HEADER + "2024-03-14,8545.00,X,\n2024-03-14,77.00,Y,\n")
    result = Reconciler(floor.conn).match(lines)
    assert "2 lines, 1 matched, 1 for somebody" in result.summary()
    assert result.total == Money.parse("8622.00")


def test_applying_takes_only_the_confident_ones(floor: Floor) -> None:
    owing(floor)
    lines = parse(HEADER + "2024-03-14,8545.00,X,\n2024-03-14,77.00,Y,\n")
    reconciler = Reconciler(floor.conn)
    assert reconciler.apply(reconciler.match(lines)) == 1
    assert StatementService(floor.conn).invoice("d-200").balance == Money.zero()


def test_an_ambiguous_line_is_never_applied(floor: Floor) -> None:
    owing_many(floor, [("d-200", "8200.00"), ("d-300", "8200.00")])
    lines = parse(HEADER + "2024-03-14,8545.00,MYSTERY,\n")
    reconciler = Reconciler(floor.conn)
    assert reconciler.apply(reconciler.match(lines)) == 0
    assert StatementService(floor.conn).invoice("d-200").balance == Money.parse("8545.00")


def test_which_matches_are_confident() -> None:
    assert MatchKind.REFERENCE.is_confident
    assert MatchKind.AMOUNT_AND_DATE.is_confident
    assert not MatchKind.AMOUNT.is_confident
    assert not MatchKind.AMBIGUOUS.is_confident
    assert not MatchKind.NONE.is_confident
