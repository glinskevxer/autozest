import pytest

from autozest.block.bidding import BiddingService
from autozest.catalog.groups import GroupService
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.money import Money
from tests.conftest import Floor


@pytest.fixture()
def groups(floor: Floor) -> GroupService:
    return GroupService(floor.conn)


def test_creating_a_group(groups: GroupService) -> None:
    group = groups.create("g-1", "Ridgeline Group")
    assert group.name == "Ridgeline Group"
    assert not group.shares_credit
    assert groups.members("g-1") == []


def test_an_unknown_group(groups: GroupService) -> None:
    with pytest.raises(NotFound):
        groups.get("g-9")


def test_two_groups_cannot_share_a_name(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    with pytest.raises(Conflict):
        groups.create("g-2", "Ridgeline Group")


def test_members_and_siblings(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-200")
    assert groups.members("g-1") == ["d-100", "d-200"]
    assert groups.siblings("d-100") == ["d-200"]
    assert groups.siblings("d-300") == []


def test_an_account_belongs_to_one_group(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.create("g-2", "Kestrel Group")
    groups.add("g-1", "d-100")
    with pytest.raises(Conflict, match="already in a group"):
        groups.add("g-2", "d-100")


def test_adding_the_same_account_twice_is_harmless(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-100")
    assert groups.members("g-1") == ["d-100"]


def test_leaving_a_group_frees_the_account(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.create("g-2", "Kestrel Group")
    groups.add("g-1", "d-100")
    groups.remove("g-1", "d-100")
    assert groups.group_of("d-100") is None
    groups.add("g-2", "d-100")


def test_two_accounts_in_one_group_are_related(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-200")
    assert groups.related("d-100", "d-200")
    assert groups.related("d-100", "d-100")
    assert not groups.related("d-100", "d-300")


def test_ungrouped_accounts_are_only_related_to_themselves(groups: GroupService) -> None:
    assert groups.related("d-100", "d-100")
    assert not groups.related("d-100", "d-200")


def test_credit_pools_only_when_the_group_says_so(groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-200")
    assert groups.credit_pool("d-100") == ["d-100"]
    groups.create("g-2", "Kestrel Group", shares_credit=True)
    groups.add("g-2", "d-300")
    assert groups.credit_pool("d-300") == ["d-300"]


def test_a_group_mate_cannot_bid_on_the_units(floor: Floor, groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-200")
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    assert caught.value.rule == "bid.own_unit"
    assert caught.value.context["seller"] == "d-100"


def test_somebody_outside_the_group_still_bids(floor: Floor, groups: GroupService) -> None:
    groups.create("g-1", "Ridgeline Group")
    groups.add("g-1", "d-100")
    groups.add("g-1", "d-200")
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-300", Money.parse("5200.00"))
    assert bidding.fall(run_id).buyer_id == "d-300"


def test_listing_the_groups(groups: GroupService) -> None:
    groups.create("g-2", "Zephyr Group")
    groups.create("g-1", "Apex Group")
    assert [g.name for g in groups.groups()] == ["Apex Group", "Zephyr Group"]
