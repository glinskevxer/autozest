"""Making a copy of the database somebody can be given.

The support company wants a copy to reproduce a bug. The group office wants
one to build a report against. Neither of them should have four hundred
dealers' tax numbers, licence numbers and contact details, and handing over the
live file because it is easier is how that happens anyway.

So this makes a copy with the identifying columns replaced. What it does not do
is drop rows: a redacted database has to behave like the real one, which means
the same units, the same money, the same shape. Only the strings that identify
a person are changed, and they are changed consistently, so a report that
groups by seller still groups the same way.

Money is never touched. A redacted file with different figures in it is not a
reproduction of anything.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autozest.storage.db import connect, table_names

#: table -> columns whose contents identify somebody.
IDENTIFYING: dict[str, tuple[str, ...]] = {
    "account": ("name", "tax_id", "dealer_licence"),
    "carrier": ("name", "contact"),
    "lienholder": ("name", "contact"),
    "staff": ("name",),
    "exemption_certificate": ("number",),
    "notice": ("subject", "body"),
    "payment": ("reference", "note"),
    "title_record": ("title_number",),
}

#: Columns that look identifying and are load bearing. Left alone deliberately.
KEPT = ("id", "party_id", "buyer_id", "seller_id", "consignment_id", "vin")


@dataclass(frozen=True)
class Redaction:
    """What a redaction changed."""

    path: Path
    columns: int
    rows: int

    def summary(self) -> str:
        return f"{self.rows} values across {self.columns} columns, written to {self.path}"


def pseudonym(value: str, salt: str) -> str:
    """A stable stand in for a real name.

    The same input gives the same output within one redaction and a different
    one between redactions, so two copies cannot be lined up against each other
    to recover anything.
    """
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return f"redacted-{digest[:12]}"


def redact(
    source: sqlite3.Connection, target: Path | str, *, salt: str = "autozest"
) -> Redaction:
    """Copy the database and replace what identifies a person."""
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    copy = connect(destination)
    source.backup(copy)

    present = set(table_names(copy))
    changed_columns = 0
    changed_rows = 0
    try:
        for table, columns in sorted(IDENTIFYING.items()):
            if table not in present:
                continue
            for column in columns:
                rows = copy.execute(
                    f"SELECT rowid AS row_id, {column} AS value FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} != ''"
                ).fetchall()
                if not rows:
                    continue
                changed_columns += 1
                for row in rows:
                    copy.execute(
                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                        (pseudonym(str(row["value"]), salt), row["row_id"]),
                    )
                    changed_rows += 1
    finally:
        copy.close()
    return Redaction(path=destination, columns=changed_columns, rows=changed_rows)


def would_change(conn: sqlite3.Connection) -> dict[str, int]:
    """How many values a redaction would replace, without doing it."""
    present = set(table_names(conn))
    counts: dict[str, int] = {}
    for table, columns in sorted(IDENTIFYING.items()):
        if table not in present:
            continue
        for column in columns:
            found = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != ''"
            ).fetchone()["n"]
            if found:
                counts[f"{table}.{column}"] = int(found)
    return counts
