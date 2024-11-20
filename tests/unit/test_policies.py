from autozest.config import Settings, from_mapping
from autozest.defects import DefectClass
from autozest.money import Money
from autozest.policies import build
from autozest.settlement.fees import Service
from autozest.timing import WindowUnit


def test_the_defaults_build() -> None:
    policies = build()
    assert policies.arbitration.version == "2024.1"
    assert policies.fees.version == "2024.1"
    assert policies.calendar.opens.hour == 8


def test_an_arbitration_amendment_reaches_the_policy() -> None:
    settings = from_mapping({"arbitration": {"version": "2025.1", "engine": {"window": 96}}})
    policies = build(settings)
    assert policies.arbitration.version == "2025.1"
    assert policies.arbitration.window_for(DefectClass.ENGINE).amount == 96
    assert policies.arbitration.window_for(DefectClass.ENGINE).unit is WindowUnit.BUSINESS_HOURS


def test_a_fee_amendment_reaches_the_card() -> None:
    settings = from_mapping({"fees": {"version": "2025.1", "transport": "220.00"}})
    policies = build(settings)
    assert policies.fees.version == "2025.1"
    assert policies.fees.service(Service.TRANSPORT) == Money.parse("220.00")


def test_the_calendar_comes_from_the_same_place() -> None:
    settings = from_mapping({"opens": "07:00:00", "extra_closures": ["2024-05-06"]})
    policies = build(settings)
    assert policies.calendar.opens.hour == 7
    assert len(policies.calendar.extra_closures) == 1


def test_both_amendments_at_once() -> None:
    settings = from_mapping(
        {
            "arbitration": {"engine": {"window": 96}},
            "fees": {"transport": "220.00"},
        }
    )
    policies = build(settings)
    assert policies.arbitration.window_for(DefectClass.ENGINE).amount == 96
    assert policies.fees.service(Service.TRANSPORT) == Money.parse("220.00")


def test_the_description_names_both_versions() -> None:
    described = "\n".join(build(Settings()).describe())
    assert "arbitration policy 2024.1" in described
    assert "fee card 2024.1" in described
    assert "open 08:00:00" in described
