from autozest.block.bidding import BiddingService
from autozest.inspection.announcements import AnnouncementCode
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.reporting import disclosure
from autozest.title.branding import TitleBrand
from autozest.title.service import TitleService
from tests.conftest import Floor


def sold(floor: Floor, announce: AnnouncementCode | None = None) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    consignment_id = floor.sales.run(run_id).consignment_id
    if announce is not None:
        InspectionService(floor.conn).add_announcement(consignment_id, announce)
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    return consignment_id


def titled(floor: Floor, consignment_id: str, brand: TitleBrand) -> None:
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id, brand=brand)


def test_a_sale_with_nothing_sold(floor: Floor) -> None:
    floor.catalogue()
    floor.sales.open_sale(floor.code)
    report = disclosure.audit(floor.conn, floor.code)
    assert report.checked == 0
    assert report.is_clean


def test_a_clean_unit_with_nothing_called_is_clean(floor: Floor) -> None:
    consignment_id = sold(floor)
    titled(floor, consignment_id, TitleBrand.CLEAN)
    report = disclosure.audit(floor.conn, floor.code)
    assert report.checked == 1
    assert report.clean == 1
    assert report.is_clean


def test_a_brand_nobody_called_is_a_miss(floor: Floor) -> None:
    consignment_id = sold(floor)
    titled(floor, consignment_id, TitleBrand.SALVAGE)
    report = disclosure.audit(floor.conn, floor.code)
    assert len(report.misses) == 1
    assert "nothing was called" in report.misses[0].detail
    assert not report.is_clean


def test_a_brand_that_was_called_is_not_a_miss(floor: Floor) -> None:
    consignment_id = sold(floor, AnnouncementCode.SALVAGE_TITLE)
    titled(floor, consignment_id, TitleBrand.SALVAGE)
    assert disclosure.audit(floor.conn, floor.code).is_clean


def test_a_brand_called_on_a_clean_title_is_an_overcall(floor: Floor) -> None:
    consignment_id = sold(floor, AnnouncementCode.SALVAGE_TITLE)
    titled(floor, consignment_id, TitleBrand.CLEAN)
    report = disclosure.audit(floor.conn, floor.code)
    assert len(report.overcalls) == 1
    assert "the title is clean" in report.overcalls[0].detail


def test_a_unit_with_no_title_yet_counts_as_unverified(floor: Floor) -> None:
    sold(floor, AnnouncementCode.SALVAGE_TITLE)
    report = disclosure.audit(floor.conn, floor.code)
    assert report.unverified == 1
    assert report.checked == 0


def test_a_clean_unit_with_no_title_yet_is_not_counted(floor: Floor) -> None:
    sold(floor)
    report = disclosure.audit(floor.conn, floor.code)
    assert report.unverified == 0


def test_the_summary_reads(floor: Floor) -> None:
    consignment_id = sold(floor)
    titled(floor, consignment_id, TitleBrand.SALVAGE)
    printed = "\n".join(disclosure.render(floor.conn, floor.code))
    assert "disclosure audit" in printed
    assert "1 missed" in printed
    assert "needs looking at" in printed
