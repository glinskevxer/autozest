"""How much of a sale rests on one party.

Two questions, and both are about what happens when somebody stops paying.

**Buyer concentration** is what share of a night one buyer took. A sale where
one dealer bought forty percent of the units is a sale where one dealer not
paying is a very bad week, and the number is worth seeing on the Wednesday
rather than the following Tuesday.

**Seller concentration** is the same thing from the other side. A sale that is
mostly one fleet's units is a sale that stops happening if that fleet moves,
which is a commercial problem rather than a credit one, and a slower one.

The threshold is a warning and not a rule. Nothing here refuses anything. A
concentrated sale is a fact about the market some weeks and the whole point
other weeks, and the system is in no position to tell them apart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.money import Money, total

#: A share above this is called out.
WARN_ABOVE = Decimal("30.0")


def _share(part: Money, whole: Money) -> Decimal:
    if whole.cents == 0:
        return Decimal("0.0")
    return (Decimal(part.cents) * 100 / Decimal(whole.cents)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class Share:
    """One party's slice."""

    party_id: str
    units: int
    value: Money
    share: Decimal

    @property
    def is_heavy(self) -> bool:
        return self.share > WARN_ABOVE

    def line(self) -> str:
        mark = "  <-- " if self.is_heavy else "      "
        amount = str(self.value)
        return f"{self.party_id:<12} {self.units:>4} units  {amount:>14}  {self.share}%{mark}"


@dataclass(frozen=True)
class Concentration:
    """A sale, split by who took what."""

    sale_code: str
    gross: Money
    buyers: tuple[Share, ...]
    sellers: tuple[Share, ...]

    @property
    def heaviest_buyer(self) -> Share | None:
        return self.buyers[0] if self.buyers else None

    @property
    def heavy_buyers(self) -> tuple[Share, ...]:
        return tuple(share for share in self.buyers if share.is_heavy)

    @property
    def heavy_sellers(self) -> tuple[Share, ...]:
        return tuple(share for share in self.sellers if share.is_heavy)

    @property
    def is_concentrated(self) -> bool:
        return bool(self.heavy_buyers or self.heavy_sellers)

    def summary(self) -> str:
        if not self.buyers:
            return "nothing sold"
        top = self.buyers[0]
        return (
            f"{len(self.buyers)} buyers, {len(self.sellers)} sellers, "
            f"top buyer {top.party_id} at {top.share}% of {self.gross}"
        )


def build(conn: sqlite3.Connection, sale_code: str) -> Concentration:
    """Split one sale by buyer and by seller."""
    sales = SaleRepository(conn)
    consignments = ConsignmentRepository(conn)
    runs = [run for run in sales.runs(sale_code) if run.status is RunStatus.SOLD]
    gross = total([run.hammer for run in runs if run.hammer])

    by_buyer: dict[str, list[Money]] = {}
    by_seller: dict[str, list[Money]] = {}
    for run in runs:
        if run.hammer is None or run.buyer_id is None:
            continue
        by_buyer.setdefault(run.buyer_id, []).append(run.hammer)
        seller = consignments.get(run.consignment_id).seller_id
        by_seller.setdefault(seller, []).append(run.hammer)

    return Concentration(
        sale_code=sale_code,
        gross=gross,
        buyers=_shares(by_buyer, gross),
        sellers=_shares(by_seller, gross),
    )


def _shares(grouped: dict[str, list[Money]], gross: Money) -> tuple[Share, ...]:
    shares = [
        Share(
            party_id=party,
            units=len(amounts),
            value=total(amounts),
            share=_share(total(amounts), gross),
        )
        for party, amounts in grouped.items()
    ]
    shares.sort(key=lambda item: -item.value.cents)
    return tuple(shares)


def render(conn: sqlite3.Connection, sale_code: str) -> list[str]:
    report = build(conn, sale_code)
    lines = [f"concentration {report.sale_code}  gross {report.gross}", "buyers"]
    lines.extend(f"  {share.line()}" for share in report.buyers)
    lines.append("sellers")
    lines.extend(f"  {share.line()}" for share in report.sellers)
    lines.append(report.summary())
    lines.append("concentrated" if report.is_concentrated else "spread across enough people")
    return lines
