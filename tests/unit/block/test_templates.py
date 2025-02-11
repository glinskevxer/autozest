from datetime import date

import pytest

from autozest.block.templates import TemplateService
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from tests.conftest import Floor

TUESDAY = date(2024, 3, 19)
THURSDAY = date(2024, 3, 21)


@pytest.fixture()
def templates(floor: Floor) -> TemplateService:
    service = TemplateService(floor.conn)
    service.create("t-1", "Tuesday dealer", 1, "Tuesday dealer sale", {"A": 120, "B": 120})
    return service


def test_a_template_describes_itself(templates: TemplateService) -> None:
    template = templates.get("t-1")
    assert template.day_name == "Tuesday"
    assert template.capacity == 240
    assert "every Tuesday, A, B, 240 slots" in template.describe()


def test_a_template_with_no_lanes(templates: TemplateService) -> None:
    with pytest.raises(ValidationError, match="builds nothing"):
        templates.create("t-2", "Empty", 1, "Empty", {})


def test_a_template_needs_a_title(templates: TemplateService) -> None:
    with pytest.raises(ValidationError, match="needs a title"):
        templates.create("t-2", "Bad", 1, "  ", {"A": 10})


def test_a_weekday_that_is_not_one(templates: TemplateService) -> None:
    with pytest.raises(ValidationError, match="not a weekday"):
        templates.create("t-2", "Bad", 9, "Title", {"A": 10})


def test_two_templates_cannot_share_a_name(templates: TemplateService) -> None:
    with pytest.raises(Conflict):
        templates.create("t-2", "Tuesday dealer", 3, "Another", {"A": 10})


def test_an_unknown_template(templates: TemplateService) -> None:
    with pytest.raises(NotFound):
        templates.get("t-9")


def test_the_next_occurrence_is_strictly_after(templates: TemplateService) -> None:
    template = templates.get("t-1")
    assert template.next_after(date(2024, 3, 18)) == TUESDAY
    assert template.next_after(TUESDAY) == date(2024, 3, 26)


def test_building_a_sale_from_a_template(floor: Floor, templates: TemplateService) -> None:
    sale = templates.build("t-1", TUESDAY)
    assert sale.title == "Tuesday dealer sale"
    assert [lane.letter for lane in floor.sales.lanes(sale.code)] == ["A", "B"]


def test_building_on_the_wrong_day_is_a_typo(templates: TemplateService) -> None:
    with pytest.raises(PolicyViolation) as caught:
        templates.build("t-1", THURSDAY)
    assert caught.value.rule == "template.wrong_day"
    assert "runs on a Tuesday" in str(caught.value)


def test_building_the_next_one(floor: Floor, templates: TemplateService) -> None:
    sale = templates.build_next("t-1", after=date(2024, 3, 18))
    assert sale.sale_date == TUESDAY


def test_a_retired_template_builds_nothing(templates: TemplateService) -> None:
    templates.retire("t-1")
    with pytest.raises(PolicyViolation) as caught:
        templates.build("t-1", TUESDAY)
    assert caught.value.rule == "template.retired"


def test_a_retired_template_drops_off_the_list(templates: TemplateService) -> None:
    templates.create("t-2", "Thursday fleet", 3, "Thursday fleet sale", {"C": 80})
    assert len(templates.templates()) == 2
    templates.retire("t-1")
    assert [t.id for t in templates.templates()] == ["t-2"]
    assert len(templates.templates(active_only=False)) == 2


def test_the_upcoming_list(templates: TemplateService) -> None:
    templates.create("t-2", "Thursday fleet", 3, "Thursday fleet sale", {"C": 80})
    upcoming = templates.upcoming(after=date(2024, 3, 18))
    assert [when for _, when in upcoming] == [TUESDAY, THURSDAY]


def test_nothing_is_built_on_its_own(floor: Floor, templates: TemplateService) -> None:
    """A sale appearing because a template existed is one nobody decided to hold."""
    templates.upcoming(after=date(2024, 3, 18))
    assert floor.sales.on(TUESDAY) is None
