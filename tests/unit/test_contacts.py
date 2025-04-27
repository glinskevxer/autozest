import sqlite3
from datetime import date

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.contacts import DEFAULT_CHANNEL, ContactBook
from autozest.errors import NotFound, ValidationError
from autozest.notices import Channel


@pytest.fixture()
def book(db: sqlite3.Connection) -> ContactBook:
    AccountRepository(db).add(
        Account(
            id="d-200",
            name="Kestrel Auto",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-2",
            state="TX",
        )
    )
    return ContactBook(db)


def test_nothing_on_file_falls_back_to_post(book: ContactBook) -> None:
    routing = book.route("d-200", "payment.due")
    assert routing.channel is DEFAULT_CHANNEL
    assert routing.channel is Channel.POST
    assert routing.is_default
    assert "nothing on file" in routing.reason


def test_a_preference_is_used(book: ContactBook) -> None:
    book.set_preference(
        "d-200",
        "payment.due",
        Channel.EMAIL,
        "ap@kestrel.example",
        verified_on=date(2024, 1, 1),
    )
    routing = book.route("d-200", "payment.due")
    assert routing.channel is Channel.EMAIL
    assert routing.address == "ap@kestrel.example"
    assert routing.reason == "as asked for"


def test_a_preference_needs_an_address(book: ContactBook) -> None:
    with pytest.raises(ValidationError, match="needs an address"):
        book.set_preference("d-200", "payment.due", Channel.EMAIL, "  ")


def test_an_unknown_preference(book: ContactBook) -> None:
    with pytest.raises(NotFound):
        book.get("d-200", "payment.due")
    assert book.find("d-200", "payment.due") is None


def test_an_unverified_address_falls_back_for_a_clock_starter(
    book: ContactBook,
) -> None:
    book.set_preference("d-200", "claim.decided", Channel.EMAIL, "ap@kestrel.example")
    routing = book.route("d-200", "claim.decided")
    assert routing.channel is Channel.POST
    assert "starts a clock" in routing.reason


def test_an_unverified_address_is_fine_for_anything_else(book: ContactBook) -> None:
    book.set_preference("d-200", "title.chase", Channel.EMAIL, "ap@kestrel.example")
    assert book.route("d-200", "title.chase").channel is Channel.EMAIL


def test_verifying_an_address_lets_the_clock_letters_through(
    book: ContactBook,
) -> None:
    book.set_preference("d-200", "claim.decided", Channel.EMAIL, "ap@kestrel.example")
    book.verify("d-200", "claim.decided")
    routing = book.route("d-200", "claim.decided")
    assert routing.channel is Channel.EMAIL
    assert book.get("d-200", "claim.decided").is_verified


def test_a_preference_can_be_moved(book: ContactBook) -> None:
    book.set_preference("d-200", "payment.due", Channel.EMAIL, "one@kestrel.example")
    book.set_preference("d-200", "payment.due", Channel.SMS, "0100 900 100")
    assert book.get("d-200", "payment.due").channel is Channel.SMS
    assert len(book.for_party("d-200")) == 1


def test_different_kinds_go_different_ways(book: ContactBook) -> None:
    book.set_preference(
        "d-200",
        "payment.due",
        Channel.EMAIL,
        "ap@kestrel.example",
        verified_on=date(2024, 1, 1),
    )
    book.set_preference(
        "d-200", "claim.decided", Channel.SMS, "0100 900 100", verified_on=date(2024, 1, 1)
    )
    assert book.route("d-200", "payment.due").channel is Channel.EMAIL
    assert book.route("d-200", "claim.decided").channel is Channel.SMS
    assert len(book.for_party("d-200")) == 2


def test_the_unverified_chase_list(book: ContactBook) -> None:
    book.set_preference("d-200", "payment.due", Channel.EMAIL, "ap@kestrel.example")
    book.set_preference("d-200", "title.chase", Channel.POST, "14 Cotton Lane")
    assert [item.kind for item in book.unverified()] == ["payment.due"]
    book.verify("d-200", "payment.due")
    assert book.unverified() == []


def test_post_never_needs_verifying(book: ContactBook) -> None:
    book.set_preference("d-200", "claim.decided", Channel.POST, "14 Cotton Lane")
    assert book.route("d-200", "claim.decided").channel is Channel.POST
    assert book.unverified() == []
