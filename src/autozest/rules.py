"""Every rule this system will refuse something for, in one list.

A PolicyViolation carries a rule name and the name is what an operator greps
for, quotes down a phone and finds in the handbook. That makes it an interface,
and an interface nobody wrote down drifts: two of them were spelled differently
in the same module before this existed.

The catalogue is checked against the source. A rule raised anywhere and not
listed here fails the test, which is the only way a list like this stays true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    """One reason the system says no."""

    name: str
    area: str
    detail: str

    def __str__(self) -> str:
        return f"{self.name:<28} {self.detail}"


def _rule(name: str, area: str, detail: str) -> Rule:
    return Rule(name=name, area=area, detail=detail)


CATALOGUE: tuple[Rule, ...] = (
    _rule("account.bid", "accounts", "the account class does not bid"),
    _rule("account.consign", "accounts", "the account class does not consign"),
    _rule("account.licence", "accounts", "a licensed class needs a licence on file"),
    _rule("odometer.declaration", "catalog", "the declared status is softer than the evidence"),
    _rule("floor.negative", "consignment", "a floor cannot be negative"),
    _rule("floor.closed", "consignment", "the consignment is closed"),
    _rule(
        "floor.raise_after_catalogue", "consignment", "the floor cannot go up once catalogued"
    ),
    _rule("intake.already_arrived", "consignment", "the unit is past the gate"),
    _rule("intake.release", "consignment", "only a no sale is released back"),
    _rule("inspection.not_arrived", "inspection", "the unit is not through the gate"),
    _rule("inspection.closed", "inspection", "the report is signed"),
    _rule("inspection.template", "inspection", "a required check has not been answered"),
    _rule("announcement.after_sale", "inspection", "the unit has already sold"),
    _rule("sale.no_lanes", "block", "a sale needs a lane"),
    _rule("sale.frozen", "block", "the run order is printed"),
    _rule("sale.empty", "block", "there is nothing catalogued to sell"),
    _rule("sale.not_open", "block", "the sale is not running"),
    _rule("lane.full", "block", "the lane is at capacity"),
    _rule("catalogue.not_inspected", "block", "the unit has no signed report"),
    _rule("catalogue.frozen", "block", "the light cannot move once the sale is open"),
    _rule("catalogue.relight_reason", "block", "moving a light by hand needs a reason"),
    _rule("run.finished", "block", "the unit has already been through"),
    _rule("bid.increment", "block", "the bid is under the next step"),
    _rule("bid.own_unit", "block", "a seller cannot bid on their own consignment"),
    _rule("if_bid.counter_below", "block", "a counter has to beat the bid"),
    _rule("if_bid.not_pending", "block", "the unit is not on if"),
    _rule("if_bid.nothing_to_accept", "block", "there is no bid to accept"),
    _rule("proxy.too_late", "block", "the unit has already run"),
    _rule("registration.blocked", "block", "something stops this buyer registering"),
    _rule("registration.missing", "block", "the buyer is not on the door list"),
    _rule("registration.not_active", "block", "the badge is blocked"),
    _rule("registration.no_badges", "block", "every badge in the sale is taken"),
    _rule("limit.exceeded", "block", "the bid goes past a purchase cap"),
    _rule("roster.retired", "block", "the person is off the roster"),
    _rule("roster.double_booked", "block", "the person is already on another lane"),
    _rule("roster.short", "block", "a lane has no auctioneer or no ring man"),
    _rule("template.retired", "block", "the sale template is retired"),
    _rule("template.wrong_day", "block", "the template runs on another weekday"),
    _rule("rerun.never_ran", "block", "the unit has not been through a sale"),
    _rule("rerun.not_a_no_sale", "block", "only a no sale runs again"),
    _rule("rerun.too_many", "block", "the unit has run its three times"),
    _rule("arb.announced", "arbitration", "the defect was announced at the block"),
    _rule("arb.light", "arbitration", "the light does not hear this class"),
    _rule("arb.not_arbitrable", "arbitration", "nobody hears this class"),
    _rule("arb.threshold", "arbitration", "the hammer is under the class floor"),
    _rule("arb.window", "arbitration", "the window closed"),
    _rule("arb.repeat", "arbitration", "the class has already been decided"),
    _rule("arb.refused", "arbitration", "the claim is not heard"),
    _rule("arb.not_sold", "arbitration", "the unit did not sell"),
    _rule("arb.not_the_buyer", "arbitration", "only the buyer of record may claim"),
    _rule("arb.no_hammer", "arbitration", "the unit has no price"),
    _rule("arb.over_hammer", "arbitration", "an arbitration cannot pay past the hammer"),
    _rule("arb.not_under_review", "arbitration", "the desk has not taken the claim up"),
    _rule("psi.already_ordered", "arbitration", "an inspection is already on this unit"),
    _rule("psi.not_ordered", "arbitration", "no inspection was ordered"),
    _rule("psi.already_done", "arbitration", "the inspection is finished"),
    _rule("appeal.not_decided", "arbitration", "the claim has not been decided"),
    _rule("appeal.not_a_party", "arbitration", "only the buyer or seller may appeal"),
    _rule("appeal.late", "arbitration", "the appeal window closed"),
    _rule("appeal.already_decided", "arbitration", "the appeal is settled"),
    _rule("title.held", "title", "something is holding the title"),
    _rule("title.sold", "title", "a sold unit's title goes to the buyer"),
    _rule("lien.already_paid", "title", "the payoff has been made"),
    _rule("lien.short", "title", "the payment does not clear the lien today"),
    _rule("settle.not_sold", "settlement", "there is nothing to settle"),
    _rule("settle.not_posted", "settlement", "the run was never posted"),
    _rule("settle.no_buyer", "settlement", "the service falls on a buyer and there is none"),
    _rule("settle.nothing_to_post", "settlement", "the decision moves no money"),
    _rule("payment.held", "settlement", "something is holding the proceeds"),
    _rule("payment.overpay", "settlement", "that is more than the seller is owed"),
    _rule("proceeds.held", "settlement", "the unit is held"),
    _rule("proceeds.no_lienholder", "settlement", "a payoff needs somebody to pay"),
    _rule("credit.exhausted", "settlement", "the buyer is past their line"),
    _rule("credit.line_closed", "settlement", "the line is suspended or closed"),
    _rule("period.closed", "settlement", "the accounting period is shut"),
    _rule("period.checks_failed", "settlement", "the period will not close yet"),
    _rule("adjustment.needs_approval", "settlement", "the amount needs a second name"),
    _rule("adjustment.same_person", "settlement", "the second name must be somebody else"),
    _rule("adjustment.over_balance", "settlement", "more than the party owes"),
    _rule("transport.already_booked", "transport", "an order is already open"),
    _rule("yard.occupied", "yard", "move the unit before blocking the space"),
    _rule("yard.not_here", "yard", "the unit is not in the yard"),
    _rule("keys.already_handed", "yard", "the keys have gone"),
    _rule("release.held", "yard", "something is holding the unit"),
    _rule("retention.protected", "admin", "that table is never pruned"),
    _rule("retention.sale_open", "admin", "a sale is archived once closed"),
)

BY_NAME = {rule.name: rule for rule in CATALOGUE}

#: Three shapes carry a rule name. A PolicyViolation raises one, a Ruling
#: reports one without raising, which is how admissibility answers a question
#: rather than refusing an action, and a fallback covers a Ruling that somehow
#: arrived without one. All three count as the rule existing.
_PATTERNS = (
    re.compile(r'PolicyViolation\(\s*\n?\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(r'rule="([a-z_]+\.[a-z_]+)"'),
    re.compile(r'or "([a-z_]+\.[a-z_]+)"'),
)


def raised_in(root: Path) -> set[str]:
    """Every rule name the source actually produces."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if path.name == "rules.py":
            continue
        text = path.read_text("utf-8")
        for pattern in _PATTERNS:
            found |= set(pattern.findall(text))
    return found


def areas() -> dict[str, list[Rule]]:
    grouped: dict[str, list[Rule]] = {}
    for rule in CATALOGUE:
        grouped.setdefault(rule.area, []).append(rule)
    return grouped


def render() -> list[str]:
    """The catalogue, grouped, for the reference card."""
    lines: list[str] = []
    for area, rules in sorted(areas().items()):
        lines.append(area)
        lines.extend(f"  {rule}" for rule in rules)
    lines.append(f"{len(CATALOGUE)} rules")
    return lines
