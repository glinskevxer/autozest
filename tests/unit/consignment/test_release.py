import pytest

from autozest.block.bidding import BiddingService
from autozest.consignment.release import ReleaseService
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.title.service import TitleService
from tests.conftest import Floor


@pytest.fixture()
def gate(floor: Floor) -> ReleaseService:
    return ReleaseService(floor.conn)


def sold(floor: Floor) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    PostingService(floor.conn).post_sale(run_id)
    return floor.sales.run(run_id).consignment_id


def clear(floor: Floor, consignment_id: str) -> None:
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)


def test_logging_keys_in(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    keys = gate.take_keys(consignment_id, "T-0412", 2)
    assert keys.is_held
    assert keys.count == 2
    assert gate.by_tag("T-0412").consignment_id == consignment_id


def test_a_negative_count(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    with pytest.raises(ValidationError):
        gate.take_keys(consignment_id, "T-0412", -1)


def test_two_units_cannot_share_a_tag(floor: Floor, gate: ReleaseService) -> None:
    first = sold(floor)
    gate.take_keys(first, "T-0412", 2)
    second = floor.intake.register("JTDKN3DU8A0000001", "d-100").id
    with pytest.raises(Conflict):
        gate.take_keys(second, "T-0412", 2)


def test_keys_are_handed_over_once(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    gate.take_keys(consignment_id, "T-0412", 2)
    handed = gate.hand_over(consignment_id, "d-200")
    assert not handed.is_held
    assert handed.handed_to == "d-200"
    with pytest.raises(PolicyViolation) as caught:
        gate.hand_over(consignment_id, "d-300")
    assert caught.value.rule == "keys.already_handed"


def test_the_cabinet_list_empties(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    gate.take_keys(consignment_id, "T-0412", 2)
    assert len(gate.held()) == 1
    gate.hand_over(consignment_id, "d-200")
    assert gate.held() == []


def test_keys_that_were_never_logged(floor: Floor, gate: ReleaseService) -> None:
    with pytest.raises(NotFound):
        gate.keys("C-2024-999999")
    with pytest.raises(NotFound):
        gate.by_tag("T-9999")


def test_release_is_refused_while_anything_holds_it(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    with pytest.raises(PolicyViolation) as caught:
        gate.release(consignment_id, "d-200", "r.okafor")
    assert caught.value.rule == "release.held"
    assert caught.value.context["holds"] >= 1


def test_a_clear_unit_goes_out(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    clear(floor, consignment_id)
    released = gate.release(consignment_id, "d-200", "r.okafor")
    assert not released.was_overridden
    assert gate.find_release(consignment_id) is not None


def test_a_supervisor_can_override_and_it_is_written_down(
    floor: Floor, gate: ReleaseService
) -> None:
    consignment_id = sold(floor)
    released = gate.release(
        consignment_id, "d-200", "m.byrne", override="wire confirmed by the bank"
    )
    assert released.was_overridden
    assert "wire confirmed" in (released.holds_overridden or "")
    assert "still owes" in (released.holds_overridden or "")
    assert [r.id for r in gate.overrides()] == [released.id]


def test_a_unit_is_released_once(floor: Floor, gate: ReleaseService) -> None:
    consignment_id = sold(floor)
    clear(floor, consignment_id)
    gate.release(consignment_id, "d-200", "r.okafor")
    with pytest.raises(Conflict, match="already been released"):
        gate.release(consignment_id, "d-200", "r.okafor")


def test_a_clean_release_is_not_on_the_override_list(
    floor: Floor, gate: ReleaseService
) -> None:
    consignment_id = sold(floor)
    clear(floor, consignment_id)
    gate.release(consignment_id, "d-200", "r.okafor")
    assert gate.overrides() == []
