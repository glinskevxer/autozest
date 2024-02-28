"""Writing a condition report, and closing it.

A report is open while the inspector is working and closed when they sign it.
Closing is the interesting moment: that is when the grade is computed, the
announcements are derived and written, and the consignment moves on. Nothing
about a closed report can be edited afterwards, because the announcement set it
produced is what the buyer heard.

Damage found after the report is closed does not reopen it. It becomes a
supplementary report, which is a different thing with a different date, and the
date is what an arbitration turns on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from autozest.catalog.vehicles import VehicleRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import NotFound, PolicyViolation
from autozest.identifiers import sequenced
from autozest.inspection.announcements import AnnouncementCode, AnnouncementSource
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.derivation import Findings, announce
from autozest.inspection.grade import GradeResult
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, one_or_raise, update

REPORT_PREFIX = "R"


@dataclass(frozen=True)
class ConditionReport:
    """One inspection of one consignment."""

    id: str
    consignment_id: str
    inspector: str
    inspected_at: datetime
    grade: Decimal | None = None
    frame_checked: bool = False
    drivable: bool = True
    engine_starts: bool = True
    tread_32nds: int | None = None
    finalised_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.finalised_at is None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ConditionReport:
        return cls(
            id=row["id"],
            consignment_id=row["consignment_id"],
            inspector=row["inspector"],
            inspected_at=datetime.fromisoformat(row["inspected_at"]),
            grade=Decimal(str(row["grade"])) if row["grade"] is not None else None,
            frame_checked=bool(row["frame_checked"]),
            drivable=bool(row["drivable"]),
            engine_starts=bool(row["engine_starts"]),
            tread_32nds=row["tread_32nds"],
            finalised_at=(
                datetime.fromisoformat(row["finalised_at"]) if row["finalised_at"] else None
            ),
        )


class InspectionService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)
        self.vehicles = VehicleRepository(conn)

    def open_report(
        self, consignment_id: str, inspector: str, *, at: datetime | None = None
    ) -> ConditionReport:
        """Start a report. The unit has to be on the ground."""
        consignment = self.consignments.get(consignment_id)
        if consignment.status is not ConsignmentStatus.ARRIVED:
            raise PolicyViolation(
                "inspection.not_arrived",
                "a unit is inspected once it is through the gate",
                consignment=consignment_id,
                status=consignment.status.value,
            )
        when = at or datetime.now().astimezone()
        report = ConditionReport(
            id=self._next_report_id(when.date()),
            consignment_id=consignment_id,
            inspector=inspector,
            inspected_at=when,
        )
        insert(
            self.conn,
            "condition_report",
            {
                "id": report.id,
                "consignment_id": report.consignment_id,
                "inspector": report.inspector,
                "inspected_at": report.inspected_at.isoformat(timespec="seconds"),
                "frame_checked": 0,
                "drivable": 1,
                "engine_starts": 1,
            },
        )
        return report

    def _next_report_id(self, day: date) -> str:
        rows = all_rows(
            self.conn,
            "SELECT id FROM condition_report WHERE id LIKE ?",
            (f"{REPORT_PREFIX}-{day.year:04d}-%",),
        )
        return sequenced(REPORT_PREFIX, day, len(rows) + 1)

    def get(self, report_id: str) -> ConditionReport:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM condition_report WHERE id = ?",
            (report_id,),
            "condition report",
            report_id,
        )
        return ConditionReport.from_row(row)

    def latest_for(self, consignment_id: str) -> ConditionReport | None:
        row = one(
            self.conn,
            "SELECT * FROM condition_report WHERE consignment_id = ? "
            "ORDER BY inspected_at DESC LIMIT 1",
            (consignment_id,),
        )
        return ConditionReport.from_row(row) if row else None

    def add_damage(self, report_id: str, line: DamageLine) -> None:
        """Record a finding. Only while the report is open."""
        report = self.get(report_id)
        if not report.is_open:
            raise PolicyViolation(
                "inspection.closed",
                "that report is signed, a later finding needs its own report",
                report=report_id,
            )
        insert(
            self.conn,
            "damage_line",
            {
                "report_id": report_id,
                "panel": line.panel.value,
                "kind": line.kind.value,
                "severity": line.severity,
                "estimate_cents": line.cost.cents,
                "structural": int(line.is_structural),
                "note": line.note,
            },
        )

    def damage(self, report_id: str) -> list[DamageLine]:
        rows = all_rows(
            self.conn,
            "SELECT panel, kind, severity, estimate_cents, note FROM damage_line "
            "WHERE report_id = ? ORDER BY id",
            (report_id,),
        )
        return [
            DamageLine(
                panel=Panel(row["panel"]),
                kind=DamageKind(row["kind"]),
                severity=row["severity"],
                estimate=Money(row["estimate_cents"]),
                note=row["note"],
            )
            for row in rows
        ]

    def finalise(
        self,
        report_id: str,
        *,
        drivable: bool = True,
        engine_starts: bool = True,
        tread_32nds: int | None = None,
        frame_checked: bool = True,
        unibody: bool = True,
        reference_value: Money | None = None,
        declared: frozenset[AnnouncementCode] = frozenset(),
        at: datetime | None = None,
    ) -> tuple[ConditionReport, set[AnnouncementCode]]:
        """Sign the report, grade the unit and write the announcements."""
        report = self.get(report_id)
        if not report.is_open:
            raise PolicyViolation(
                "inspection.closed", "that report is already signed", report=report_id
            )
        consignment = self.consignments.get(report.consignment_id)
        findings = self._findings(
            report,
            consignment.vin,
            drivable=drivable,
            engine_starts=engine_starts,
            tread_32nds=tread_32nds,
            unibody=unibody,
            reference_value=reference_value,
            declared=declared,
            title_present=consignment.title_expected,
        )
        result: GradeResult = findings.grade
        codes = announce(findings)
        when = at or datetime.now().astimezone()
        update(
            self.conn,
            "condition_report",
            {"id": report_id},
            {
                "grade": float(result.grade),
                "frame_checked": int(frame_checked),
                "drivable": int(drivable),
                "engine_starts": int(engine_starts),
                "tread_32nds": tread_32nds,
                "finalised_at": when.isoformat(timespec="seconds"),
            },
        )
        self._write_announcements(report.consignment_id, codes, declared)
        self.consignments.save_status(consignment, ConsignmentStatus.INSPECTED)
        return self.get(report_id), codes

    def _findings(
        self,
        report: ConditionReport,
        vin: str,
        *,
        drivable: bool,
        engine_starts: bool,
        tread_32nds: int | None,
        unibody: bool,
        reference_value: Money | None,
        declared: frozenset[AnnouncementCode],
        title_present: bool,
    ) -> Findings:
        vehicle = self.vehicles.get(vin)
        history = self.vehicles.history(vin)
        status = (
            history.status(vehicle.year, report.inspected_at.date())
            if history.readings
            else Findings().odometer_status
        )
        consignment = self.consignments.get(report.consignment_id)
        return Findings(
            damage=self.damage(report.id),
            drivable=drivable,
            engine_starts=engine_starts,
            tread_32nds=tread_32nds,
            keys_count=consignment.keys_count,
            odometer_status=status,
            title_present=title_present,
            unibody=unibody,
            reference_value=reference_value,
            declared=declared,
        )

    def _write_announcements(
        self,
        consignment_id: str,
        codes: set[AnnouncementCode],
        declared: frozenset[AnnouncementCode],
    ) -> None:
        for code in sorted(codes, key=lambda c: c.value):
            source = (
                AnnouncementSource.SELLER if code in declared else AnnouncementSource.INSPECTION
            )
            insert(
                self.conn,
                "announcement",
                {
                    "consignment_id": consignment_id,
                    "code": code.value,
                    "source": source.value,
                    "made_at": now_text(),
                },
            )

    def announcements(self, consignment_id: str) -> set[AnnouncementCode]:
        rows = all_rows(
            self.conn,
            "SELECT code FROM announcement WHERE consignment_id = ?",
            (consignment_id,),
        )
        return {AnnouncementCode(row["code"]) for row in rows}

    def add_announcement(
        self, consignment_id: str, code: AnnouncementCode, detail: str | None = None
    ) -> None:
        """Call something by hand. Used for what only the seller knows."""
        if self.consignments.get(consignment_id).status is ConsignmentStatus.SOLD:
            raise PolicyViolation(
                "announcement.after_sale",
                "the unit has already sold, an announcement now changes nothing",
                consignment=consignment_id,
            )
        insert(
            self.conn,
            "announcement",
            {
                "consignment_id": consignment_id,
                "code": code.value,
                "detail": detail,
                "source": AnnouncementSource.MANUAL.value,
                "made_at": now_text(),
            },
        )

    def require_report(self, consignment_id: str) -> ConditionReport:
        report = self.latest_for(consignment_id)
        if report is None:
            raise NotFound("condition report for consignment", consignment_id)
        return report
