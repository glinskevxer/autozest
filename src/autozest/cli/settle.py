"""``az settle``.

The settlement office runs the day off four of these. ``post`` after the sale
closes, ``pay`` as the money arrives, ``holds`` when a seller rings up, and
``release`` when the answer is that nothing is stopping it.

``holds`` is the one that gets used most and it is written to be read out over
a phone: one reason per line, in the order they are usually cleared.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.money import Money
from autozest.notices import NoticeQueue
from autozest.reporting.text import Column, Table, key_values, money_column
from autozest.settlement.adjustments import AdjustmentKind, AdjustmentService
from autozest.settlement.fees import Service
from autozest.settlement.floorplan import FloorplanService, LineStatus
from autozest.settlement.ledger import Ledger
from autozest.settlement.payments import Method, PaymentService
from autozest.settlement.periods import PeriodService
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.settlement.reconcile import Reconciler, parse
from autozest.settlement.statements import Side, StatementService

pass_context = click.make_pass_decorator(Context)

PERIOD_ACTIONS = click.Choice(["open", "checks", "begin-close", "close", "reopen", "list"])

STATEMENT_TABLE = Table(
    [
        Column("reference", 18),
        Column("memo", 40),
        money_column("amount", 13),
    ]
)


@click.group()
def settle() -> None:
    """Post sales, take payments and release proceeds."""


@settle.command("post")
@click.argument("code")
@click.option("--run", "number", default=None, help="One run, or the whole sale.")
@pass_context
def post(ctx: Context, code: str, number: str | None) -> None:
    """Write the sale to the ledger."""
    conn = ctx.open()
    sales = SaleRepository(conn)
    posting = PostingService(conn, ctx.policies.fees)
    runs = (
        [sales.require_run(code, number)]
        if number
        else [r for r in sales.runs(code) if r.status is RunStatus.SOLD]
    )
    posted = 0
    for run in runs:
        assert run.id is not None
        if posting.is_posted(run.id):
            continue
        figures = posting.figures(run.id)
        posting.post_sale(run.id)
        posted += 1
        click.echo(
            f"{run.number}  hammer {figures.hammer}  buyer owes {figures.buyer_owes}  "
            f"seller gets {figures.seller_earns}"
        )
    click.echo(f"{posted} runs posted")


@settle.command("service")
@click.argument("code")
@click.argument("run")
@click.argument("service", type=click.Choice([s.value for s in Service]))
@click.option("--amount", default=None, help="Override the published price.")
@pass_context
def add_service(ctx: Context, code: str, run: str, service: str, amount: str | None) -> None:
    """Charge a service to whichever side it falls on."""
    conn = ctx.open()
    found = SaleRepository(conn).require_run(code, run)
    assert found.id is not None
    entry = PostingService(conn, ctx.policies.fees).post_service(
        found.id, Service(service), amount=Money.parse(amount) if amount else None
    )
    click.echo(f"{found.number} {service} {entry.total}")


@settle.command("pay")
@click.argument("party")
@click.argument("amount")
@click.option("--method", default="wire", type=click.Choice([m.value for m in Method]))
@click.option("--reference", default=None, help="Cheque or wire number.")
@click.option("--sale", "sale_code", default=None)
@pass_context
def pay(
    ctx: Context,
    party: str,
    amount: str,
    method: str,
    reference: str | None,
    sale_code: str | None,
) -> None:
    """Take money in from a buyer."""
    payment = PaymentService(ctx.open()).receive(
        party,
        Money.parse(amount),
        method=Method(method),
        reference=reference,
        sale_code=sale_code,
    )
    click.echo(f"{payment.id}  {payment.amount} from {party} by {method}")


@settle.command("statement")
@click.argument("party")
@click.option("--side", default="buyer", type=click.Choice([s.value for s in Side]))
@click.option("--sale", "sale_code", default=None)
@pass_context
def statement(ctx: Context, party: str, side: str, sale_code: str | None) -> None:
    """A buyer invoice or a seller statement."""
    service = StatementService(ctx.open())
    built = service.build(Side(side), party, sale_code=sale_code)
    click.echo(f"{side} statement for {party} as at {built.built_at:%Y-%m-%d %H:%M}")
    rows = [[line.reference, line.memo, line.amount] for line in built.lines]
    for text in STATEMENT_TABLE.render(rows):
        click.echo(text)
    for text in key_values(
        [("charges", built.charges), ("credits", built.credits), ("balance", built.balance)]
    ):
        click.echo(text)


@settle.command("holds")
@click.argument("token")
@pass_context
def holds(ctx: Context, token: str) -> None:
    """Why a seller has not been paid yet."""
    conn = ctx.open()
    consignment_id = lookup.consignment_id(ctx, token)
    position = ProceedsService(conn).position(consignment_id)
    click.echo(f"{consignment_id}  seller {position.seller_id}  gross {position.gross}")
    for hold in position.holds:
        click.echo(f"  {hold.detail}")
    click.echo(f"{len(position.holds)} holds")


@settle.command("release")
@click.argument("token")
@click.option("--lien", default=None, help="Payoff to come off the top.")
@click.option("--lienholder", default=None)
@click.option("--method", default="cheque", type=click.Choice([m.value for m in Method]))
@click.option("--reference", default=None)
@pass_context
def release(
    ctx: Context,
    token: str,
    lien: str | None,
    lienholder: str | None,
    method: str,
    reference: str | None,
) -> None:
    """Pay a seller, once nothing is holding it."""
    conn = ctx.open()
    consignment_id = lookup.consignment_id(ctx, token)
    paid = ProceedsService(conn).release(
        consignment_id,
        lien=Money.parse(lien) if lien else None,
        lienholder=lienholder,
        method=Method(method),
        reference=reference,
    )
    click.echo(f"{consignment_id} released {paid}")


@settle.command("queue")
@pass_context
def queue(ctx: Context) -> None:
    """What can be paid and what is stuck."""
    service = ProceedsService(ctx.open())
    ready = service.ready_to_release()
    stuck = service.held()
    for consignment_id in ready:
        click.echo(f"ready    {consignment_id}")
    for consignment_id, reasons in sorted(stuck.items()):
        click.echo(f"held     {consignment_id}  {reasons[0].detail}")
    click.echo(f"{len(ready)} ready, {len(stuck)} held")


@settle.command("balances")
@pass_context
def balances(ctx: Context) -> None:
    """Who owes us and who we owe."""
    service = StatementService(ctx.open())
    table = Table([Column("party", 12), Column("side", 8), money_column("balance", 13)])
    rows: list[list[object]] = []
    for party, amount in service.outstanding_buyers().items():
        rows.append([party, "buyer", amount])
    for party, amount in service.unpaid_sellers().items():
        rows.append([party, "seller", amount])
    for text in table.render(rows):
        click.echo(text)
    click.echo(f"{len(rows)} accounts with a balance")


@settle.command("trial-balance")
@pass_context
def trial_balance(ctx: Context) -> None:
    """Every account, and the proof it all nets to nothing."""
    ledger = Ledger(ctx.open())
    table = Table([Column("account", 24), money_column("signed", 15)])
    rows = [[account.value, amount] for account, amount in ledger.trial_balance().items()]
    for text in table.render(rows):
        click.echo(text)
    click.echo("balanced" if ledger.is_balanced() else "OUT OF BALANCE")
    if not ledger.is_balanced():
        raise SystemExit(1)


@settle.command("credit")
@click.argument("buyer")
@click.option(
    "--open",
    "open_line",
    nargs=3,
    default=None,
    metavar="ID LENDER LIMIT",
    help="Open a line for this buyer.",
)
@click.option("--limit", "new_limit", nargs=2, default=None, metavar="ID AMOUNT")
@click.option("--suspend", "suspend_id", default=None, metavar="ID")
@click.option("--reinstate", "reinstate_id", default=None, metavar="ID")
@pass_context
def credit(
    ctx: Context,
    buyer: str,
    open_line: tuple[str, str, str] | None,
    new_limit: tuple[str, str] | None,
    suspend_id: str | None,
    reinstate_id: str | None,
) -> None:
    """Show or change a buyer's floor plan lines."""
    service = FloorplanService(ctx.open())
    if open_line:
        line_id, lender, limit = open_line
        service.open_line(line_id, buyer, lender, Money.parse(limit))
    if new_limit:
        service.set_limit(new_limit[0], Money.parse(new_limit[1]))
    if suspend_id:
        service.set_status(suspend_id, LineStatus.SUSPENDED)
    if reinstate_id:
        service.set_status(reinstate_id, LineStatus.ACTIVE)

    table = Table(
        [
            Column("line", 8),
            Column("lender", 24),
            money_column("limit", 14),
            Column("status", 10),
        ]
    )
    lines = service.lines_for(buyer)
    for text in table.render([[ln.id, ln.lender, ln.limit, ln.status.value] for ln in lines]):
        click.echo(text)
    capacity = service.capacity(buyer)
    click.echo(capacity.explain())


@settle.command("period")
@click.argument("action", type=PERIOD_ACTIONS)
@click.option("--from", "starts_on", default=None, help="First day, for open.")
@click.option("--to", "ends_on", default=None, help="Last day, for open.")
@click.option("--id", "period_id", default=None)
@click.option("--by", "closed_by", default=None)
@click.option("--note", default=None)
@pass_context
def period(
    ctx: Context,
    action: str,
    starts_on: str | None,
    ends_on: str | None,
    period_id: str | None,
    closed_by: str | None,
    note: str | None,
) -> None:
    """Open, check and close an accounting period."""
    service = PeriodService(ctx.open())
    if action == "open":
        if not starts_on or not ends_on:
            raise click.UsageError("open needs --from and --to")
        opened = service.open_period(date.fromisoformat(starts_on), date.fromisoformat(ends_on))
        click.echo(f"{opened.id} open, {opened.starts_on} to {opened.ends_on}")
        return
    if action == "list":
        table = Table(
            [Column("period", 10), Column("from", 12), Column("to", 12), Column("status", 9)]
        )
        rows = [[p.id, p.starts_on, p.ends_on, p.status.value] for p in service.periods()]
        for line in table.render(rows):
            click.echo(line)
        return
    if not period_id:
        raise click.UsageError(f"{action} needs --id")
    if action == "checks":
        failed = 0
        for check in service.checks(period_id):
            click.echo(str(check))
            failed += 0 if check.passed else 1
        click.echo(f"{failed} checks failed")
        if failed:
            raise SystemExit(1)
        return
    if action == "begin-close":
        click.echo(f"{service.begin_close(period_id).id} is closing, entries are stopped")
        return
    if action == "reopen":
        click.echo(f"{service.reopen(period_id).id} is open again")
        return
    closed = service.close(period_id, closed_by=closed_by or "unknown", note=note)
    click.echo(f"{closed.id} closed by {closed.closed_by}")


@settle.command("notices")
@click.option("--party", default=None)
@click.option("--stuck", "only_stuck", is_flag=True)
@pass_context
def notices(ctx: Context, party: str | None, only_stuck: bool) -> None:
    """What is waiting to go out."""
    queue = NoticeQueue(ctx.open())
    if only_stuck:
        found = queue.stuck()
    elif party:
        found = queue.for_party(party)
    else:
        found = queue.pending()
    table = Table(
        [
            Column("notice", 13),
            Column("kind", 16),
            Column("party", 10),
            Column("status", 10),
            Column("subject", 40),
        ]
    )
    rows = [[n.id, n.kind, n.party_id, n.status.value, n.subject] for n in found]
    for line in table.render(rows):
        click.echo(line)
    counts = queue.counts()
    summary = ", ".join(
        f"{status.value} {count}"
        for status, count in sorted(counts.items(), key=lambda pair: pair[0].value)
    )
    click.echo(f"{len(rows)} listed. {summary or 'nothing queued'}")


@settle.command("reconcile")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--apply", "really", is_flag=True, help="Take in the confident matches.")
@pass_context
def reconcile(ctx: Context, path: str, really: bool) -> None:
    """Match a bank file against what buyers owe.

    Nothing is applied unless asked for, and only the confident matches are
    applied even then. An ambiguous line goes in front of a person.
    """
    reconciler = Reconciler(ctx.open())
    result = reconciler.match(parse(Path(path).read_text("utf-8")))
    for match in result.matches:
        click.echo(str(match))
        if match.candidates:
            click.echo(f"    could be {', '.join(match.candidates)}")
    click.echo(result.summary())
    if really:
        click.echo(f"took in {reconciler.apply(result)} payments")


@settle.command("adjust")
@click.argument("kind", type=click.Choice([k.value for k in AdjustmentKind]))
@click.argument("party")
@click.argument("amount")
@click.option("--reason", required=True)
@click.option("--by", "raised_by", required=True)
@click.option("--approved-by", default=None, help="Needed over the threshold.")
@click.option("--side", default="buyer", type=click.Choice([s.value for s in Side]))
@pass_context
def adjust(
    ctx: Context,
    kind: str,
    party: str,
    amount: str,
    reason: str,
    raised_by: str,
    approved_by: str | None,
    side: str,
) -> None:
    """Raise a credit note or a write off."""
    service = AdjustmentService(ctx.open())
    money = Money.parse(amount)
    if AdjustmentKind(kind) is AdjustmentKind.WRITE_OFF:
        adjustment = service.write_off(
            party, money, reason=reason, raised_by=raised_by, approved_by=approved_by
        )
    else:
        adjustment = service.credit_note(
            party,
            money,
            reason=reason,
            raised_by=raised_by,
            approved_by=approved_by,
            side=Side(side),
        )
    click.echo(f"{adjustment.reference}  {adjustment.kind.value}  {adjustment.amount}")


@settle.command("adjustments")
@click.argument("party")
@pass_context
def adjustments(ctx: Context, party: str) -> None:
    """Every credit note and write off against one account."""
    service = AdjustmentService(ctx.open())
    for reference, amount, memo in service.history(party):
        click.echo(f"{reference}  {amount:>12}  {memo}")
    click.echo(f"{service.written_off()} written off in total")
