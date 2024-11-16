import pytest

from autozest.block.roster import REQUIRED, Role, RosterService, Staff
from autozest.errors import Conflict, NotFound, PolicyViolation
from tests.conftest import Floor


@pytest.fixture()
def roster(floor: Floor) -> RosterService:
    service = RosterService(floor.conn)
    service.add(Staff(id="s-1", name="Della Voss", role=Role.AUCTIONEER))
    service.add(Staff(id="s-2", name="Ray Okafor", role=Role.RING))
    service.add(Staff(id="s-3", name="Jo Hale", role=Role.RING))
    service.add(Staff(id="s-4", name="Pat Dunne", role=Role.TITLE))
    return service


def test_people_come_back_sorted_and_can_be_narrowed(roster: RosterService) -> None:
    assert [p.name for p in roster.people(Role.RING)] == ["Jo Hale", "Ray Okafor"]
    assert len(roster.people()) == 4


def test_two_people_cannot_share_a_name_in_a_role(roster: RosterService) -> None:
    with pytest.raises(Conflict):
        roster.add(Staff(id="s-9", name="Della Voss", role=Role.AUCTIONEER))
    roster.add(Staff(id="s-9", name="Della Voss", role=Role.CLERK))


def test_an_unknown_person(roster: RosterService) -> None:
    with pytest.raises(NotFound):
        roster.person("s-9")


def test_assigning_a_lane(floor: Floor, roster: RosterService) -> None:
    roster.assign(floor.code, "A", "s-1")
    roster.assign(floor.code, "A", "s-2")
    crew = roster.on_lane(floor.code, "A")
    assert {member.staff_id for member in crew} == {"s-1", "s-2"}


def test_a_lane_worker_cannot_be_on_two_lanes(floor: Floor, roster: RosterService) -> None:
    roster.assign(floor.code, "A", "s-1")
    with pytest.raises(PolicyViolation) as caught:
        roster.assign(floor.code, "B", "s-1")
    assert caught.value.rule == "roster.double_booked"


def test_somebody_who_does_not_work_a_lane_can_be_anywhere(
    floor: Floor, roster: RosterService
) -> None:
    assert not Role.TITLE.works_a_lane
    roster.assign(floor.code, "A", "s-4")
    roster.assign(floor.code, "B", "s-4")


def test_a_retired_person_is_not_assigned(floor: Floor, roster: RosterService) -> None:
    roster.retire("s-1")
    with pytest.raises(PolicyViolation) as caught:
        roster.assign(floor.code, "A", "s-1")
    assert caught.value.rule == "roster.retired"


def test_a_lane_needs_an_auctioneer_and_a_ring_man(floor: Floor, roster: RosterService) -> None:
    assert set(REQUIRED) == {Role.AUCTIONEER, Role.RING}
    gaps = roster.gaps(floor.code)
    assert gaps["A"] == ["1 auctioneer", "1 ring"]
    roster.assign(floor.code, "A", "s-1")
    assert roster.gaps(floor.code)["A"] == ["1 ring"]
    roster.assign(floor.code, "A", "s-2")
    assert "A" not in roster.gaps(floor.code)


def test_the_sale_is_not_staffed_until_every_lane_is(
    floor: Floor, roster: RosterService
) -> None:
    roster.assign(floor.code, "A", "s-1")
    roster.assign(floor.code, "A", "s-2")
    with pytest.raises(PolicyViolation) as caught:
        roster.require_staffed(floor.code)
    assert caught.value.rule == "roster.short"
    assert "lane B needs" in str(caught.value)


def test_a_fully_staffed_sale_passes(floor: Floor, roster: RosterService) -> None:
    roster.assign(floor.code, "A", "s-1")
    roster.assign(floor.code, "A", "s-2")
    roster.add(Staff(id="s-5", name="Mel Byrne", role=Role.AUCTIONEER))
    roster.assign(floor.code, "B", "s-5")
    roster.assign(floor.code, "B", "s-3")
    roster.require_staffed(floor.code)
    assert roster.duplicate_check(floor.code) == []


def test_unassigning_puts_the_gap_back(floor: Floor, roster: RosterService) -> None:
    roster.assign(floor.code, "A", "s-1")
    roster.unassign(floor.code, "A", "s-1")
    assert roster.gaps(floor.code)["A"] == ["1 auctioneer", "1 ring"]


def test_the_same_person_is_not_assigned_to_a_lane_twice(
    floor: Floor, roster: RosterService
) -> None:
    roster.assign(floor.code, "A", "s-1")
    with pytest.raises(Conflict):
        roster.assign(floor.code, "A", "s-1")
