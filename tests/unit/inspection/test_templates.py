import sqlite3

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.inspection.damage import Panel
from autozest.inspection.templates import Prompt, TemplateBook


@pytest.fixture()
def book(db: sqlite3.Connection) -> TemplateBook:
    AccountRepository(db).add(
        Account(
            id="f-100",
            name="Halloway Fleet",
            account_class=AccountClass.FLEET_LEASE,
            dealer_licence="TX-9",
            state="TX",
        )
    )
    return TemplateBook(db)


HOUSE = [
    Prompt(1, "walk the panels"),
    Prompt(2, "start it and listen", required=True),
    Prompt(3, "photograph the interior", required=False),
]

FLEET = [
    Prompt(1, "tyre depth on all four", panel=Panel.TYRE),
    Prompt(2, "underbody for rust", panel=Panel.UNDERBODY),
]


def test_a_template_with_no_checks(book: TemplateBook) -> None:
    with pytest.raises(ValidationError, match="asks for nothing"):
        book.create("t-1", "Empty", [])


def test_a_house_template_describes_itself(book: TemplateBook) -> None:
    template = book.create("t-house", "House walkaround", HOUSE)
    assert template.is_house
    assert "3 checks, 2 required" in template.describe()
    assert "(house)" in template.describe()


def test_prompts_come_back_in_walking_order(book: TemplateBook) -> None:
    template = book.create("t-house", "House walkaround", list(reversed(HOUSE)))
    assert [prompt.ordinal for prompt in template.prompts] == [1, 2, 3]
    assert "*  2. start it and listen" in str(template.prompts[1])
    assert "   3." in str(template.prompts[2])


def test_a_panel_shows_on_the_prompt(book: TemplateBook) -> None:
    template = book.create("t-f", "Fleet", FLEET, seller_id="f-100")
    assert "[tyre]" in str(template.prompts[0])


def test_a_seller_gets_their_own_over_the_house_one(book: TemplateBook) -> None:
    book.create("t-house", "House walkaround", HOUSE)
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    found = book.for_seller("f-100")
    assert found is not None and found.id == "t-f"


def test_a_seller_with_none_gets_the_house_one(book: TemplateBook) -> None:
    book.create("t-house", "House walkaround", HOUSE)
    found = book.for_seller("d-999")
    assert found is not None and found.is_house


def test_no_template_at_all_asks_for_nothing(book: TemplateBook) -> None:
    assert book.for_seller("f-100") is None
    book.require_covered("f-100", set())


def test_an_unknown_template(book: TemplateBook) -> None:
    with pytest.raises(NotFound):
        book.get("t-9")


def test_a_seller_cannot_have_two_templates_of_a_name(book: TemplateBook) -> None:
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    with pytest.raises(Conflict):
        book.create("t-f2", "Halloway contract", FLEET, seller_id="f-100")


def test_the_required_checks_have_to_be_answered(book: TemplateBook) -> None:
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    with pytest.raises(PolicyViolation) as caught:
        book.require_covered("f-100", {1})
    assert caught.value.rule == "inspection.template"
    assert "underbody for rust" in str(caught.value)
    assert caught.value.context["missing"] == 1


def test_answering_everything_required_passes(book: TemplateBook) -> None:
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    book.require_covered("f-100", {1, 2})


def test_an_optional_check_is_not_required(book: TemplateBook) -> None:
    book.create("t-house", "House walkaround", HOUSE)
    book.require_covered("d-999", {1, 2})


def test_a_retired_template_stops_applying(book: TemplateBook) -> None:
    book.create("t-house", "House walkaround", HOUSE)
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    book.retire("t-f")
    found = book.for_seller("f-100")
    assert found is not None and found.is_house


def test_listing_the_live_templates(book: TemplateBook) -> None:
    book.create("t-house", "House walkaround", HOUSE)
    book.create("t-f", "Halloway contract", FLEET, seller_id="f-100")
    assert len(book.templates()) == 2
    book.retire("t-house")
    assert len(book.templates()) == 1
