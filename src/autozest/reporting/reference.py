"""The rule book, printed from the rules themselves.

Every auction has a laminated card on the counter and every laminated card is
out of date, because the rules move and the card does not. So this prints the
card from the same objects the system decides with. If the arbitration window
on the printout is wrong, the system is wrong, and that is a much better
problem to have than a printout nobody trusts.

It is also what a new starter is handed. The sections are in the order the
questions arrive: what does the light mean, what can I come back on, what does
it cost.
"""

from __future__ import annotations

from collections.abc import Callable

from autozest import rules as rules_catalogue
from autozest.arbitration.policy import ArbitrationPolicy, classes_for
from autozest.defects import DefectClass
from autozest.errors import (
    ConfigError,
    Conflict,
    NotFound,
    PolicyViolation,
    StateError,
    ValidationError,
)
from autozest.inspection.announcements import REGISTRY, AnnouncementCode, Light
from autozest.reporting.text import Align, Column, Table, heading, money_column, wrap
from autozest.settlement.fees import FeeSchedule, Service

#: What every exit status means. A script reading these is the point.
EXIT_STATUSES: tuple[tuple[int, str, str], ...] = (
    (0, "ok", "it worked"),
    (1, "error", "something unexpected, or a check that failed"),
    (ValidationError.exit_status, ValidationError.code, "the input is not well formed"),
    (ConfigError.exit_status, ConfigError.code, "the configuration or the database is wrong"),
    (NotFound.exit_status, NotFound.code, "no record with that key"),
    (Conflict.exit_status, Conflict.code, "it already exists, or would duplicate"),
    (StateError.exit_status, StateError.code, "the record is in the wrong state"),
    (PolicyViolation.exit_status, PolicyViolation.code, "a rule forbids it"),
)

LIGHT_MEANING = {
    Light.GREEN: "Full arbitration. Everything on the list below is heard.",
    Light.YELLOW: (
        "Arbitration on what an inspection could not have found. A dent was "
        "there to be looked at, a rolled odometer was not."
    ),
    Light.BLUE: "Title lane. The unit sells, the paperwork is still being chased.",
    Light.RED: (
        "As is. Nothing is heard except the title being what it was said to be, "
        "which is not negotiable under any light."
    ),
}


def lights_section() -> list[str]:
    lines = heading("The lights")
    for light in Light:
        lines.append(f"{light.value.upper()}")
        lines.extend(f"  {line}" for line in wrap(LIGHT_MEANING[light], 88))
        heard = sorted(defect.value for defect in classes_for(light))
        lines.extend(f"  heard: {line}" for line in wrap(", ".join(heard), 88))
        lines.append("")
    return lines


def announcements_section() -> list[str]:
    lines = heading("What gets called at the block")
    table = Table(
        [
            Column("code", 26),
            Column("what the announcer says", 40),
            Column("forces", 8),
            Column("bars", 3, Align.RIGHT),
        ]
    )
    rows: list[list[object]] = []
    for code in sorted(AnnouncementCode, key=lambda item: item.value):
        spec = REGISTRY[code]
        rows.append(
            [
                code.value,
                spec.label,
                spec.forces.value if spec.forces else "",
                len(spec.bars),
            ]
        )
    lines.extend(table.render(rows))
    lines.append("")
    lines.extend(
        wrap(
            "A defect that was announced cannot be arbitrated. That is the rule "
            "everything else hangs off.",
            88,
        )
    )
    lines.append("")
    return lines


def arbitration_section(policy: ArbitrationPolicy | None = None) -> list[str]:
    rules = policy or ArbitrationPolicy()
    lines = heading(f"Arbitration windows, policy {rules.version}")
    table = Table(
        [
            Column("class", 20),
            Column("window", 20),
            money_column("hammer at least", 16),
            Column("evidence", 9),
        ]
    )
    rows: list[list[object]] = []
    for defect in sorted(DefectClass, key=lambda item: item.value):
        rule = rules.rule_for(defect)
        rows.append(
            [
                defect.value,
                str(rule.window) if rule.is_arbitrable else "not heard",
                rule.threshold,
                rule.needs_evidence,
            ]
        )
    lines.extend(table.render(rows))
    lines.append("")
    return lines


def fees_section(schedule: FeeSchedule | None = None) -> list[str]:
    card = schedule or FeeSchedule()
    lines = heading(f"Fees, card {card.version}")
    table = Table(
        [
            money_column("up to", 14),
            money_column("buyer", 12),
            money_column("seller", 12),
        ]
    )
    rows: list[list[object]] = [
        [band.upper if band.upper else "and up", band.buyer, band.seller] for band in card.bands
    ]
    lines.extend(table.render(rows))
    lines.append("")
    lines.append("Services")
    for service in sorted(Service, key=lambda item: item.value):
        side = "buyer" if service.falls_on_buyer else "seller"
        lines.append(f"  {service.value:<22} {card.service(service)!s:>10}  {side}")
    lines.append("")
    return lines


def rules_section() -> list[str]:
    """Every rule the system will refuse something for."""
    lines = heading("What the system will refuse, and what it calls it")
    lines.extend(f"  {line}" for line in rules_catalogue.render())
    lines.append("")
    return lines


def statuses_section() -> list[str]:
    """What each exit status means, for whoever is writing a script."""
    lines = heading("Exit statuses")
    table = Table([Column("status", 8, Align.RIGHT), Column("code", 14), Column("means", 56)])
    rows: list[list[object]] = [
        [status, code, detail] for status, code, detail in EXIT_STATUSES
    ]
    lines.extend(table.render(rows))
    lines.append("")
    return lines


def render(
    policy: ArbitrationPolicy | None = None, schedule: FeeSchedule | None = None
) -> list[str]:
    """The whole card, in the order the questions arrive."""
    lines: list[str] = []
    lines.extend(lights_section())
    lines.extend(announcements_section())
    lines.extend(arbitration_section(policy))
    lines.extend(fees_section(schedule))
    lines.extend(rules_section())
    lines.extend(statuses_section())
    return lines


#: The card's sections, so the command line can print one of them. Typed as a
#: no argument callable, which the two that take an optional policy satisfy.
SECTIONS: dict[str, Callable[[], list[str]]] = {
    "lights": lights_section,
    "announcements": announcements_section,
    "arbitration": arbitration_section,
    "fees": fees_section,
    "rules": rules_section,
    "statuses": statuses_section,
}
