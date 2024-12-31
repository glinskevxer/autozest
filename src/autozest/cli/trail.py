"""Recording what the command line did.

Every command that changes something writes one audit line. Doing it here
rather than inside each service is a deliberate trade: a service called from a
test or an import script writes nothing, and the trail is therefore a record of
what people did rather than of what the code did. That is the question the
trail gets asked.

The subject is whatever the command was about, and commands say so by returning
it. A command that changes nothing says nothing, which is why the read only
groups are listed rather than guessed at.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from autozest.audit import AuditLog

#: Groups that only ever print. Nothing from these is recorded.
READ_ONLY = frozenset({"report", "admin"})

#: Commands inside a writing group that only print.
READ_ONLY_COMMANDS = frozenset(
    {
        "consign:show",
        "consign:list",
        "consign:history",
        "sale:runs",
        "sale:list",
        "block:show",
        "block:if-list",
        "block:door",
        "block:blockers",
        "arb:assess",
        "arb:queue",
        "arb:show",
        "arb:classes",
        "arb:history",
        "arb:worklist",
        "arb:appeals",
        "title:show",
        "title:holds",
        "title:outstanding",
        "settle:statement",
        "settle:holds",
        "settle:queue",
        "settle:balances",
        "settle:trial-balance",
        "settle:notices",
        "settle:adjustments",
        "yard:find",
        "yard:free",
        "yard:occupancy",
        "yard:history",
        "gate:cabinet",
        "gate:overrides",
        "roster:people",
        "roster:lane",
        "roster:gaps",
    }
)


def is_worth_recording(group: str | None, command: str | None) -> bool:
    """Whether this invocation changed anything worth a line."""
    if group is None or command is None:
        return False
    if group in READ_ONLY:
        return False
    return f"{group}:{command}" not in READ_ONLY_COMMANDS


def record(
    conn: sqlite3.Connection,
    group: str,
    command: str,
    subject: str,
    *,
    actor: str | None = None,
    detail: Sequence[str] | None = None,
) -> None:
    """Write the line. Never raises: a trail failure must not fail the work."""
    try:
        AuditLog(conn, actor).record(
            f"{group}.{command}",
            group,
            subject,
            " ".join(detail) if detail else None,
        )
    except Exception:  # pragma: no cover - the trail is never the thing that fails
        return


def subject_of(arguments: Sequence[str]) -> str:
    """The best guess at what a command was about, from what was typed.

    The first positional argument, which is a reference, a vin or a sale code
    in every command that has one. Commands that take none say so rather than
    inventing something.
    """
    for argument in arguments:
        if argument.startswith("-"):
            continue
        return argument
    return "-"
