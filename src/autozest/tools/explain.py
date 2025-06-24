"""What SQLite is actually doing with our queries.

Run as ``python -m autozest.tools.explain``. It takes the handful of queries
that run on every screen, asks SQLite for its plan, and says which of them are
scanning a table rather than using an index.

It exists because the indexes in the migrations were guesses until somebody
looked. Three of them were on columns nothing filtered by and two queries that
run on every sale night were reading the whole ledger.

A scan is not automatically wrong. Scanning a table with four rows in it is
faster than an index lookup, which is why this reports rather than fails.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Re-exported deliberately: the test patches it here to watch that the
# connection this module opens for itself is closed again.
from autozest.storage.db import connect_memory as connect_memory
from autozest.storage.migrator import migrate

#: The queries that run on a screen somebody is waiting in front of.
WATCHED: dict[str, tuple[str, tuple[object, ...]]] = {
    "seller statement": (
        "SELECT e.reference, e.memo, e.posted_at, l.debit_cents, l.credit_cents "
        "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
        "WHERE l.account = ? AND l.party_id = ? ORDER BY e.id, l.id",
        ("seller_payable", "d-100"),
    ),
    "aged receivables": (
        "SELECT l.party_id, e.reference, e.posted_at, l.debit_cents, l.credit_cents "
        "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
        "WHERE l.account = ? AND l.party_id IS NOT NULL ORDER BY l.party_id, e.id",
        ("buyer_receivable",),
    ),
    "run sheet": (
        "SELECT r.id FROM run r JOIN consignment c ON c.id = r.consignment_id "
        "JOIN vehicle v ON v.vin = c.vin WHERE r.sale_code = ? ORDER BY r.lane, r.position",
        ("S-20240312",),
    ),
    "open claims": (
        "SELECT * FROM arbitration_claim WHERE status IN ('open', 'under_review') "
        "ORDER BY opened_at",
        (),
    ),
    "outstanding titles": (
        "SELECT * FROM title_record WHERE status IN "
        "('expected', 'absent', 'attorney', 'lost_applied') ORDER BY consignment_id",
        (),
    ),
    "payment by reference": (
        "SELECT party_id FROM payment WHERE reference = ? LIMIT 1",
        ("WIRE-1",),
    ),
}


@dataclass(frozen=True)
class Plan:
    """What SQLite said it would do."""

    name: str
    steps: tuple[str, ...]

    @property
    def scans(self) -> tuple[str, ...]:
        return tuple(step for step in self.steps if "SCAN" in step)

    @property
    def uses_an_index(self) -> bool:
        return any(
            "USING INDEX" in step or "USING COVERING INDEX" in step for step in self.steps
        )

    def __str__(self) -> str:
        mark = "index" if self.uses_an_index else "scan "
        return f"{mark}  {self.name}: {'; '.join(self.steps)}"


def plan_for(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> tuple[str, ...]:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return tuple(row["detail"] for row in rows)


def plans(conn: sqlite3.Connection | None = None) -> list[Plan]:
    """The plan for every watched query.

    A connection this builds for itself is closed again. Python 3.13 raises a
    ResourceWarning when an unclosed one is collected, and with warnings as
    errors that surfaced as three unrelated tests failing on whichever one
    happened to trigger the collection.
    """
    if conn is not None:
        return _plans_on(conn)
    connection = connect_memory()
    try:
        migrate(connection)
        return _plans_on(connection)
    finally:
        connection.close()


def _plans_on(conn: sqlite3.Connection) -> list[Plan]:
    return [
        Plan(name=name, steps=plan_for(conn, sql, params))
        for name, (sql, params) in WATCHED.items()
    ]


def main() -> int:
    found = plans()
    for plan in found:
        print(str(plan))
    scanning = [plan for plan in found if not plan.uses_an_index]
    print(f"{len(found)} queries, {len(scanning)} without an index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
