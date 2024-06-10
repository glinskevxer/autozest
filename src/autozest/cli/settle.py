"""``az settle``.

The settlement office runs the day off four of these. ``post`` after the sale
closes, ``pay`` as the money arrives, ``holds`` when a seller rings up, and
``release`` when the answer is that nothing is stopping it.

``holds`` is the one that gets used most and it is written to be read out over
a phone: one reason per line, in the order they are usually cleared.
"""

from __future__ import annotations

import click

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.cli.context import Context
from autozest.consignment.intake import IntakeService
from autozest.money import Money
from autozest.reporting.text import Column, Table, key_values, money_column
from autozest.settlement.fees import Service
from autozest.settlement.ledger import Ledger
from autozest.settlement.payments import Method, PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.settlement.statements import Side, StatementService

pass_context = click.make_pass_decorator(Context)

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
    posting = PostingService(conn)
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
    entry = PostingService(conn).post_service(
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
    consignment_id = IntakeService(conn).find(token).id
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
    consignment_id = IntakeService(conn).find(token).id
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
