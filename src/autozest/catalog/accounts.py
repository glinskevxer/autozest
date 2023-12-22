"""The parties we deal with, and what their class lets them do.

The same dealer is a seller on Tuesday and a buyer on Thursday, so an account
has no side. What it has is a class, and the class is what the rest of the
system asks about. A public account can bid in the open lanes and can never
consign, a finance account consigns repossessions and never bids, and a factory
account is held to a different arbitration policy than either.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any

from autozest.errors import PolicyViolation, StateError
from autozest.storage.records import all_rows, insert, now_text, one_or_raise, scalar, update


class AccountClass(Enum):
    """What kind of party this is. Mirrors the check constraint on the table."""

    FRANCHISE_DEALER = "franchise_dealer"
    INDEPENDENT_DEALER = "independent_dealer"
    FLEET_LEASE = "fleet_lease"
    FINANCE = "finance"
    FACTORY = "factory"
    WHOLESALER = "wholesaler"
    PUBLIC = "public"

    @property
    def may_consign(self) -> bool:
        return self is not AccountClass.PUBLIC

    @property
    def may_bid(self) -> bool:
        return self is not AccountClass.FINANCE

    @property
    def is_licensed(self) -> bool:
        """Whether a dealer licence is required before the account can trade."""
        return self not in (AccountClass.PUBLIC,)


class AccountStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"

    @property
    def may_trade(self) -> bool:
        return self is AccountStatus.ACTIVE


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    account_class: AccountClass
    status: AccountStatus = AccountStatus.ACTIVE
    tax_id: str | None = None
    dealer_licence: str | None = None
    state: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Account:
        return cls(
            id=row["id"],
            name=row["name"],
            account_class=AccountClass(row["class"]),
            status=AccountStatus(row["status"]),
            tax_id=row["tax_id"],
            dealer_licence=row["dealer_licence"],
            state=row["state"],
        )

    def require_can_consign(self) -> None:
        """Raise unless this account may put a unit through the block."""
        self._require_trading()
        if not self.account_class.may_consign:
            raise PolicyViolation(
                "account.consign",
                f"a {self.account_class.value} account cannot consign",
                account=self.id,
            )

    def require_can_bid(self) -> None:
        """Raise unless this account may buy."""
        self._require_trading()
        if not self.account_class.may_bid:
            raise PolicyViolation(
                "account.bid",
                f"a {self.account_class.value} account cannot bid",
                account=self.id,
            )

    def _require_trading(self) -> None:
        if not self.status.may_trade:
            raise StateError(f"account {self.id}", self.status.value, "active")


class AccountRepository:
    """Reads and writes for :class:`Account`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, account: Account) -> Account:
        if account.account_class.is_licensed and not account.dealer_licence:
            raise PolicyViolation(
                "account.licence",
                f"a {account.account_class.value} account needs a dealer licence",
                account=account.id,
            )
        insert(
            self.conn,
            "account",
            {
                "id": account.id,
                "name": account.name,
                "class": account.account_class.value,
                "status": account.status.value,
                "tax_id": account.tax_id,
                "dealer_licence": account.dealer_licence,
                "state": account.state,
                "created_at": now_text(),
            },
        )
        return account

    def get(self, account_id: str) -> Account:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM account WHERE id = ?",
            (account_id,),
            "account",
            account_id,
        )
        return Account.from_row(row)

    def find(self, name: str, state: str | None = None) -> Account | None:
        rows = all_rows(
            self.conn,
            "SELECT * FROM account WHERE name = ? AND (? IS NULL OR state = ?)",
            (name, state, state),
        )
        return Account.from_row(rows[0]) if rows else None

    def of_class(self, account_class: AccountClass) -> list[Account]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM account WHERE class = ? ORDER BY name",
            (account_class.value,),
        )
        return [Account.from_row(row) for row in rows]

    def set_status(self, account_id: str, status: AccountStatus) -> Account:
        current = self.get(account_id)
        if current.status is AccountStatus.CLOSED and status is not AccountStatus.CLOSED:
            raise StateError(f"account {account_id}", "closed", "active or suspended")
        update(self.conn, "account", {"id": account_id}, {"status": status.value})
        return self.get(account_id)

    def count(self) -> int:
        return int(scalar(self.conn, "SELECT COUNT(*) FROM account") or 0)
