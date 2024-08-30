"""``az arb``.

The desk works the phone with this. The two commands that matter most are
``assess``, which answers "can I claim on this" without opening anything, and
``open``, which either takes the claim or says exactly which rule refused it.
Those two share their whole answer, which is deliberate: an operator should
never be able to get a different answer from asking than from doing.
"""

from __future__ import annotations

from datetime import datetime

import click

from autozest.arbitration.claims import ClaimStatus, Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.sales import SaleRepository
from autozest.cli.context import Context
from autozest.defects import DefectClass
from autozest.money import Money
from autozest.reporting import claims as claim_report
from autozest.reporting.text import Align, Column, Table, key_values, money_column

pass_context = click.make_pass_decorator(Context)

CLASS_CHOICE = click.Choice([c.value for c in DefectClass])

CLAIM_TABLE = Table(
    [
        Column("claim", 13),
        Column("run", 5),
        Column("claimant", 9),
        Column("class", 16),
        Column("status", 13),
        money_column("outcome", 11),
    ]
)


def _run_id(ctx: Context, code: str, number: str) -> int:
    run = SaleRepository(ctx.open()).require_run(code, number)
    assert run.id is not None
    return run.id


@click.group()
def arb() -> None:
    """The arbitration desk."""


@arb.command("assess")
@click.argument("code")
@click.argument("run")
@click.option(
    "--class", "defect", default=None, type=click.Choice([c.value for c in DefectClass])
)
@pass_context
def assess(ctx: Context, code: str, run: str, defect: str | None) -> None:
    """Would a claim be heard right now, and until when."""
    conn = ctx.open()
    desk = ArbitrationDesk(conn)
    run_id = _run_id(ctx, code, run)
    if defect is None:
        open_now = desk.open_classes(run_id)
        for name in sorted(c.value for c in open_now):
            click.echo(name)
        click.echo(f"{len(open_now)} classes open")
        return
    ruling = desk.assess(run_id, DefectClass(defect))
    click.echo(ruling.explain())
    if not ruling.admissible:
        raise SystemExit(7)


@arb.command("open")
@click.argument("code")
@click.argument("run")
@click.option("--claimant", required=True)
@click.option(
    "--class", "defect", required=True, type=click.Choice([c.value for c in DefectClass])
)
@click.option("--summary", required=True)
@click.option("--claimed", default=None, help="What the buyer is asking for.")
@click.option("--actor", default="desk")
@pass_context
def open_claim(
    ctx: Context,
    code: str,
    run: str,
    claimant: str,
    defect: str,
    summary: str,
    claimed: str | None,
    actor: str,
) -> None:
    """Take a claim, or refuse it naming the rule."""
    desk = ArbitrationDesk(ctx.open())
    claim = desk.open_claim(
        _run_id(ctx, code, run),
        claimant,
        DefectClass(defect),
        summary,
        claimed=Money.parse(claimed) if claimed else None,
        actor=actor,
    )
    click.echo(f"opened {claim.id} on {run.upper()} for {claimant}")


@arb.command("take")
@click.argument("claim")
@click.option("--actor", default="desk")
@click.option("--note", default="")
@pass_context
def take(ctx: Context, claim: str, actor: str, note: str) -> None:
    """Move a claim into review."""
    moved = ArbitrationDesk(ctx.open()).take_up(claim, actor, note)
    click.echo(f"{moved.id} is {moved.status.value}")


@arb.command("resolve")
@click.argument("claim")
@click.argument("resolution", type=click.Choice([r.value for r in Resolution]))
@click.option("--amount", default=None)
@click.option("--reason", required=True)
@click.option("--actor", default="desk")
@pass_context
def resolve(
    ctx: Context, claim: str, resolution: str, amount: str | None, reason: str, actor: str
) -> None:
    """Decide a claim."""
    service = ResolutionService(ctx.open())
    outcome = service.resolve(
        claim,
        Resolution(resolution),
        amount=Money.parse(amount) if amount else None,
        reason=reason,
        actor=actor,
    )
    click.echo(f"{claim} {outcome.resolution.value} {outcome.amount}")
    for line in key_values(
        [
            ("buyer receives", outcome.buyer_receives),
            ("seller pays", outcome.seller_pays),
            ("auction pays", outcome.auction_pays),
            ("hammer after", outcome.hammer_after),
        ]
    ):
        click.echo(f"  {line}")


@arb.command("withdraw")
@click.argument("claim")
@click.argument("reason")
@click.option("--actor", default="desk")
@pass_context
def withdraw(ctx: Context, claim: str, reason: str, actor: str) -> None:
    """Pull a claim at the buyer's request."""
    click.echo(f"withdrew {ArbitrationDesk(ctx.open()).withdraw(claim, actor, reason).id}")


@arb.command("queue")
@click.option("--claimant", default=None)
@pass_context
def queue(ctx: Context, claimant: str | None) -> None:
    """Everything still open."""
    desk = ArbitrationDesk(ctx.open())
    claims = desk.claims.for_claimant(claimant) if claimant else desk.queue()
    rows = [
        [
            c.id,
            desk.sales.run(c.run_id).number,
            c.claimant_id,
            c.defect.value,
            c.status.value,
            c.outcome,
        ]
        for c in claims
    ]
    for line in CLAIM_TABLE.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} claims")


@arb.command("show")
@click.argument("claim")
@pass_context
def show(ctx: Context, claim: str) -> None:
    """One claim and its whole history."""
    desk = ArbitrationDesk(ctx.open())
    found = desk.claims.get(claim)
    run = desk.sales.run(found.run_id)
    for line in key_values(
        [
            ("claim", found.id),
            ("run", run.number),
            ("light", run.light.value),
            ("claimant", found.claimant_id),
            ("class", found.defect.value),
            ("summary", found.summary),
            ("claimed", found.claimed),
            ("status", found.status.value),
            ("resolution", found.resolution.value if found.resolution else None),
            ("outcome", found.outcome),
            ("policy", found.policy_version),
        ]
    ):
        click.echo(line)
    click.echo("history")
    table = Table(
        [
            Column("when", 20),
            Column("what", 18),
            Column("who", 10),
            Column("detail", 40, Align.LEFT),
        ]
    )
    rows = [
        [_when(event.made_at), event.kind, event.actor, event.detail]
        for event in desk.claims.events(claim)
    ]
    for line in table.render(rows):
        click.echo(f"  {line}")


def _when(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else ""


@arb.command("inspect")
@click.argument("code")
@click.argument("run")
@click.option("--complete", is_flag=True, help="Record the result rather than ordering one.")
@click.option("--passed/--failed", default=True)
@click.option("--findings", default=None)
@pass_context
def inspect_run(
    ctx: Context, code: str, run: str, complete: bool, passed: bool, findings: str | None
) -> None:
    """Order a post sale inspection, or record what it found."""
    desk = ArbitrationDesk(ctx.open())
    run_id = _run_id(ctx, code, run)
    if complete:
        check = desk.complete_inspection(run_id, passed=passed, findings=findings)
        state = "passed" if check.passed else "failed"
        click.echo(f"{check.id} {state}")
        return
    ordered = desk.order_inspection(run_id)
    click.echo(f"ordered {ordered.id}, the mechanical clock is stopped")


@arb.command("classes")
@pass_context
def classes(ctx: Context) -> None:
    """The defect classes and the window each one carries."""
    desk = ArbitrationDesk(ctx.open())
    table = Table(
        [
            Column("class", 18),
            Column("window", 20),
            money_column("threshold", 11),
            Column("evidence", 8),
        ]
    )
    rows = []
    for defect in DefectClass:
        rule = desk.policy.rule_for(defect)
        rows.append([defect.value, str(rule.window), rule.threshold, rule.needs_evidence])
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"policy {desk.policy.version}")


@arb.command("history")
@click.argument("code")
@click.argument("run")
@pass_context
def history(ctx: Context, code: str, run: str) -> None:
    """Every claim ever brought on one unit."""
    desk = ArbitrationDesk(ctx.open())
    run_id = _run_id(ctx, code, run)
    claims = desk.claims.for_run(run_id)
    settled = desk.claims.settled_classes(run_id)
    rows = [
        [c.id, run.upper(), c.claimant_id, c.defect.value, c.status.value, c.outcome]
        for c in claims
    ]
    for line in CLAIM_TABLE.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} claims, {len(settled)} classes used up")
    if any(c.status is ClaimStatus.RESOLVED for c in claims):
        service = ResolutionService(ctx.open())
        click.echo(f"hammer now {service.effective_hammer(run_id)}")


@arb.command("worklist")
@pass_context
def worklist(ctx: Context) -> None:
    """Everything open, staleest first, with the escalations called out."""
    for line in claim_report.render(ctx.open()):
        click.echo(line)
