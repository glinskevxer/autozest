from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.reporting import dashboard
from autozest.settlement.postings import PostingService
from tests.conftest import Floor


def sold(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    return run_id


def test_an_empty_building_is_quiet(floor: Floor) -> None:
    board = dashboard.build(floor.conn)
    assert board.is_quiet
    assert board.gross == Money.zero()
    assert board.sale_code is None


def test_a_sale_fills_in_the_top_section(floor: Floor) -> None:
    sold(floor)
    board = dashboard.build(floor.conn, floor.code)
    assert board.sold == 1
    assert board.gross == Money.parse("8200.00")
    assert board.sell_through == "100.0%"


def test_the_money_section_follows_the_ledger(floor: Floor) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    board = dashboard.build(floor.conn, floor.code)
    assert board.owed_to_us == Money.parse("8545.00")
    assert board.owed_by_us == Money.parse("7965.00")
    assert board.cash == Money.zero()


def test_a_claim_shows_on_the_desks_section(floor: Floor) -> None:
    from datetime import datetime, timedelta

    from autozest.arbitration.desk import ArbitrationDesk
    from autozest.defects import DefectClass

    run_id = sold(floor)
    sold_at = datetime(2024, 3, 12, 14, 30)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (sold_at.isoformat(), run_id))
    ArbitrationDesk(floor.conn).open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=sold_at + timedelta(hours=1)
    )
    board = dashboard.build(floor.conn, floor.code)
    assert board.open_claims == 1


def test_the_rendered_screen_has_all_three_headings(floor: Floor) -> None:
    sold(floor)
    printed = "\n".join(dashboard.render(floor.conn, floor.code))
    assert "the sale" in printed
    assert "the money" in printed
    assert "the desks" in printed
    assert "claim turnaround" in printed


def test_the_screen_says_whether_anybody_has_to_do_something(floor: Floor) -> None:
    printed = "\n".join(dashboard.render(floor.conn))
    assert "nothing needs anybody" in printed


def test_the_aged_detail_is_reachable_from_here(floor: Floor) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    aged = dashboard.aged_side(floor.conn)
    assert aged.total == Money.parse("8545.00")
