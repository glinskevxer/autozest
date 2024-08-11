"""Sales tax, and getting out of it.

Most units here are not taxed, because most buyers are dealers buying for
resale and the tax lands when the unit retails off their forecourt. That makes
the interesting part not the arithmetic but the exemption, and the exemption is
a document with a date on it.

That is the whole reason a certificate is a record rather than a flag on the
account. A dealer whose resale certificate lapsed in March is not exempt in
April, and the first anybody hears about it is an assessment two years later
covering every unit in between.

Three things have to line up before a sale is exempt and they come from three
different places: the buyer's class, a certificate that is live on the day of
the sale, and the state the unit is going to. A dealer with a Texas resale
certificate buying a unit that is being delivered in Oklahoma is not covered by
it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from autozest.catalog.accounts import AccountClass, AccountRepository
from autozest.errors import NotFound, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, one, update

#: Basis points, so 825 is 8.25 percent. Integers, because a rate of 0.0825 in
#: a float is not 0.0825 and this multiplies a hammer price.
BASIS_POINTS = 10_000


class ExemptionKind(Enum):
    RESALE = "resale"
    EXPORT = "export"
    GOVERNMENT = "government"
    CHARITY = "charity"

    @property
    def needs_a_licence(self) -> bool:
        """Kinds only a licensed trader can hold."""
        return self is ExemptionKind.RESALE


@dataclass(frozen=True)
class Certificate:
    """One exemption document."""

    id: str
    party_id: str
    state: str
    kind: ExemptionKind
    number: str
    issued_on: date
    expires_on: date | None = None
    revoked_on: date | None = None

    def is_live_on(self, day: date) -> bool:
        if day < self.issued_on:
            return False
        if self.revoked_on is not None and day >= self.revoked_on:
            return False
        return not (self.expires_on is not None and day > self.expires_on)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Certificate:
        return cls(
            id=row["id"],
            party_id=row["party_id"],
            state=row["state"],
            kind=ExemptionKind(row["kind"]),
            number=row["number"],
            issued_on=date.fromisoformat(row["issued_on"]),
            expires_on=date.fromisoformat(row["expires_on"]) if row["expires_on"] else None,
            revoked_on=date.fromisoformat(row["revoked_on"]) if row["revoked_on"] else None,
        )


@dataclass(frozen=True)
class TaxAssessment:
    """What is owed, and the reason it is or is not nothing."""

    state: str
    taxable: Money
    rate_basis_points: int
    exempt_under: Certificate | None
    reason: str

    @property
    def rate(self) -> Decimal:
        return Decimal(self.rate_basis_points) / 100

    @property
    def amount(self) -> Money:
        if self.exempt_under is not None:
            return Money.zero()
        return self.taxable.times(Decimal(self.rate_basis_points) / BASIS_POINTS)

    @property
    def is_exempt(self) -> bool:
        return self.exempt_under is not None

    def explain(self) -> str:
        if self.exempt_under is not None:
            return f"exempt under {self.exempt_under.kind.value} {self.exempt_under.number}"
        return f"{self.rate}% of {self.taxable} in {self.state}: {self.reason}"


class TaxService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepository(conn)

    def set_rate(self, state: str, basis_points: int, *, effective: date | None = None) -> None:
        if not 0 <= basis_points <= 2000:
            raise ValidationError(f"{basis_points} basis points is not a sales tax rate")
        existing = one(self.conn, "SELECT state FROM tax_rate WHERE state = ?", (state,))
        values = {
            "rate_basis_points": basis_points,
            "effective_from": (effective or date.today()).isoformat(),
        }
        if existing:
            update(self.conn, "tax_rate", {"state": state}, values)
        else:
            insert(self.conn, "tax_rate", {"state": state, **values})

    def rate_for(self, state: str) -> int:
        row = one(self.conn, "SELECT rate_basis_points FROM tax_rate WHERE state = ?", (state,))
        if row is None:
            raise NotFound("tax rate", state)
        return int(row["rate_basis_points"])

    def add_certificate(self, certificate: Certificate) -> Certificate:
        if certificate.kind.needs_a_licence:
            holder = self.accounts.get(certificate.party_id)
            if not holder.dealer_licence:
                raise ValidationError(
                    f"a {certificate.kind.value} certificate needs a dealer licence on file"
                )
        insert(
            self.conn,
            "exemption_certificate",
            {
                "id": certificate.id,
                "party_id": certificate.party_id,
                "state": certificate.state,
                "kind": certificate.kind.value,
                "number": certificate.number,
                "issued_on": certificate.issued_on.isoformat(),
                "expires_on": (
                    certificate.expires_on.isoformat() if certificate.expires_on else None
                ),
            },
        )
        return certificate

    def revoke(self, certificate_id: str, on: date | None = None) -> None:
        update(
            self.conn,
            "exemption_certificate",
            {"id": certificate_id},
            {"revoked_on": (on or date.today()).isoformat()},
        )

    def certificates(self, party_id: str, state: str | None = None) -> list[Certificate]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM exemption_certificate WHERE party_id = ? "
            "AND (? IS NULL OR state = ?) ORDER BY id",
            (party_id, state, state),
        )
        return [Certificate.from_row(row) for row in rows]

    def live_certificate(self, party_id: str, state: str, on: date) -> Certificate | None:
        """A certificate covering this party, in this state, on this day."""
        for certificate in self.certificates(party_id, state):
            if certificate.is_live_on(on):
                return certificate
        return None

    def assess(
        self, buyer_id: str, taxable: Money, destination_state: str, on: date
    ) -> TaxAssessment:
        """Work out the tax on one sale."""
        buyer = self.accounts.get(buyer_id)
        certificate = self.live_certificate(buyer_id, destination_state, on)
        if certificate is None and buyer.account_class is not AccountClass.PUBLIC:
            reason = "no live certificate for that state"
        elif certificate is None:
            reason = "retail buyer"
        else:
            reason = "covered"
        return TaxAssessment(
            state=destination_state,
            taxable=taxable,
            rate_basis_points=self.rate_for(destination_state),
            exempt_under=certificate,
            reason=reason,
        )

    def expiring(self, before: date) -> list[Certificate]:
        """Certificates that lapse soon, for the chase list."""
        rows = all_rows(
            self.conn,
            "SELECT * FROM exemption_certificate WHERE expires_on IS NOT NULL "
            "AND expires_on <= ? AND revoked_on IS NULL ORDER BY expires_on",
            (before.isoformat(),),
        )
        return [Certificate.from_row(row) for row in rows]
