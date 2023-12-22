import sqlite3

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository, AccountStatus
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError


def dealer(account_id: str = "d-100", **kwargs: object) -> Account:
    defaults = {
        "name": "Ridgeline Motors",
        "account_class": AccountClass.INDEPENDENT_DEALER,
        "dealer_licence": "TX-44192",
        "state": "TX",
    }
    defaults.update(kwargs)
    return Account(id=account_id, **defaults)  # type: ignore[arg-type]


@pytest.fixture()
def repo(db: sqlite3.Connection) -> AccountRepository:
    return AccountRepository(db)


def test_a_public_account_may_bid_and_may_not_consign() -> None:
    assert AccountClass.PUBLIC.may_bid
    assert not AccountClass.PUBLIC.may_consign


def test_a_finance_account_consigns_repossessions_and_does_not_bid() -> None:
    assert AccountClass.FINANCE.may_consign
    assert not AccountClass.FINANCE.may_bid


def test_only_an_active_account_trades() -> None:
    assert AccountStatus.ACTIVE.may_trade
    assert not AccountStatus.SUSPENDED.may_trade
    assert not AccountStatus.CLOSED.may_trade


def test_a_dealer_can_do_both_sides(repo: AccountRepository) -> None:
    account = repo.add(dealer())
    account.require_can_consign()
    account.require_can_bid()


def test_a_public_account_is_told_which_rule_stopped_it() -> None:
    account = Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC)
    with pytest.raises(PolicyViolation) as caught:
        account.require_can_consign()
    assert caught.value.rule == "account.consign"
    account.require_can_bid()


def test_a_finance_account_cannot_bid() -> None:
    account = dealer(account_class=AccountClass.FINANCE)
    with pytest.raises(PolicyViolation) as caught:
        account.require_can_bid()
    assert caught.value.rule == "account.bid"


def test_suspension_stops_both_sides_before_the_class_is_even_looked_at() -> None:
    account = dealer(status=AccountStatus.SUSPENDED)
    with pytest.raises(StateError, match="is suspended"):
        account.require_can_bid()
    with pytest.raises(StateError):
        account.require_can_consign()


def test_a_licensed_class_needs_a_licence_on_file(repo: AccountRepository) -> None:
    with pytest.raises(PolicyViolation) as caught:
        repo.add(dealer(dealer_licence=None))
    assert caught.value.rule == "account.licence"


def test_a_public_account_needs_no_licence(repo: AccountRepository) -> None:
    repo.add(Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC))
    assert repo.get("p-1").dealer_licence is None


def test_round_trip_through_the_database(repo: AccountRepository) -> None:
    repo.add(dealer())
    found = repo.get("d-100")
    assert found.name == "Ridgeline Motors"
    assert found.account_class is AccountClass.INDEPENDENT_DEALER
    assert found.status is AccountStatus.ACTIVE
    assert found.state == "TX"


def test_an_unknown_account_raises_not_found(repo: AccountRepository) -> None:
    with pytest.raises(NotFound) as caught:
        repo.get("nope")
    assert caught.value.kind == "account"


def test_the_same_name_in_two_states_is_fine_and_a_repeat_is_not(
    repo: AccountRepository,
) -> None:
    repo.add(dealer("d-1", state="TX"))
    repo.add(dealer("d-2", state="OK"))
    with pytest.raises(Conflict, match="already exists"):
        repo.add(dealer("d-3", state="TX"))


def test_find_narrows_by_state_when_asked(repo: AccountRepository) -> None:
    repo.add(dealer("d-1", state="TX"))
    repo.add(dealer("d-2", state="OK"))
    assert repo.find("Ridgeline Motors", "OK") is not None
    assert repo.find("Ridgeline Motors", "OK").id == "d-2"  # type: ignore[union-attr]
    assert repo.find("Nobody Motors") is None


def test_listing_by_class_is_sorted_by_name(repo: AccountRepository) -> None:
    repo.add(dealer("d-1", name="Zenith Auto", state="TX"))
    repo.add(dealer("d-2", name="Apex Auto", state="TX"))
    repo.add(dealer("f-1", name="Fleet Co", account_class=AccountClass.FLEET_LEASE, state="TX"))
    names = [a.name for a in repo.of_class(AccountClass.INDEPENDENT_DEALER)]
    assert names == ["Apex Auto", "Zenith Auto"]


def test_suspending_and_reinstating(repo: AccountRepository) -> None:
    repo.add(dealer())
    assert repo.set_status("d-100", AccountStatus.SUSPENDED).status is AccountStatus.SUSPENDED
    assert repo.set_status("d-100", AccountStatus.ACTIVE).status is AccountStatus.ACTIVE


def test_a_closed_account_does_not_come_back(repo: AccountRepository) -> None:
    repo.add(dealer())
    repo.set_status("d-100", AccountStatus.CLOSED)
    with pytest.raises(StateError, match="is closed"):
        repo.set_status("d-100", AccountStatus.ACTIVE)


def test_counting(repo: AccountRepository) -> None:
    assert repo.count() == 0
    repo.add(dealer("d-1", state="TX"))
    repo.add(dealer("d-2", state="OK"))
    assert repo.count() == 2
