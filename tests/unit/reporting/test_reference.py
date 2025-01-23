from autozest.arbitration.policy import from_config
from autozest.reporting import reference
from autozest.settlement.fees import from_config as fees_from_config


def text(lines: list[str]) -> str:
    return "\n".join(lines)


def test_the_lights_section_says_what_each_one_means() -> None:
    printed = text(reference.lights_section())
    assert "GREEN" in printed
    assert "As is" in printed
    assert "odometer" in printed


def test_every_light_lists_what_it_hears() -> None:
    printed = text(reference.lights_section())
    assert printed.count("heard:") >= 4


def test_the_announcement_table_covers_every_code() -> None:
    printed = text(reference.announcements_section())
    assert "frame_damage" in printed
    assert "as is, no arbitration" in printed
    assert "cannot be arbitrated" in printed


def test_the_arbitration_section_prints_the_windows() -> None:
    printed = text(reference.arbitration_section())
    assert "48 business hours" in printed
    assert "policy 2024.1" in printed
    assert "not heard" in printed


def test_the_arbitration_section_follows_an_amended_policy() -> None:
    amended = from_config({"version": "2025.1", "engine": {"window": 96}})
    printed = text(reference.arbitration_section(amended))
    assert "policy 2025.1" in printed
    assert "96 business hours" in printed


def test_the_fee_section_prints_the_bands_and_the_services() -> None:
    printed = text(reference.fees_section())
    assert "card 2024.1" in printed
    assert "and up" in printed
    assert "transport" in printed
    assert "post_sale_inspection" in printed


def test_the_fee_section_follows_an_amended_card() -> None:
    amended = fees_from_config({"version": "2025.1", "transport": "220.00"})
    printed = text(reference.fees_section(amended))
    assert "card 2025.1" in printed
    assert "220.00" in printed


def test_the_whole_card_reads_in_order() -> None:
    printed = text(reference.render())
    assert printed.index("The lights") < printed.index("What gets called")
    assert printed.index("What gets called") < printed.index("Arbitration windows")
    assert printed.index("Arbitration windows") < printed.index("Fees, card")


def test_the_sections_are_all_callable() -> None:
    for name, builder in reference.SECTIONS.items():
        assert builder(), name


def test_the_rules_section_lists_every_rule() -> None:
    from autozest.rules import CATALOGUE

    printed = text(reference.rules_section())
    assert "arb.announced" in printed
    assert f"{len(CATALOGUE)} rules" in printed


def test_the_status_section_covers_every_code() -> None:
    from autozest.errors import PolicyViolation, ValidationError

    printed = text(reference.statuses_section())
    assert str(ValidationError.exit_status) in printed
    assert PolicyViolation.code in printed
    assert "a rule forbids it" in printed


def test_the_statuses_are_unique_and_ordered() -> None:
    statuses = [status for status, _, _ in reference.EXIT_STATUSES]
    assert statuses == sorted(statuses)
    assert len(statuses) == len(set(statuses))


def test_the_whole_card_now_ends_with_the_statuses() -> None:
    printed = text(reference.render())
    assert printed.index("Fees, card") < printed.index("What the system will refuse")
    assert printed.index("What the system will refuse") < printed.index("Exit statuses")
