"""Deciding a claim, and what the decision does to the money.

Four ways a claim can end well for the buyer and they are not variations on a
theme.

**Buy back** unwinds the sale. The buyer returns the unit and gets the hammer
back, and the seller has it again. The run stays sold in the record, because it
was sold, and the reversal is a separate movement rather than a rewriting of
what happened on the night.

**Price adjustment** restates the price. This is the one with a tail: the buyer
fee and the seller fee were both charged on a hammer that has now changed, so
they have to be recomputed. That is why :attr:`Resolution.changes_the_hammer`
exists and why settlement reads the effective hammer rather than the run.

**Repair allowance** pays the buyer for a fix and leaves the price alone. The
fees do not move. This is usually the right answer for a mechanical claim,
because the unit is worth what it sold for once it is fixed.

**Split** is an adjustment where the seller and the auction share it, and it
moves the hammer for exactly the seller's share.

The cap on all of them is the hammer. An arbitration cannot hand a buyer more
than they paid, however much the repair estimate says, because at that point
the answer is a buy back.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from autozest.arbitration.claims import Claim, ClaimRepository, ClaimStatus, Resolution
from autozest.block.sales import SaleRepository
from autozest.errors import PolicyViolation, ValidationError
from autozest.money import Money, total
from autozest.storage.records import update

#: The auction's share of a split, the rest falling on the seller.
AUCTION_SPLIT_SHARE = "50"


@dataclass(frozen=True)
class Outcome:
    """What a decision does to the money, before anything is posted."""

    claim: Claim
    resolution: Resolution
    amount: Money
    buyer_receives: Money
    seller_pays: Money
    auction_pays: Money
    hammer_after: Money

    @property
    def unwinds(self) -> bool:
        return self.resolution.unwinds_the_sale


class ResolutionService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.claims = ClaimRepository(conn)
        self.sales = SaleRepository(conn)

    def hammer_for(self, run_id: int) -> Money:
        """The price at the fall, before any arbitration touched it."""
        run = self.sales.run(run_id)
        if run.hammer is None:
            raise PolicyViolation("arb.no_hammer", "that unit has no price", run=run.number)
        return run.hammer

    def effective_hammer(self, run_id: int) -> Money:
        """The price after every adjustment that has landed on this unit.

        Settlement reads this. Admissibility deliberately does not.
        """
        adjustments = [
            claim.outcome or Money.zero()
            for claim in self.claims.for_run(run_id)
            if claim.status is ClaimStatus.RESOLVED
            and claim.resolution is not None
            and claim.resolution.changes_the_hammer
        ]
        return self.hammer_for(run_id) - total(adjustments)

    def plan(
        self, claim_reference: str, resolution: Resolution, amount: Money | None = None
    ) -> Outcome:
        """Work out an outcome without recording it."""
        claim = self.claims.get(claim_reference)

        if resolution is Resolution.DENIED:
            return Outcome(
                claim=claim,
                resolution=resolution,
                amount=Money.zero(),
                buyer_receives=Money.zero(),
                seller_pays=Money.zero(),
                auction_pays=Money.zero(),
                hammer_after=self.effective_hammer(claim.run_id),
            )

        if resolution is Resolution.BUY_BACK:
            settled = self.effective_hammer(claim.run_id)
            return Outcome(
                claim=claim,
                resolution=resolution,
                amount=settled,
                buyer_receives=settled,
                seller_pays=settled,
                auction_pays=Money.zero(),
                hammer_after=settled,
            )

        if amount is None:
            raise ValidationError(f"a {resolution.value} needs an amount")
        if amount.is_negative:
            raise ValidationError("an arbitration cannot pay a negative amount")

        remaining = self.effective_hammer(claim.run_id)
        if amount > remaining:
            raise PolicyViolation(
                "arb.over_hammer",
                "an arbitration cannot hand back more than the unit brought, "
                "at that point the answer is a buy back",
                claim=claim_reference,
                amount=str(amount),
                hammer=str(remaining),
            )

        if resolution is Resolution.SPLIT:
            auction_share, seller_share = amount.allocate(
                [int(AUCTION_SPLIT_SHARE), 100 - int(AUCTION_SPLIT_SHARE)]
            )
        else:
            auction_share, seller_share = Money.zero(), amount

        hammer_after = remaining - amount if resolution.changes_the_hammer else remaining
        return Outcome(
            claim=claim,
            resolution=resolution,
            amount=amount,
            buyer_receives=amount,
            seller_pays=seller_share,
            auction_pays=auction_share,
            hammer_after=hammer_after,
        )

    def resolve(
        self,
        claim_reference: str,
        resolution: Resolution,
        *,
        amount: Money | None = None,
        reason: str,
        actor: str = "desk",
        at: datetime | None = None,
    ) -> Outcome:
        """Decide a claim and write the decision."""
        if not reason.strip():
            raise ValidationError("a decision needs a reason on the record")
        claim = self.claims.get(claim_reference)
        if claim.status is not ClaimStatus.UNDER_REVIEW:
            raise PolicyViolation(
                "arb.not_under_review",
                "a claim is decided once the desk has taken it up",
                claim=claim_reference,
                status=claim.status.value,
            )
        outcome = self.plan(claim_reference, resolution, amount)
        wanted = ClaimStatus.DENIED if resolution is Resolution.DENIED else ClaimStatus.RESOLVED
        moved = claim.decided_as(wanted, resolution, outcome.amount)
        update(
            self.conn,
            "arbitration_claim",
            {"id": claim_reference},
            {
                "status": moved.status.value,
                "resolution": resolution.value,
                "outcome_cents": outcome.amount.cents,
                "reason": reason,
                "decided_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"),
            },
        )
        self.claims.record(claim_reference, resolution.value, actor, reason)
        return Outcome(
            claim=self.claims.get(claim_reference),
            resolution=outcome.resolution,
            amount=outcome.amount,
            buyer_receives=outcome.buyer_receives,
            seller_pays=outcome.seller_pays,
            auction_pays=outcome.auction_pays,
            hammer_after=outcome.hammer_after,
        )

    def exposure(self, run_id: int) -> Money:
        """What has been paid out on this unit so far."""
        return total(
            [
                claim.outcome or Money.zero()
                for claim in self.claims.for_run(run_id)
                if claim.status is ClaimStatus.RESOLVED
            ]
        )

    def was_bought_back(self, run_id: int) -> bool:
        return any(
            claim.resolution is Resolution.BUY_BACK and claim.status is ClaimStatus.RESOLVED
            for claim in self.claims.for_run(run_id)
        )
