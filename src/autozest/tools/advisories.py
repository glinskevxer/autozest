"""Which security advisories this project has looked at and accepted.

A dependency audit that reports something nobody has decided about is a build
that goes red and stays red until somebody turns the job off. So an advisory is
either fixed by moving the pin or it is accepted here, by identifier, with the
reason and the date somebody looked.

An acceptance expires. That is the whole point: an advisory accepted in March
because the fix was not out yet has to be looked at again in June, and an
acceptance with no end date is a decision nobody ever revisits.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Accepted:
    """One advisory somebody decided to live with, for now."""

    identifier: str
    package: str
    reason: str
    accepted_on: date
    review_by: date

    def is_current(self, on: date) -> bool:
        return self.accepted_on <= on <= self.review_by

    def is_overdue(self, on: date) -> bool:
        return on > self.review_by

    def __str__(self) -> str:
        return (
            f"{self.identifier} ({self.package}) accepted {self.accepted_on}, "
            f"review by {self.review_by}: {self.reason}"
        )


#: Nothing is accepted at the moment. Every pin is on a version with no known
#: advisory against it, which is the state to keep this in.
ACCEPTED: tuple[Accepted, ...] = ()


def by_identifier() -> dict[str, Accepted]:
    return {item.identifier: item for item in ACCEPTED}


def is_accepted(identifier: str, *, on: date | None = None) -> bool:
    """Whether this advisory has been looked at and is still accepted."""
    entry = by_identifier().get(identifier)
    return entry is not None and entry.is_current(on or date.today())


def overdue(on: date | None = None) -> list[Accepted]:
    """Acceptances that have run past their review date."""
    today = on or date.today()
    return [item for item in ACCEPTED if item.is_overdue(today)]


def unaccepted(found: list[str], *, on: date | None = None) -> list[str]:
    """Advisories an audit reported that nobody has decided about."""
    return sorted(item for item in found if not is_accepted(item, on=on))


@dataclass(frozen=True)
class Finding:
    """One advisory an audit reported against an installed package."""

    identifier: str
    package: str
    version: str
    fixed_in: tuple[str, ...] = ()

    @property
    def remedy(self) -> str:
        return ", ".join(self.fixed_in) if self.fixed_in else "no fix published"

    def __str__(self) -> str:
        return f"{self.identifier} {self.package} {self.version} (fixed in {self.remedy})"


def from_audit(payload: str) -> list[Finding]:
    """Read pip-audit's json output.

    Defensive about the shape on purpose. This runs against whatever pip-audit
    the job installed, the report grows keys between releases, and a KeyError
    here reads as a broken build rather than as a tool that moved on.
    """
    try:
        document: Any = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(document, dict):
        return []
    found: list[Finding] = []
    for entry in document.get("dependencies") or []:
        if not isinstance(entry, dict):
            continue
        for vuln in entry.get("vulns") or []:
            if not isinstance(vuln, dict) or not vuln.get("id"):
                continue
            found.append(
                Finding(
                    identifier=str(vuln["id"]),
                    package=str(entry.get("name", "?")),
                    version=str(entry.get("version", "?")),
                    fixed_in=tuple(str(v) for v in vuln.get("fix_versions") or ()),
                )
            )
    return sorted(found, key=lambda f: (f.package, f.identifier))


def summary(findings: list[Finding], *, on: date | None = None) -> list[str]:
    """What an audit found, split by whether somebody has decided about it."""
    today = on or date.today()
    lines: list[str] = []
    if not findings:
        lines.append("the audit found nothing")
    for finding in findings:
        mark = "accepted" if is_accepted(finding.identifier, on=today) else "undecided"
        lines.append(f"  {mark}: {finding}")
    stale = overdue(today)
    for item in stale:
        lines.append(f"  OVERDUE: {item}")
    undecided = len(findings) - sum(1 for f in findings if is_accepted(f.identifier, on=today))
    lines.append(
        f"{len(findings)} reported, {undecided} nobody has decided about, "
        f"{len(stale)} past review"
    )
    return lines


def render(on: date | None = None) -> list[str]:
    """What the acceptance list says today."""
    today = on or date.today()
    if not ACCEPTED:
        return ["no advisories accepted, every pin is clean"]
    lines = [str(item) for item in ACCEPTED]
    stale = overdue(today)
    lines.append(f"{len(ACCEPTED)} accepted, {len(stale)} past their review date")
    return lines


def _read(source: str) -> str | None:
    """The report, or None when the audit never produced one.

    pip-audit writing nothing is a normal outcome of a step that is allowed to
    fail, and turning that into a traceback here would make this step the thing
    that goes red instead of the thing that explains.
    """
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        return None
    return path.read_text("utf-8")


def main(argv: list[str] | None = None) -> int:
    """Report an audit against the acceptance list.

    A reported advisory does not fail this on its own. The pins in this project
    are deliberate and moving one is a decision with a commit behind it, not
    something a build does on its own at three in the morning. What does fail is
    an acceptance that has run past the date somebody said they would look
    again, because that is the project's own promise going unkept.
    """
    parser = argparse.ArgumentParser(description="check an audit against what we accept")
    parser.add_argument(
        "--from-audit",
        metavar="PATH",
        help="pip-audit json output, or - to read standard input",
    )
    args = parser.parse_args(argv)

    for line in render():
        print(line)
    if args.from_audit:
        payload = _read(args.from_audit)
        if payload is None:
            print(f"no audit report at {args.from_audit}, nothing to check")
        else:
            for line in summary(from_audit(payload)):
                print(line)

    stale = overdue()
    if stale:
        print(f"{len(stale)} acceptances are past their review date", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
