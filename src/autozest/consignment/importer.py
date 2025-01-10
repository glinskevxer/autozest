"""Bulk consignment import.

A fleet sends a spreadsheet of four hundred units a fortnight before the sale.
Somebody used to type them in. This reads the file instead, and the whole
design of it comes from watching that go wrong:

* the file is validated **entirely** before anything is written, because a run
  that inserts two hundred rows and then stops on a bad vin leaves the yard
  with a list nobody can reconcile
* every problem in the file is reported, not the first one, because the fleet
  is going to fix them in one pass and send it back
* rows are keyed by their line number in the original file, since that is what
  the person on the phone is looking at

Unknown columns are ignored rather than refused. Fleets add columns to their
own exports without telling anybody and none of them matter to us.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from autozest.catalog.accounts import AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import IntakeService
from autozest.consignment.models import Consignment
from autozest.errors import AutozestError, ValidationError
from autozest.money import Money
from autozest.storage.retry import writing
from autozest.vin import Vin

REQUIRED_COLUMNS = ("vin", "make", "model")
OPTIONAL_COLUMNS = ("year", "series", "floor", "transport_ref", "title", "note")


@dataclass(frozen=True)
class RowProblem:
    """One thing wrong with one line of the file."""

    line: int
    column: str
    detail: str

    def __str__(self) -> str:
        return f"line {self.line}, {self.column}: {self.detail}"


@dataclass
class ImportPlan:
    """What would happen, worked out before anything is written."""

    seller_id: str
    rows: list[tuple[int, Vehicle, Money | None, str | None, bool, str | None]] = field(
        default_factory=list
    )
    problems: list[RowProblem] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.problems

    @property
    def count(self) -> int:
        return len(self.rows)

    def report(self) -> list[str]:
        return [str(problem) for problem in self.problems]


def parse(text: str, seller_id: str) -> ImportPlan:
    """Read and check a file without touching the database."""
    plan = ImportPlan(seller_id=seller_id)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        plan.problems.append(RowProblem(0, "file", "the file is empty"))
        return plan
    columns = {name.strip().lower() for name in reader.fieldnames}
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        plan.problems.append(RowProblem(1, "header", f"missing columns: {', '.join(missing)}"))
        return plan

    seen: set[str] = set()
    for line, raw in enumerate(reader, start=2):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        problems = _check_row(line, row, seen)
        if problems:
            plan.problems.extend(problems)
            continue
        vin = Vin.parse(row["vin"], require_check_digit=False)
        seen.add(vin.value)
        plan.rows.append(
            (
                line,
                Vehicle(
                    vin=vin,
                    make=row["make"],
                    model=row["model"],
                    series=row.get("series") or None,
                    model_year=int(row["year"]) if row.get("year") else None,
                ),
                Money.parse(row["floor"]) if row.get("floor") else None,
                row.get("transport_ref") or None,
                row.get("title", "yes").lower() not in ("no", "false", "0"),
                row.get("note") or None,
            )
        )
    return plan


def _check_row(line: int, row: dict[str, str], seen: set[str]) -> list[RowProblem]:
    problems: list[RowProblem] = []
    for column in REQUIRED_COLUMNS:
        if not row.get(column):
            problems.append(RowProblem(line, column, "is empty"))
    if problems:
        return problems
    try:
        vin = Vin.parse(row["vin"], require_check_digit=False)
    except ValidationError as exc:
        return [RowProblem(line, "vin", str(exc))]
    if vin.value in seen:
        problems.append(RowProblem(line, "vin", "appears twice in this file"))
    if row.get("year"):
        try:
            year = int(row["year"])
        except ValueError:
            problems.append(RowProblem(line, "year", f"{row['year']!r} is not a year"))
        else:
            if not 1900 <= year <= date.today().year + 2:
                problems.append(RowProblem(line, "year", f"{year} is not a believable year"))
    if row.get("floor"):
        try:
            Money.parse(row["floor"])
        except ValidationError as exc:
            problems.append(RowProblem(line, "floor", str(exc)))
    return problems


class ConsignmentImporter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepository(conn)
        self.vehicles = VehicleRepository(conn)
        self.intake = IntakeService(conn)

    def plan(self, text: str, seller_id: str) -> ImportPlan:
        """Check the file, including things only the database knows."""
        built = parse(text, seller_id)
        try:
            self.accounts.get(seller_id).require_can_consign()
        except AutozestError as exc:
            built.problems.append(RowProblem(0, "seller", str(exc)))
            return built
        for line, vehicle, _, _, _, _ in built.rows:
            existing = self.intake.consignments.find_open_for_vin(vehicle.vin.value)
            if existing is not None:
                built.problems.append(
                    RowProblem(line, "vin", f"already consigned as {existing.id}")
                )
        return built

    def apply(self, plan: ImportPlan) -> list[Consignment]:
        """Write a plan, all of it or none of it.

        One transaction around the whole file. Four hundred rows written one
        commit at a time is four hundred fsyncs, and worse, a crash half way
        through leaves the yard with a list that matches nothing. The all or
        nothing promise the checking makes is only real if the writing keeps
        it too.
        """
        if not plan.is_clean:
            raise ValidationError(
                f"the file has {len(plan.problems)} problems and none of it was imported"
            )
        created: list[Consignment] = []
        with writing(self.conn):
            for _, vehicle, floor, transport, title, note in plan.rows:
                self.vehicles.upsert(vehicle)
                created.append(
                    self.intake.register(
                        vehicle.vin.value,
                        plan.seller_id,
                        floor=floor,
                        transport_ref=transport,
                        title_expected=title,
                        note=note,
                    )
                )
        return created

    def import_file(self, path: Path | str, seller_id: str) -> list[Consignment]:
        return self.apply(self.plan(Path(path).read_text("utf-8"), seller_id))
