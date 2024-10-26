from datetime import date

import pytest

from autozest.catalog.odometer import OdometerStatus
from autozest.catalog.vehicles import Vehicle
from autozest.errors import ValidationError
from autozest.forms import STATUS_WORDING, Party, bill_of_sale, odometer_disclosure
from autozest.money import Money
from autozest.vin import Vin

ACCORD = Vehicle(
    vin=Vin.parse("1HGCM82633A004352"), make="Honda", model="Accord", series="EX-L"
)
SELLER = Party(name="Ridgeline Motors", address="14 Cotton Lane, Dallas TX", licence="TX-1")
BUYER = Party(name="Kestrel Auto", address="900 Marchmont Rd, Tulsa OK", licence="OK-2")


def text(lines: list[str]) -> str:
    return "\n".join(lines)


def flat(lines: list[str]) -> str:
    """The form with its wrapping removed, for asserting on a sentence."""
    return " ".join(" ".join(lines).split())


def test_a_disclosure_carries_the_vehicle_and_the_reading() -> None:
    printed = text(
        odometer_disclosure(
            ACCORD, 41_930, OdometerStatus.ACTUAL, SELLER, BUYER, on=date(2024, 3, 12)
        )
    )
    assert "ODOMETER DISCLOSURE STATEMENT" in printed
    assert "1HGCM82633A004352" in printed
    assert "41,930 miles" in printed
    assert "2003 Honda Accord EX-L" in printed
    assert "2024-03-12" in printed


def test_both_parties_get_a_signature_line() -> None:
    printed = text(odometer_disclosure(ACCORD, 41_930, OdometerStatus.ACTUAL, SELLER, BUYER))
    assert printed.count("signature ___") == 2
    assert "transferor: Ridgeline Motors" in printed
    assert "transferee: Kestrel Auto" in printed
    assert "dealer licence TX-1" in printed


def test_the_statutory_wording_is_used_verbatim() -> None:
    for status, wording in STATUS_WORDING.items():
        printed = flat(odometer_disclosure(ACCORD, 41_930, status, SELLER, BUYER))
        assert wording in printed


def test_a_rolled_odometer_carries_the_warning() -> None:
    printed = flat(
        odometer_disclosure(ACCORD, 41_930, OdometerStatus.NOT_ACTUAL, SELLER, BUYER)
    )
    assert "ODOMETER DISCREPANCY" in printed


def test_an_exempt_unit_says_so_rather_than_certifying_anything() -> None:
    printed = text(odometer_disclosure(ACCORD, 180_000, OdometerStatus.EXEMPT, SELLER, BUYER))
    assert "exempt from federal odometer disclosure" in printed
    assert "I certify" not in printed


def test_negative_miles() -> None:
    with pytest.raises(ValidationError, match="negative miles"):
        odometer_disclosure(ACCORD, -1, OdometerStatus.ACTUAL, SELLER, BUYER)


def test_a_party_without_a_licence_leaves_the_line_off() -> None:
    walk_in = Party(name="Walk In", address="somewhere")
    printed = text(odometer_disclosure(ACCORD, 1000, OdometerStatus.ACTUAL, SELLER, walk_in))
    assert printed.count("dealer licence") == 1


def test_a_bill_of_sale_carries_what_was_called() -> None:
    printed = text(
        bill_of_sale(
            ACCORD,
            Money.parse("8200.00"),
            SELLER,
            BUYER,
            announcements=["frame damage", "no keys"],
            on=date(2024, 3, 12),
        )
    )
    assert "BILL OF SALE" in printed
    assert "8,200.00" in printed
    assert "frame damage" in printed
    assert "Nothing announced above is subject to arbitration." in flat(
        bill_of_sale(ACCORD, Money.parse("8200.00"), SELLER, BUYER)
    )


def test_a_bill_of_sale_with_nothing_called_says_nothing() -> None:
    printed = text(bill_of_sale(ACCORD, Money.parse("8200.00"), SELLER, BUYER))
    assert "announced at the block\n  nothing" in printed


def test_a_negative_price() -> None:
    with pytest.raises(ValidationError, match="negative price"):
        bill_of_sale(ACCORD, Money(-1), SELLER, BUYER)


def test_every_line_fits_on_a_printed_page() -> None:
    for lines in (
        odometer_disclosure(ACCORD, 41_930, OdometerStatus.NOT_ACTUAL, SELLER, BUYER),
        bill_of_sale(ACCORD, Money.parse("8200.00"), SELLER, BUYER),
    ):
        assert all(len(line) <= 92 for line in lines)
