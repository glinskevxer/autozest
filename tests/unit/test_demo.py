import sqlite3
from datetime import date

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import AccountRepository
from autozest.reporting import recap as recap_report
from autozest.tools.demo import build
from autozest.vin import Vin


def test_the_same_seed_builds_the_same_sale(db: sqlite3.Connection) -> None:
    from autozest.storage.db import connect_memory
    from autozest.storage.migrator import migrate

    first = build(db, units=8, seed=7)
    other = connect_memory()
    migrate(other)
    second = build(other, units=8, seed=7)
    assert first == second
    left = [r.consignment_id for r in SaleRepository(db).runs(first.sale_code)]
    right = [r.consignment_id for r in SaleRepository(other).runs(second.sale_code)]
    assert left == right
    other.close()


def test_a_different_seed_builds_a_different_sale(db: sqlite3.Connection) -> None:
    from autozest.storage.db import connect_memory
    from autozest.storage.migrator import migrate

    build(db, units=12, seed=1)
    other = connect_memory()
    migrate(other)
    build(other, units=12, seed=2)
    left = [row["vin"] for row in db.execute("SELECT vin FROM vehicle ORDER BY vin")]
    right = [row["vin"] for row in other.execute("SELECT vin FROM vehicle ORDER BY vin")]
    assert left != right
    other.close()


def test_every_generated_vin_passes_its_check_digit(db: sqlite3.Connection) -> None:
    build(db, units=20, seed=11)
    rows = db.execute("SELECT vin, model_year FROM vehicle").fetchall()
    assert len(rows) == 20
    for row in rows:
        vin = Vin.parse(row["vin"])
        assert vin.check_digit_matches
        assert vin.model_year == row["model_year"]


def test_the_sale_lands_on_the_day_it_was_asked_for(db: sqlite3.Connection) -> None:
    built = build(db, units=4, seed=3, sale_day=date(2025, 1, 14))
    assert built.sale_code == "S-20250114"
    assert SaleRepository(db).get(built.sale_code).sale_date == date(2025, 1, 14)


def test_the_accounts_are_all_there(db: sqlite3.Connection) -> None:
    built = build(db, units=4, seed=3)
    assert AccountRepository(db).count() == built.accounts


def test_not_everything_sells(db: sqlite3.Connection) -> None:
    built = build(db, units=40, seed=5)
    assert built.sold > 0
    assert built.no_sale > 0
    assert built.sold + built.no_sale == built.units


def test_the_recap_reads_off_a_generated_sale(db: sqlite3.Connection) -> None:
    built = build(db, units=30, seed=9)
    recap = recap_report.build(db, built.sale_code)
    assert recap.catalogued == 30
    assert recap.sold == built.sold
    assert recap.gross.cents > 0
    assert recap.sell_through > 0


def test_the_generated_sale_uses_more_than_one_light(db: sqlite3.Connection) -> None:
    built = build(db, units=40, seed=13)
    recap = recap_report.build(db, built.sale_code)
    used = {light for light, count in recap.by_light.items() if count}
    assert len(used) >= 2


def test_every_run_has_landed_somewhere(db: sqlite3.Connection) -> None:
    built = build(db, units=16, seed=17)
    for run in SaleRepository(db).runs(built.sale_code):
        assert run.status is not RunStatus.SCHEDULED
        assert run.status is not RunStatus.IF_BID
