import sqlite3

from autozest.config import Settings
from autozest.health import Severity, examine


def test_a_fresh_database_is_healthy(db: sqlite3.Connection) -> None:
    report = examine(db)
    assert report.can_open
    assert report.failures == ()
    assert report.summary() == "healthy"


def test_every_check_has_a_name_and_a_detail(db: sqlite3.Connection) -> None:
    for check in examine(db).checks:
        assert check.name
        assert check.detail
        assert str(check).startswith("ok")


def test_an_unbalanced_ledger_is_a_hard_failure(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'broken', 'test', '2024-03-12T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    report = examine(db)
    assert not report.can_open
    assert "not usable" in report.summary()
    assert any(check.name == "ledger" for check in report.hard_failures)


def test_a_lapsed_certificate_is_soft(db: sqlite3.Connection) -> None:
    from datetime import date

    from autozest.catalog.accounts import Account, AccountClass, AccountRepository
    from autozest.settlement.tax import Certificate, ExemptionKind, TaxService

    AccountRepository(db).add(
        Account(
            id="d-200",
            name="Kestrel",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-2",
            state="TX",
        )
    )
    TaxService(db).add_certificate(
        Certificate(
            id="X-1",
            party_id="d-200",
            state="TX",
            kind=ExemptionKind.RESALE,
            number="RS-1",
            issued_on=date(2020, 1, 1),
            expires_on=date(2021, 1, 1),
        )
    )
    report = examine(db)
    assert report.can_open
    assert "things to look at" in report.summary()
    assert any(check.severity is Severity.SOFT and not check.passed for check in report.checks)


def test_a_stuck_notice_is_soft(db: sqlite3.Connection) -> None:
    from autozest.catalog.accounts import Account, AccountClass, AccountRepository

    AccountRepository(db).add(
        Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC)
    )
    db.execute(
        "INSERT INTO notice (id, kind, party_id, subject, body, channel, status, "
        "queued_at, attempts) VALUES ('N-1', 'x', 'p-1', 's', 'b', 'email', 'failed', "
        "'2024-03-12T00:00:00+00:00', 4)"
    )
    report = examine(db)
    assert report.can_open
    assert any("notices have failed" in check.detail for check in report.failures)


def test_the_calendar_check_reports_the_hours(db: sqlite3.Connection) -> None:
    report = examine(db, Settings())
    calendar_check = next(check for check in report.checks if check.name == "calendar")
    assert "opens 08:00" in calendar_check.detail
