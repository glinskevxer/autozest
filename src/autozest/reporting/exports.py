"""CSV exports.

Everything downstream of this building reads CSV. The accountant imports the
ledger, the floorplan company wants the sold list every Wednesday, and the
group office pulls arbitration numbers into a spreadsheet nobody will let us
see. So the exports are boring on purpose: fixed column order, header row
always present, amounts as plain decimal strings with no currency symbol and no
thousands separators.

That last one matters. A comma inside a number in a CSV is a bug waiting for a
spreadsheet to find it, and the pretty rendering in the text reports is exactly
the wrong thing here.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from autozest.arbitration.claims import ClaimRepository
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.ledger import Ledger
from autozest.storage.records import all_rows


def plain(value: object) -> str:
    """One rule for turning anything into a cell.

    Amounts go out as ``8200.00`` and never ``8,200.00``, because the comma is
    a bug waiting for a spreadsheet to find it.
    """
    if value is None:
        return ""
    if isinstance(value, Money):
        return str(value.dollars)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


@dataclass(frozen=True)
class Export:
    """A named set of rows with a fixed header."""

    name: str
    header: Sequence[str]
    rows: Sequence[Sequence[object]]

    def __post_init__(self) -> None:
        width = len(self.header)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValidationError(
                    f"row {index} of {self.name} has {len(row)} cells, the header has {width}"
                )

    def to_text(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.header)
        for row in self.rows:
            writer.writerow([plain(cell) for cell in row])
        return buffer.getvalue()

    def write(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_text(), encoding="utf-8")
        return target

    @property
    def row_count(self) -> int:
        return len(self.rows)


def sold_units(conn: sqlite3.Connection, sale_code: str) -> Export:
    """What sold, for the floorplan company."""
    sales = SaleRepository(conn)
    consignments = ConsignmentRepository(conn)
    rows: list[Sequence[object]] = []
    for run in sales.runs(sale_code):
        if run.status.value != "sold":
            continue
        unit = consignments.get(run.consignment_id)
        rows.append(
            [
                run.sale_code,
                run.number,
                unit.vin,
                unit.seller_id,
                run.buyer_id,
                run.light.value,
                run.hammer,
                run.ran_at,
            ]
        )
    return Export(
        name="sold_units",
        header=["sale", "run", "vin", "seller", "buyer", "light", "hammer", "sold_at"],
        rows=rows,
    )


def ledger_lines(conn: sqlite3.Connection, reference_like: str | None = None) -> Export:
    """The journal, for the accountant."""
    rows = all_rows(
        conn,
        "SELECT e.id AS entry, e.reference, e.memo, e.source, e.posted_at, "
        "l.account, l.party_id, l.debit_cents, l.credit_cents "
        "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
        "WHERE (? IS NULL OR e.reference LIKE ?) ORDER BY e.id, l.id",
        (reference_like, f"{reference_like}%" if reference_like else None),
    )
    return Export(
        name="ledger",
        header=[
            "entry",
            "reference",
            "memo",
            "source",
            "posted_at",
            "account",
            "party",
            "debit",
            "credit",
        ],
        rows=[
            [
                row["entry"],
                row["reference"],
                row["memo"],
                row["source"],
                row["posted_at"],
                row["account"],
                row["party_id"],
                Money(row["debit_cents"]),
                Money(row["credit_cents"]),
            ]
            for row in rows
        ],
    )


def claims(conn: sqlite3.Connection) -> Export:
    """Arbitration, for the group office spreadsheet."""
    repository = ClaimRepository(conn)
    sales = SaleRepository(conn)
    rows: list[Sequence[object]] = []
    for row in all_rows(conn, "SELECT id FROM arbitration_claim ORDER BY id"):
        claim = repository.get(row["id"])
        run = sales.run(claim.run_id)
        rows.append(
            [
                claim.id,
                run.sale_code,
                run.number,
                claim.claimant_id,
                claim.defect.value,
                claim.status.value,
                claim.resolution.value if claim.resolution else None,
                claim.outcome,
                claim.policy_version,
                claim.opened_at,
                claim.decided_at,
            ]
        )
    return Export(
        name="claims",
        header=[
            "claim",
            "sale",
            "run",
            "claimant",
            "class",
            "status",
            "resolution",
            "outcome",
            "policy",
            "opened_at",
            "decided_at",
        ],
        rows=rows,
    )


def trial_balance(conn: sqlite3.Connection) -> Export:
    """Every account with a balance."""
    ledger = Ledger(conn)
    return Export(
        name="trial_balance",
        header=["account", "signed"],
        rows=[[account.value, amount] for account, amount in ledger.trial_balance().items()],
    )


EXPORTS = {
    "sold": sold_units,
    "ledger": ledger_lines,
    "claims": claims,
    "trial-balance": trial_balance,
}


def named(name: str) -> Any:
    try:
        return EXPORTS[name]
    except KeyError as exc:
        raise ValidationError(f"{name!r} is not an export", known=sorted(EXPORTS)) from exc


def available() -> Iterable[str]:
    return sorted(EXPORTS)
