"""One screen with the numbers somebody actually asks for.

Everything here exists elsewhere in a fuller form. What this adds is that it is
one command, because the alternative is six commands and a person who runs four
of them.

The numbers are grouped by who asks. The owner asks what came in. The
settlement office asks what is owed. The arbitration desk asks what is open.
Grouping them that way means each of them can stop reading at their own
heading.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from autozest.money import Money
from autozest.reporting import aging, disclosure, turnaround
from autozest.reporting import claims as claim_report
from autozest.reporting import recap as recap_report
from autozest.reporting.text import heading, key_values
from autozest.settlement.ledger import Account, Ledger
from autozest.settlement.statements import Side, StatementService
from autozest.storage.records import scalar


@dataclass(frozen=True)
class Dashboard:
    """What one look at the building says."""

    sale_code: str | None
    gross: Money
    sold: int
    sell_through: str
    owed_to_us: Money
    owed_by_us: Money
    overdue_accounts: int
    open_claims: int
    escalations: int
    outstanding_titles: int
    disclosure_misses: int
    cash: Money

    @property
    def is_quiet(self) -> bool:
        """Whether there is anything anybody has to act on."""
        return (
            self.escalations == 0 and self.overdue_accounts == 0 and self.disclosure_misses == 0
        )


def build(
    conn: sqlite3.Connection, sale_code: str | None = None, *, as_at: date | None = None
) -> Dashboard:
    """Gather it, from the reports that already know how."""
    statements = StatementService(conn)
    ledger = Ledger(conn)
    queue = claim_report.build(conn)
    aged = aging.build(conn)

    gross = Money.zero()
    sold = 0
    sell_through = "0.0%"
    misses = 0
    if sale_code:
        recap = recap_report.build(conn, sale_code)
        gross, sold = recap.gross, recap.sold
        sell_through = f"{recap.sell_through}%"
        misses = len(disclosure.audit(conn, sale_code).misses)

    outstanding = int(
        scalar(
            conn,
            "SELECT COUNT(*) FROM title_record WHERE status IN "
            "('expected', 'absent', 'attorney', 'lost_applied')",
        )
        or 0
    )
    return Dashboard(
        sale_code=sale_code,
        gross=gross,
        sold=sold,
        sell_through=sell_through,
        owed_to_us=Money(sum(m.cents for m in statements.outstanding_buyers().values())),
        owed_by_us=Money(sum(m.cents for m in statements.unpaid_sellers().values())),
        overdue_accounts=len(aged.overdue),
        open_claims=queue.count,
        escalations=len(queue.escalations),
        outstanding_titles=outstanding,
        disclosure_misses=misses,
        cash=ledger.balance(Account.CASH),
    )


def render(
    conn: sqlite3.Connection, sale_code: str | None = None, *, as_at: date | None = None
) -> list[str]:
    """The screen."""
    board = build(conn, sale_code, as_at=as_at)
    lines: list[str] = []

    lines.extend(heading("the sale"))
    lines.extend(
        key_values(
            [
                ("sale", board.sale_code or "none named"),
                ("sold", board.sold),
                ("gross", board.gross),
                ("sell through", board.sell_through),
            ]
        )
    )
    lines.append("")

    lines.extend(heading("the money"))
    lines.extend(
        key_values(
            [
                ("owed to us", board.owed_to_us),
                ("owed by us", board.owed_by_us),
                ("cash", board.cash),
                ("accounts overdue", board.overdue_accounts),
            ]
        )
    )
    lines.append("")

    lines.extend(heading("the desks"))
    lines.extend(
        key_values(
            [
                ("claims open", board.open_claims),
                ("for escalation", board.escalations),
                ("titles outstanding", board.outstanding_titles),
                ("disclosure misses", board.disclosure_misses),
            ]
        )
    )
    lines.append("")
    lines.extend(f"  {spread}" for spread in turnaround.everything(conn))
    lines.append("")
    lines.append("nothing needs anybody" if board.is_quiet else "there is work to do")
    return lines


def aged_side(conn: sqlite3.Connection, side: Side = Side.BUYER) -> aging.Aging:
    """The aged list, for a caller that wants the detail behind a number."""
    return aging.build(conn, side)
