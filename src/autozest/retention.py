"""Keeping things for as long as they are needed and no longer.

Two different problems live under the word retention and mixing them up is how
somebody deletes a ledger.

**Pruning** removes rows that have served their purpose. Bid histories, notice
bodies, audit lines and claim events all pile up and none of them is evidence
of money. They have their own ages and they are pruned in place.

**Archiving** copies a whole sale out to its own file so it can be taken off
the live database. Nothing is pruned by archiving and archiving is not a
deletion.

The ledger is never touched by either. Neither are runs, consignments,
arbitration decisions or titles. If a row could be the answer to "what did we
agree and what did we pay", it stays.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from autozest.block.sales import SaleRepository
from autozest.errors import PolicyViolation
from autozest.storage.db import connect
from autozest.storage.records import all_rows, scalar

#: How long each prunable table is kept, in days.
RETENTION_DAYS: dict[str, int] = {
    "bid": 365 * 2,
    "notice": 365,
    "audit_event": 365 * 7,
    "claim_event": 365 * 7,
    "yard_movement": 365,
    "title_movement": 365 * 7,
}

#: The column each table's age is measured on.
AGE_COLUMN: dict[str, str] = {
    "bid": "made_at",
    "notice": "queued_at",
    "audit_event": "made_at",
    "claim_event": "made_at",
    "yard_movement": "moved_at",
    "title_movement": "made_at",
}

#: Nothing in here is ever pruned, whatever the policy file says.
PROTECTED = frozenset(
    {
        "ledger_entry",
        "ledger_line",
        "payment",
        "run",
        "consignment",
        "arbitration_claim",
        "title_record",
        "vehicle",
        "account",
        "sale",
        "schema_migration",
    }
)


@dataclass(frozen=True)
class PruneCandidate:
    """One table and how much of it is old enough to go."""

    table: str
    rows: int
    keep_days: int
    cutoff: date

    @property
    def has_work(self) -> bool:
        return self.rows > 0


@dataclass(frozen=True)
class PruneResult:
    """What a prune actually removed."""

    removed: dict[str, int]
    dry_run: bool

    @property
    def total(self) -> int:
        return sum(self.removed.values())


def candidates(conn: sqlite3.Connection, *, as_at: date | None = None) -> list[PruneCandidate]:
    """Every table with rows old enough to prune."""
    today = as_at or date.today()
    found: list[PruneCandidate] = []
    for table, keep in sorted(RETENTION_DAYS.items()):
        cutoff = today - timedelta(days=keep)
        column = AGE_COLUMN[table]
        rows = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM {table} WHERE {column} < ?",
                (cutoff.isoformat(),),
            )
            or 0
        )
        found.append(PruneCandidate(table=table, rows=rows, keep_days=keep, cutoff=cutoff))
    return found


def prune(
    conn: sqlite3.Connection, *, as_at: date | None = None, dry_run: bool = True
) -> PruneResult:
    """Remove what is old enough. Nothing protected is touched."""
    removed: dict[str, int] = {}
    for candidate in candidates(conn, as_at=as_at):
        if candidate.table in PROTECTED:
            raise PolicyViolation(
                "retention.protected",
                f"{candidate.table} is never pruned",
                table=candidate.table,
            )
        if not candidate.has_work:
            continue
        removed[candidate.table] = candidate.rows
        if not dry_run:
            column = AGE_COLUMN[candidate.table]
            conn.execute(
                f"DELETE FROM {candidate.table} WHERE {column} < ?",
                (candidate.cutoff.isoformat(),),
            )
    return PruneResult(removed=removed, dry_run=dry_run)


@dataclass(frozen=True)
class Archive:
    """A sale copied out to its own file."""

    sale_code: str
    path: Path
    tables: dict[str, int]

    @property
    def rows(self) -> int:
        return sum(self.tables.values())


ARCHIVE_TABLES = (
    ("sale", "SELECT * FROM sale WHERE code = ?"),
    ("lane", "SELECT * FROM lane WHERE sale_code = ?"),
    ("run", "SELECT * FROM run WHERE sale_code = ?"),
    (
        "bid",
        "SELECT b.* FROM bid b JOIN run r ON r.id = b.run_id WHERE r.sale_code = ?",
    ),
    (
        "registration",
        "SELECT * FROM registration WHERE sale_code = ?",
    ),
)


def archive_sale(conn: sqlite3.Connection, sale_code: str, target: Path | str) -> Archive:
    """Copy one sale's block records into a standalone file.

    Nothing is removed from the live database. Archiving and deleting are
    separate operations and conflating them is how a sale that somebody still
    needed disappeared.
    """
    sales = SaleRepository(conn)
    sale = sales.get(sale_code)
    if not sale.status.is_finished:
        raise PolicyViolation(
            "retention.sale_open",
            "a sale is archived once it is closed",
            sale=sale_code,
            status=sale.status.value,
        )
    destination = Path(target)
    out = connect(destination)
    counts: dict[str, int] = {}
    try:
        for table, query in ARCHIVE_TABLES:
            rows = all_rows(conn, query, (sale_code,))
            if not rows:
                counts[table] = 0
                continue
            columns = list(rows[0])
            out.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"({', '.join(f'{name} TEXT' for name in columns)})"
            )
            placeholders = ", ".join("?" for _ in columns)
            out.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                [tuple(row[name] for name in columns) for row in rows],
            )
            counts[table] = len(rows)
    finally:
        out.close()
    return Archive(sale_code=sale_code, path=destination, tables=counts)
