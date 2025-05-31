"""Guards on the things that got slow before.

Not benchmarks. A benchmark on shared hardware is a flaky test, and these are
deliberately loose: they catch a change that makes something ten times worse,
not one that makes it twice as slow. Every threshold here has a story behind
it, and every story is a screen somebody waited in front of.

They are counted in queries rather than in seconds wherever a count will do,
because a query count is the same on every machine and a second is not.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.reporting import aging, dashboard
from autozest.reporting import recap as recap_report
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side, StatementService
from autozest.storage.db import connect_memory
from autozest.storage.migrator import migrate
from autozest.tools.demo import build as build_demo


@contextmanager
def counting(conn: sqlite3.Connection) -> Iterator[list[str]]:
    """Every statement the block runs, so a count can be asserted on."""
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        yield seen
    finally:
        conn.set_trace_callback(None)


@pytest.fixture(scope="module")
def busy() -> Iterator[sqlite3.Connection]:
    """A sale with a couple of hundred units in it, built once."""
    conn = connect_memory()
    migrate(conn)
    built = build_demo(conn, units=200, seed=31)
    posting = PostingService(conn)
    posting.post_sale_batch(
        [
            run.id
            for run in SaleRepository(conn).runs(built.sale_code)
            if run.id and run.status is RunStatus.SOLD
        ]
    )
    yield conn
    conn.close()


def test_the_run_sheet_is_one_query(busy: sqlite3.Connection) -> None:
    """It used to be one lookup per run. Three hundred units, three hundred trips."""
    sales = SaleRepository(busy)
    code = sales.recent(1)[0].code
    with counting(busy) as statements:
        lines = sales.run_sheet(code)
    assert len(lines) > 50
    assert len(statements) == 1


def test_a_seller_statement_does_not_walk_the_ledger(busy: sqlite3.Connection) -> None:
    with counting(busy) as statements:
        StatementService(busy).build(Side.SELLER, "d-100")
    assert len(statements) <= 2


def test_the_aged_list_is_one_pass(busy: sqlite3.Connection) -> None:
    with counting(busy) as statements:
        aging.build(busy)
    assert len(statements) <= 3


def test_the_recap_of_a_full_sale_is_quick(busy: sqlite3.Connection) -> None:
    sales = SaleRepository(busy)
    code = sales.recent(1)[0].code
    started = time.perf_counter()
    recap = recap_report.build(busy, code)
    elapsed = time.perf_counter() - started
    assert recap.catalogued == 200
    assert elapsed < 2.0


def test_the_dashboard_of_a_full_sale_is_quick(busy: sqlite3.Connection) -> None:
    sales = SaleRepository(busy)
    code = sales.recent(1)[0].code
    started = time.perf_counter()
    board = dashboard.build(busy, code)
    elapsed = time.perf_counter() - started
    assert board.sold > 0
    assert elapsed < 4.0


def test_building_two_hundred_units_stays_reasonable() -> None:
    """The demo generator is also the load test, so it has to stay usable."""
    conn = connect_memory()
    migrate(conn)
    started = time.perf_counter()
    built = build_demo(conn, units=200, seed=7)
    elapsed = time.perf_counter() - started
    assert built.units == 200
    assert elapsed < 30.0
    conn.close()
