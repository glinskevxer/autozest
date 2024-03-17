from decimal import Decimal

import pytest

from autozest.block.light import assign, would_be_green
from autozest.catalog.accounts import AccountClass
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.money import Money

CLEAN = Decimal("4.2")
DEAR = Money.parse("12000.00")


def test_a_clean_unit_with_nothing_called_runs_green() -> None:
    decision = assign(announcements=set(), grade=CLEAN, floor=DEAR)
    assert decision.light is Light.GREEN
    assert decision.reasons == ()
    assert "nothing against it" in decision.explain()


def test_an_announcement_forces_the_light() -> None:
    decision = assign(announcements={AnnouncementCode.FRAME_DAMAGE}, grade=CLEAN, floor=DEAR)
    assert decision.light is Light.YELLOW
    assert decision.was_forced


def test_as_is_forces_red_over_a_clean_grade() -> None:
    decision = assign(announcements={AnnouncementCode.AS_IS}, grade=CLEAN, floor=DEAR)
    assert decision.light is Light.RED


def test_a_rough_grade_runs_red_with_nothing_announced() -> None:
    decision = assign(announcements=set(), grade=Decimal("1.8"), floor=DEAR)
    assert decision.light is Light.RED
    assert "grade 1.8" in decision.explain()


def test_a_middling_grade_runs_yellow() -> None:
    assert assign(announcements=set(), grade=Decimal("2.8"), floor=DEAR).light is Light.YELLOW


def test_the_grade_boundary_is_inclusive_at_the_bottom() -> None:
    assert assign(announcements=set(), grade=Decimal("2.0"), floor=DEAR).light is Light.RED
    assert assign(announcements=set(), grade=Decimal("2.1"), floor=DEAR).light is Light.YELLOW
    assert assign(announcements=set(), grade=Decimal("3.0"), floor=DEAR).light is Light.YELLOW
    assert assign(announcements=set(), grade=Decimal("3.1"), floor=DEAR).light is Light.GREEN


def test_a_cheap_unit_runs_red_however_clean_it_is() -> None:
    decision = assign(announcements=set(), grade=Decimal("4.8"), floor=Money.parse("1200.00"))
    assert decision.light is Light.RED
    assert "floor under" in decision.explain()


def test_the_price_band_boundary() -> None:
    assert (
        assign(announcements=set(), grade=CLEAN, floor=Money.parse("2999.99")).light
        is Light.RED
    )
    assert (
        assign(announcements=set(), grade=CLEAN, floor=Money.parse("3000.00")).light
        is Light.GREEN
    )


def test_a_missing_grade_and_floor_simply_do_not_push() -> None:
    assert assign(announcements=set()).light is Light.GREEN


def test_a_repossession_never_does_better_than_yellow() -> None:
    decision = assign(
        announcements=set(), grade=CLEAN, floor=DEAR, seller_class=AccountClass.FINANCE
    )
    assert decision.light is Light.YELLOW
    assert "finance seller" in decision.explain()


def test_a_factory_unit_is_pulled_up_out_of_its_grade() -> None:
    decision = assign(
        announcements=set(), grade=Decimal("2.4"), floor=DEAR, seller_class=AccountClass.FACTORY
    )
    assert decision.light is Light.GREEN


def test_a_factory_unit_is_not_pulled_up_past_an_announcement() -> None:
    decision = assign(
        announcements={AnnouncementCode.FRAME_DAMAGE},
        grade=Decimal("4.5"),
        floor=DEAR,
        seller_class=AccountClass.FACTORY,
    )
    assert decision.light is Light.YELLOW


def test_a_guarantee_buys_back_the_grade() -> None:
    decision = assign(announcements=set(), grade=Decimal("1.8"), floor=DEAR, guaranteed=True)
    assert decision.light is Light.GREEN
    assert "the grade" in decision.relaxed


def test_a_guarantee_buys_back_the_price_band() -> None:
    decision = assign(
        announcements=set(), grade=CLEAN, floor=Money.parse("1200.00"), guaranteed=True
    )
    assert decision.light is Light.GREEN
    assert "the price band" in decision.relaxed


def test_a_guarantee_does_nothing_about_a_disclosure() -> None:
    decision = assign(
        announcements={AnnouncementCode.FRAME_DAMAGE},
        grade=Decimal("1.8"),
        floor=Money.parse("900.00"),
        guaranteed=True,
    )
    assert decision.light is Light.YELLOW
    assert decision.relaxed == ("the grade", "the price band")
    assert decision.was_forced


def test_a_guarantee_cannot_lift_a_finance_seller_either() -> None:
    decision = assign(
        announcements=set(),
        grade=CLEAN,
        floor=DEAR,
        seller_class=AccountClass.FINANCE,
        guaranteed=True,
    )
    assert decision.light is Light.YELLOW


def test_the_most_restrictive_push_wins_when_several_apply() -> None:
    decision = assign(
        announcements={AnnouncementCode.TITLE_ABSENT},
        grade=Decimal("1.5"),
        floor=DEAR,
    )
    assert decision.light is Light.RED
    assert len(decision.reasons) == 2


def test_the_explanation_lists_every_reason() -> None:
    decision = assign(
        announcements={AnnouncementCode.TITLE_ABSENT},
        grade=Decimal("2.5"),
        floor=Money.parse("1000.00"),
    )
    text = decision.explain()
    assert "announced blue" in text
    assert "grade 2.5" in text
    assert "floor under" in text


@pytest.mark.parametrize(
    ("grade", "floor", "green"),
    [
        (Decimal("4.2"), "12000.00", True),
        (Decimal("2.5"), "12000.00", False),
        (Decimal("4.2"), "900.00", False),
    ],
)
def test_would_be_green_answers_the_sellers_question(
    grade: Decimal, floor: str, green: bool
) -> None:
    assert would_be_green(set(), grade, Money.parse(floor)) is green
