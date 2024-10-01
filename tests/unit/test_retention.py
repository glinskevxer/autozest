from datetime import date, timedelta
from pathlib import Path

import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import PolicyViolation
from autozest.money import Money
from autozest.retention import PROTECTED, RETENTION_DAYS, archive_sale, candidates, prune
from autozest.storage.db import connect
from tests.conftest import Floor


def sold(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    return run_id


def test_nothing_protected_is_ever_a_candidate(floor: Floor) -> None:
    for candidate in candidates(floor.conn):
        assert candidate.table not in PROTECTED


def test_the_ledger_is_protected() -> None:
    assert "ledger_entry" in PROTECTED
    assert "ledger_line" in PROTECTED
    assert "payment" in PROTECTED
    assert "arbitration_claim" in PROTECTED


def test_a_fresh_database_has_nothing_to_prune(floor: Floor) -> None:
    sold(floor)
    assert all(not candidate.has_work for candidate in candidates(floor.conn))
    assert prune(floor.conn).total == 0


def test_old_rows_become_candidates(floor: Floor) -> None:
    sold(floor)
    far_future = date.today() + timedelta(days=RETENTION_DAYS["bid"] + 10)
    found = {c.table: c for c in candidates(floor.conn, as_at=far_future)}
    assert found["bid"].has_work
    assert found["bid"].keep_days == RETENTION_DAYS["bid"]


def test_a_dry_run_removes_nothing(floor: Floor) -> None:
    sold(floor)
    far_future = date.today() + timedelta(days=RETENTION_DAYS["bid"] + 10)
    result = prune(floor.conn, as_at=far_future)
    assert result.dry_run
    assert result.total > 0
    assert floor.conn.execute("SELECT COUNT(*) AS n FROM bid").fetchone()["n"] > 0


def test_a_real_prune_removes_the_old_rows(floor: Floor) -> None:
    sold(floor)
    far_future = date.today() + timedelta(days=RETENTION_DAYS["bid"] + 10)
    result = prune(floor.conn, as_at=far_future, dry_run=False)
    assert not result.dry_run
    assert floor.conn.execute("SELECT COUNT(*) AS n FROM bid").fetchone()["n"] == 0
    assert floor.conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 1


def test_the_retention_table_only_lists_prunable_things() -> None:
    assert set(RETENTION_DAYS).isdisjoint(PROTECTED)


def test_an_open_sale_is_not_archived(floor: Floor, tmp_path: Path) -> None:
    sold(floor)
    with pytest.raises(PolicyViolation) as caught:
        archive_sale(floor.conn, floor.code, tmp_path / "archive.sqlite3")
    assert caught.value.rule == "retention.sale_open"


def test_archiving_a_closed_sale(floor: Floor, tmp_path: Path) -> None:
    sold(floor)
    floor.sales.close_sale(floor.code)
    target = tmp_path / "S-20240312.sqlite3"
    archive = archive_sale(floor.conn, floor.code, target)
    assert target.exists()
    assert archive.tables["run"] == 1
    assert archive.tables["bid"] == 1
    assert archive.rows > 0


def test_archiving_leaves_the_live_database_alone(floor: Floor, tmp_path: Path) -> None:
    sold(floor)
    floor.sales.close_sale(floor.code)
    archive_sale(floor.conn, floor.code, tmp_path / "archive.sqlite3")
    assert floor.conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 1


def test_the_archive_is_readable_on_its_own(floor: Floor, tmp_path: Path) -> None:
    sold(floor)
    floor.sales.close_sale(floor.code)
    target = tmp_path / "archive.sqlite3"
    archive_sale(floor.conn, floor.code, target)
    out = connect(target, create=False)
    rows = out.execute("SELECT * FROM run").fetchall()
    assert len(rows) == 1
    assert rows[0]["sale_code"] == floor.code
    out.close()
