"""The sale recap.

Printed twice: once at midnight for the block office, and again after the if
bids are worked through the next morning. The two are not the same document and
the difference between them is the whole point, so the recap carries the count
of units still on if rather than quietly leaving them out.

Sell through is counted against units that actually ran. A unit pulled before
it crossed never had a chance to sell and dragging the rate down with it makes
the number useless for comparing one Tuesday with the next.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from autozest.block.models import Run, RunStatus, Sale
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.inspection.announcements import Light
from autozest.money import Money, total
from autozest.reporting.text import Align, Column, Table, heading, key_values, money_column


@dataclass(frozen=True)
class LaneRecap:
    """One lane's night."""

    letter: str
    ran: int
    sold: int
    on_if: int
    gross: Money

    @property
    def sell_through(self) -> Decimal:
        return _percent(self.sold, self.ran)


@dataclass(frozen=True)
class Recap:
    """A whole sale, summarised."""

    sale: Sale
    catalogued: int
    ran: int
    sold: int
    no_sale: int
    on_if: int
    pulled: int
    gross: Money
    lanes: tuple[LaneRecap, ...]
    by_light: dict[Light, int]
    top: tuple[tuple[str, Money], ...]

    @property
    def sell_through(self) -> Decimal:
        return _percent(self.sold, self.ran)

    @property
    def average(self) -> Money:
        return Money(self.gross.cents // self.sold) if self.sold else Money.zero()

    @property
    def is_final(self) -> bool:
        """Whether anything is still waiting on a seller."""
        return self.on_if == 0


def _percent(part: int, whole: int) -> Decimal:
    if whole == 0:
        return Decimal("0.0")
    return (Decimal(part) * 100 / Decimal(whole)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def build(conn: sqlite3.Connection, sale_code: str, *, top: int = 5) -> Recap:
    """Assemble the recap for one sale."""
    sales = SaleRepository(conn)
    sale = sales.get(sale_code)
    runs = sales.runs(sale_code)
    return Recap(
        sale=sale,
        catalogued=len(runs),
        ran=_ran(runs),
        sold=_count(runs, RunStatus.SOLD),
        no_sale=_count(runs, RunStatus.NO_SALE),
        on_if=_count(runs, RunStatus.IF_BID),
        pulled=_count(runs, RunStatus.PULLED),
        gross=_gross(runs),
        lanes=tuple(_lane_recaps(sales, sale_code)),
        by_light={light: sum(1 for run in runs if run.light is light) for light in Light},
        top=tuple(_top(runs, top)),
    )


def _count(runs: list[Run], status: RunStatus) -> int:
    return sum(1 for run in runs if run.status is status)


def _ran(runs: list[Run]) -> int:
    """Everything that crossed the block, which excludes what was pulled."""
    return sum(1 for run in runs if run.status is not RunStatus.PULLED)


def _gross(runs: list[Run]) -> Money:
    return total([run.hammer for run in runs if run.status is RunStatus.SOLD and run.hammer])


def _lane_recaps(sales: SaleRepository, sale_code: str) -> list[LaneRecap]:
    recaps = []
    for lane in sales.lanes(sale_code):
        runs = sales.runs(sale_code, lane.letter)
        recaps.append(
            LaneRecap(
                letter=lane.letter,
                ran=_ran(runs),
                sold=_count(runs, RunStatus.SOLD),
                on_if=_count(runs, RunStatus.IF_BID),
                gross=_gross(runs),
            )
        )
    return recaps


def _top(runs: list[Run], how_many: int) -> list[tuple[str, Money]]:
    sold = [
        (run.number, run.hammer)
        for run in runs
        if run.status is RunStatus.SOLD and run.hammer is not None
    ]
    sold.sort(key=lambda pair: pair[1].cents, reverse=True)
    return sold[:how_many]


def render(conn: sqlite3.Connection, sale_code: str) -> list[str]:
    """The recap as printable lines."""
    recap = build(conn, sale_code)
    consignments = ConsignmentRepository(conn)
    lines = heading(f"sale recap  {recap.sale.code}  {recap.sale.title}")
    lines.extend(
        key_values(
            [
                ("date", recap.sale.sale_date),
                ("status", recap.sale.status.value),
                ("catalogued", recap.catalogued),
                ("ran", recap.ran),
                ("sold", recap.sold),
                ("no sale", recap.no_sale),
                ("on if", recap.on_if),
                ("pulled", recap.pulled),
                ("gross", recap.gross),
                ("average", recap.average),
                ("sell through", f"{recap.sell_through}%"),
                ("final", recap.is_final),
            ]
        )
    )
    lines.append("")
    lane_table = Table(
        [
            Column("lane", 4),
            Column("ran", 5, Align.RIGHT),
            Column("sold", 5, Align.RIGHT),
            Column("on if", 5, Align.RIGHT),
            money_column("gross", 13),
            Column("sell through", 12, Align.RIGHT),
        ]
    )
    lines.extend(
        lane_table.render(
            [
                [
                    lane.letter,
                    lane.ran,
                    lane.sold,
                    lane.on_if,
                    lane.gross,
                    f"{lane.sell_through}%",
                ]
                for lane in recap.lanes
            ]
        )
    )
    lines.append("")
    lines.append("lights")
    lines.extend(key_values([(light.value, count) for light, count in recap.by_light.items()]))
    if recap.top:
        lines.append("")
        lines.append("top of the night")
        for number, amount in recap.top:
            run = next(r for r in SaleRepository(conn).runs(sale_code) if r.number == number)
            unit = consignments.get(run.consignment_id)
            lines.append(f"  {number}  {str(amount).rjust(13)}  {unit.vin}")
    return lines
