"""Finding what is wrong with a database, and fixing the safe half.

Two kinds of wrong. Foreign key violations, which SQLite will report and which
mean a row points at something that is not there. And the shape problems it
will not report at all: a run marked sold with no price on it, a consignment
sold with no run behind it, a ledger entry with one line.

Only some of it is repairable and the split is not about difficulty. A dangling
foreign key can be deleted, because a row pointing at nothing was never usable.
A ledger entry that does not balance cannot be touched by anything automatic,
because the fix depends on which side is wrong and this has no way to know.

So findings carry whether they are repairable, and repair only ever does the
ones that are. Everything else is printed for a person.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.storage.db import integrity_problems
from autozest.storage.records import all_rows, scalar


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong."""

    code: str
    detail: str
    rows: int
    repairable: bool = False

    def __str__(self) -> str:
        mark = "fixable" if self.repairable else "look   "
        return f"{mark}  {self.code}: {self.detail} ({self.rows})"


@dataclass(frozen=True)
class Report:
    """Everything that is wrong."""

    findings: tuple[Finding, ...]

    @property
    def is_clean(self) -> bool:
        return not self.findings

    @property
    def repairable(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.repairable)

    @property
    def needs_a_person(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if not finding.repairable)

    def summary(self) -> str:
        if self.is_clean:
            return "nothing wrong"
        return (
            f"{len(self.findings)} findings, {len(self.repairable)} fixable, "
            f"{len(self.needs_a_person)} for a person"
        )


#: Shape checks sqlite will not make. Each is a count query and a description.
SHAPE_CHECKS: tuple[tuple[str, str, str, bool], ...] = (
    (
        "sold_without_price",
        "SELECT COUNT(*) FROM run WHERE status = 'sold' AND hammer_cents IS NULL",
        "runs marked sold with no price",
        False,
    ),
    (
        "sold_without_buyer",
        "SELECT COUNT(*) FROM run WHERE status = 'sold' AND buyer_id IS NULL",
        "runs marked sold with no buyer",
        False,
    ),
    (
        "consignment_sold_without_run",
        "SELECT COUNT(*) FROM consignment c WHERE c.status = 'sold' AND NOT EXISTS "
        "(SELECT 1 FROM run r WHERE r.consignment_id = c.id AND r.status = 'sold')",
        "consignments marked sold with no sold run",
        False,
    ),
    (
        "entry_without_lines",
        "SELECT COUNT(*) FROM ledger_entry e WHERE NOT EXISTS "
        "(SELECT 1 FROM ledger_line l WHERE l.entry_id = e.id)",
        "ledger entries with no lines",
        True,
    ),
    (
        "line_without_entry",
        "SELECT COUNT(*) FROM ledger_line l WHERE NOT EXISTS "
        "(SELECT 1 FROM ledger_entry e WHERE e.id = l.entry_id)",
        "ledger lines with no entry",
        True,
    ),
    (
        "unbalanced_entry",
        "SELECT COUNT(*) FROM (SELECT entry_id FROM ledger_line GROUP BY entry_id "
        "HAVING SUM(debit_cents) != SUM(credit_cents))",
        "ledger entries that do not balance",
        False,
    ),
)

#: What a repair does, keyed on the finding code.
REPAIRS: dict[str, str] = {
    "entry_without_lines": "DELETE FROM ledger_entry WHERE id IN "
    "(SELECT e.id FROM ledger_entry e WHERE NOT EXISTS "
    "(SELECT 1 FROM ledger_line l WHERE l.entry_id = e.id))",
    "line_without_entry": "DELETE FROM ledger_line WHERE entry_id NOT IN "
    "(SELECT id FROM ledger_entry)",
}


def examine(conn: sqlite3.Connection) -> Report:
    """Everything that is wrong with this database."""
    findings: list[Finding] = []
    for problem in integrity_problems(conn):
        findings.append(Finding(code="integrity", detail=problem, rows=1, repairable=False))
    for code, query, detail, repairable in SHAPE_CHECKS:
        count = int(scalar(conn, query) or 0)
        if count:
            findings.append(
                Finding(code=code, detail=detail, rows=count, repairable=repairable)
            )
    return Report(findings=tuple(findings))


def repair(conn: sqlite3.Connection) -> dict[str, int]:
    """Fix only the findings that are safe to fix automatically."""
    fixed: dict[str, int] = {}
    for finding in examine(conn).repairable:
        statement = REPAIRS.get(finding.code)
        if statement is None:
            continue
        conn.execute(statement)
        fixed[finding.code] = finding.rows
    return fixed


def orphans(conn: sqlite3.Connection) -> list[str]:
    """Rows pointing at something that is not there, named."""
    rows = all_rows(conn, "PRAGMA foreign_key_check")
    return [f"{row['table']} row {row['rowid']} -> {row['parent']}" for row in rows]
