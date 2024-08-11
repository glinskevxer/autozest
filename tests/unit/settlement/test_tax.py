import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.errors import NotFound, ValidationError
from autozest.money import Money
from autozest.settlement.tax import Certificate, ExemptionKind, TaxService
from autozest.storage.db import table_names

DAY = date(2024, 3, 12)


@pytest.fixture()
def tax(db: sqlite3.Connection) -> TaxService:
    accounts = AccountRepository(db)
    accounts.add(
        Account(
            id="d-200",
            name="Kestrel Auto",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-2",
            state="TX",
        )
    )
    accounts.add(Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC))
    service = TaxService(db)
    service.set_rate("TX", 825, effective=date(2024, 1, 1))
    service.set_rate("OK", 450, effective=date(2024, 1, 1))
    return service


def certificate(**kwargs: object) -> Certificate:
    defaults: dict[str, object] = {
        "id": "X-1",
        "party_id": "d-200",
        "state": "TX",
        "kind": ExemptionKind.RESALE,
        "number": "RS-99182",
        "issued_on": date(2024, 1, 1),
    }
    defaults.update(kwargs)
    return Certificate(**defaults)  # type: ignore[arg-type]


def test_the_tax_tables_are_there(db: sqlite3.Connection) -> None:
    assert "tax_rate" in table_names(db)
    assert "exemption_certificate" in table_names(db)


def test_rates_are_kept_in_basis_points(tax: TaxService) -> None:
    assert tax.rate_for("TX") == 825
    with pytest.raises(NotFound):
        tax.rate_for("ZZ")


def test_a_rate_nobody_would_charge(tax: TaxService) -> None:
    with pytest.raises(ValidationError, match="not a sales tax rate"):
        tax.set_rate("TX", 9000)


def test_a_rate_can_be_moved(tax: TaxService) -> None:
    tax.set_rate("TX", 900)
    assert tax.rate_for("TX") == 900


def test_a_retail_buyer_pays(tax: TaxService) -> None:
    assessment = tax.assess("p-1", Money.parse("8200.00"), "TX", DAY)
    assert not assessment.is_exempt
    assert assessment.rate == Decimal("8.25")
    assert assessment.amount == Money.parse("676.50")
    assert "retail buyer" in assessment.explain()


def test_a_dealer_with_a_live_certificate_does_not(tax: TaxService) -> None:
    tax.add_certificate(certificate())
    assessment = tax.assess("d-200", Money.parse("8200.00"), "TX", DAY)
    assert assessment.is_exempt
    assert assessment.amount == Money.zero()
    assert "exempt under resale RS-99182" in assessment.explain()


def test_a_certificate_that_has_lapsed_does_not_cover_anything(tax: TaxService) -> None:
    tax.add_certificate(certificate(expires_on=date(2024, 3, 1)))
    assessment = tax.assess("d-200", Money.parse("8200.00"), "TX", DAY)
    assert not assessment.is_exempt
    assert assessment.amount == Money.parse("676.50")


def test_a_certificate_is_not_live_before_it_was_issued(tax: TaxService) -> None:
    tax.add_certificate(certificate(issued_on=date(2024, 6, 1)))
    assert not tax.assess("d-200", Money.parse("8200.00"), "TX", DAY).is_exempt


def test_a_revoked_certificate_stops_working_that_day(tax: TaxService) -> None:
    tax.add_certificate(certificate())
    tax.revoke("X-1", on=date(2024, 3, 12))
    assert not tax.assess("d-200", Money.parse("8200.00"), "TX", DAY).is_exempt
    assert tax.assess("d-200", Money.parse("8200.00"), "TX", date(2024, 3, 11)).is_exempt


def test_a_certificate_for_another_state_does_not_travel(tax: TaxService) -> None:
    tax.add_certificate(certificate())
    assessment = tax.assess("d-200", Money.parse("8200.00"), "OK", DAY)
    assert not assessment.is_exempt
    assert assessment.rate == Decimal("4.5")
    assert "no live certificate" in assessment.explain()


def test_a_resale_certificate_needs_a_licence_behind_it(tax: TaxService) -> None:
    with pytest.raises(ValidationError, match="dealer licence"):
        tax.add_certificate(certificate(party_id="p-1"))


def test_an_export_certificate_does_not(tax: TaxService) -> None:
    tax.add_certificate(certificate(party_id="p-1", kind=ExemptionKind.EXPORT))
    assert tax.assess("p-1", Money.parse("8200.00"), "TX", DAY).is_exempt


def test_the_expiry_chase_list(tax: TaxService) -> None:
    tax.add_certificate(certificate(expires_on=date(2024, 4, 1)))
    tax.add_certificate(
        certificate(id="X-2", state="OK", number="RS-2", expires_on=date(2025, 4, 1))
    )
    assert [c.id for c in tax.expiring(date(2024, 6, 1))] == ["X-1"]
    assert len(tax.expiring(date(2026, 1, 1))) == 2


def test_a_revoked_certificate_drops_off_the_chase_list(tax: TaxService) -> None:
    tax.add_certificate(certificate(expires_on=date(2024, 4, 1)))
    tax.revoke("X-1")
    assert tax.expiring(date(2024, 6, 1)) == []


def test_the_rate_arithmetic_rounds_at_the_cent(tax: TaxService) -> None:
    assert tax.assess("p-1", Money.parse("100.06"), "TX", DAY).amount == Money.parse("8.25")
    assert tax.assess("p-1", Money.zero(), "TX", DAY).amount == Money.zero()
