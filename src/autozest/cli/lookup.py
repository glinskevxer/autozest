"""Turning whatever somebody typed into the thing they meant.

Five command groups all had their own three line version of this and they had
already drifted: two of them accepted a gate pass and three did not, which is
the sort of difference nobody notices until the person at the gate uses the
wrong command.

The rule is one rule. A token is a consignment reference, a vin or a gate pass,
whichever it turns out to be, and a run is a sale code and a run number. The
lookups live here so there is one answer to what a token means.
"""

from __future__ import annotations

from autozest.block.models import Run
from autozest.block.sales import SaleRepository
from autozest.cli.context import Context
from autozest.consignment.intake import IntakeService
from autozest.consignment.models import Consignment


def consignment(ctx: Context, token: str) -> Consignment:
    """The consignment behind a reference, a vin or a gate pass."""
    return IntakeService(ctx.open()).find(token)


def consignment_id(ctx: Context, token: str) -> str:
    """Just the reference, which is what most callers want."""
    return consignment(ctx, token).id


def run(ctx: Context, sale_code: str, number: str) -> Run:
    """The run behind a sale code and a run number such as C214."""
    return SaleRepository(ctx.open()).require_run(sale_code, number)


def run_id(ctx: Context, sale_code: str, number: str) -> int:
    """The run's row id, which the services key on."""
    found = run(ctx, sale_code, number)
    assert found.id is not None
    return found.id
