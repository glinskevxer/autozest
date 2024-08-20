"""Dealer groups.

A group trades under several account numbers, often on both sides of the same
lane. That is not a reporting nicety. It is the exact mechanism by which a
seller ends up bidding on their own consignment: consign under one number, bid
under another, and a per account check sees two strangers.

Whether a group shares credit is a property of the group and not an assumption.
Some groups guarantee each other's paper and some deliberately do not, and
getting it wrong in either direction is expensive. Sharing when they do not
means refusing a bid that was funded; not sharing when they do means a group
with four accounts carrying four times the exposure anybody agreed to.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from autozest.catalog.accounts import AccountRepository
from autozest.errors import Conflict, NotFound
from autozest.storage.records import all_rows, insert, now_text, one, update


@dataclass(frozen=True)
class DealerGroup:
    """A set of accounts trading as one business."""

    id: str
    name: str
    shares_credit: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> DealerGroup:
        return cls(id=row["id"], name=row["name"], shares_credit=bool(row["shares_credit"]))


class GroupService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepository(conn)

    def create(self, group_id: str, name: str, *, shares_credit: bool = False) -> DealerGroup:
        insert(
            self.conn,
            "dealer_group",
            {
                "id": group_id,
                "name": name,
                "shares_credit": int(shares_credit),
                "created_at": now_text(),
            },
        )
        return self.get(group_id)

    def get(self, group_id: str) -> DealerGroup:
        row = one(self.conn, "SELECT * FROM dealer_group WHERE id = ?", (group_id,))
        if row is None:
            raise NotFound("dealer group", group_id)
        return DealerGroup.from_row(row)

    def add(self, group_id: str, account_id: str, *, on: date | None = None) -> None:
        """Put an account in a group. An account belongs to one at a time."""
        self.get(group_id)
        self.accounts.get(account_id)
        current = self.group_of(account_id)
        if current is not None:
            if current.id == group_id:
                return
            raise Conflict(
                "that account is already in a group",
                account=account_id,
                group=current.id,
            )
        insert(
            self.conn,
            "group_member",
            {
                "group_id": group_id,
                "account_id": account_id,
                "joined_on": (on or date.today()).isoformat(),
            },
        )

    def remove(self, group_id: str, account_id: str, *, on: date | None = None) -> None:
        update(
            self.conn,
            "group_member",
            {"group_id": group_id, "account_id": account_id},
            {"left_on": (on or date.today()).isoformat()},
        )

    def group_of(self, account_id: str) -> DealerGroup | None:
        row = one(
            self.conn,
            "SELECT g.* FROM dealer_group g JOIN group_member m ON m.group_id = g.id "
            "WHERE m.account_id = ? AND m.left_on IS NULL",
            (account_id,),
        )
        return DealerGroup.from_row(row) if row else None

    def members(self, group_id: str) -> list[str]:
        rows = all_rows(
            self.conn,
            "SELECT account_id FROM group_member WHERE group_id = ? AND left_on IS NULL "
            "ORDER BY account_id",
            (group_id,),
        )
        return [row["account_id"] for row in rows]

    def siblings(self, account_id: str) -> list[str]:
        """Every other account trading as the same business."""
        group = self.group_of(account_id)
        if group is None:
            return []
        return [member for member in self.members(group.id) if member != account_id]

    def related(self, left: str, right: str) -> bool:
        """Whether two account numbers are the same business."""
        if left == right:
            return True
        one_group = self.group_of(left)
        other_group = self.group_of(right)
        if one_group is None or other_group is None:
            return False
        return one_group.id == other_group.id

    def credit_pool(self, account_id: str) -> list[str]:
        """Accounts whose lines count together, including this one."""
        group = self.group_of(account_id)
        if group is None or not group.shares_credit:
            return [account_id]
        return self.members(group.id)

    def groups(self) -> list[DealerGroup]:
        rows = all_rows(self.conn, "SELECT * FROM dealer_group ORDER BY name")
        return [DealerGroup.from_row(row) for row in rows]
