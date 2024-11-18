"""Did we call what we should have called.

Run after the titles come in, and it is the only report in here that grades the
building rather than a customer. Three things it looks for, and all three cost
real money when they go the wrong way.

A **miss** is a title that arrived carrying a brand nobody announced. That is a
buy back and it is our fault.

An **overcall** is a brand announced on a unit whose title turned out clean.
Nobody complains about those and they cost the seller a few hundred dollars in
a lower light, every time, quietly.

An **unverified** unit is one that sold with a brand announced and no title on
file yet. Not a problem, but it is the pool the misses come out of, so the
number matters.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.inspection.announcements import AnnouncementCode
from autozest.inspection.service import InspectionService
from autozest.title.branding import TitleBrand, brands_called, undisclosed_brand
from autozest.title.service import TitleService, TitleStatus


@dataclass(frozen=True)
class Finding:
    """One unit where the calls and the title did not line up."""

    consignment_id: str
    run_number: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.run_number}  {self.consignment_id}  {self.kind}: {self.detail}"


@dataclass(frozen=True)
class DisclosureAudit:
    """A sale's disclosures, checked against the paperwork that came back."""

    sale_code: str
    checked: int
    unverified: int
    misses: tuple[Finding, ...]
    overcalls: tuple[Finding, ...]

    @property
    def clean(self) -> int:
        return self.checked - len(self.misses) - len(self.overcalls)

    @property
    def is_clean(self) -> bool:
        return not self.misses and not self.overcalls

    def summary(self) -> str:
        return (
            f"{self.checked} titles checked, {len(self.misses)} missed, "
            f"{len(self.overcalls)} overcalled, {self.unverified} still to come"
        )


def audit(conn: sqlite3.Connection, sale_code: str) -> DisclosureAudit:
    """Check what was called against what the titles say."""
    sales = SaleRepository(conn)
    inspection = InspectionService(conn)
    titles = TitleService(conn)

    misses: list[Finding] = []
    overcalls: list[Finding] = []
    checked = 0
    unverified = 0

    for run in sales.runs(sale_code):
        if run.status is not RunStatus.SOLD:
            continue
        record = titles.find(run.consignment_id)
        called = inspection.announcements(run.consignment_id)
        if record is None or record.status in (TitleStatus.EXPECTED, TitleStatus.ABSENT):
            if brands_called(called):
                unverified += 1
            continue
        checked += 1
        missed = undisclosed_brand(record.brand, called)
        if missed is not None:
            misses.append(
                Finding(
                    consignment_id=run.consignment_id,
                    run_number=run.number,
                    kind="missed",
                    detail=f"title says {missed.value}, nothing was called",
                )
            )
            continue
        overcalled = _overcalled(record.brand, called)
        if overcalled is not None:
            overcalls.append(
                Finding(
                    consignment_id=run.consignment_id,
                    run_number=run.number,
                    kind="overcalled",
                    detail=f"{overcalled.value} was called, the title is {record.brand.value}",
                )
            )

    return DisclosureAudit(
        sale_code=sale_code,
        checked=checked,
        unverified=unverified,
        misses=tuple(misses),
        overcalls=tuple(overcalls),
    )


def _overcalled(brand: TitleBrand, called: set[AnnouncementCode]) -> TitleBrand | None:
    """A brand announced on a unit whose title is clean."""
    if not brand.is_clean:
        return None
    announced = {
        candidate
        for candidate in TitleBrand
        if not candidate.is_clean and undisclosed_brand(candidate, called) is None
    }
    if not announced:
        return None
    return sorted(announced, key=lambda item: item.value)[0]


def render(conn: sqlite3.Connection, sale_code: str) -> list[str]:
    """The audit as printable lines."""
    report = audit(conn, sale_code)
    lines = [f"disclosure audit {report.sale_code}"]
    for finding in report.misses:
        lines.append(f"  {finding}")
    for finding in report.overcalls:
        lines.append(f"  {finding}")
    lines.append(report.summary())
    lines.append("clean" if report.is_clean else "needs looking at")
    return lines
