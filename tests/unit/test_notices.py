import sqlite3
from datetime import date

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.errors import NotFound, ValidationError
from autozest.money import Money
from autozest.notices import (
    MAX_ATTEMPTS,
    Channel,
    NoticeQueue,
    NoticeStatus,
    claim_decided,
    payment_due,
    proceeds_released,
    title_chase,
)
from autozest.storage.db import table_names


@pytest.fixture()
def queue(db: sqlite3.Connection) -> NoticeQueue:
    AccountRepository(db).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-1",
            state="TX",
        )
    )
    return NoticeQueue(db)


def test_the_notice_table_is_there(db: sqlite3.Connection) -> None:
    assert "notice" in table_names(db)


def test_queueing_and_reading_back(queue: NoticeQueue) -> None:
    subject, body = title_chase("C-2024-000412", 21)
    notice = queue.queue("title.chase", "d-100", subject, body, reference="C-2024-000412")
    assert notice.status is NoticeStatus.QUEUED
    assert notice.channel is Channel.EMAIL
    assert "21 days" in notice.body
    assert queue.pending() == [notice]


def test_a_notice_needs_something_to_say(queue: NoticeQueue) -> None:
    with pytest.raises(ValidationError, match="subject and a body"):
        queue.queue("x", "d-100", "  ", "body")
    with pytest.raises(ValidationError):
        queue.queue("x", "d-100", "subject", "   ")


def test_an_unknown_notice(queue: NoticeQueue) -> None:
    with pytest.raises(NotFound):
        queue.get("N-2024-999999")


def test_sending_takes_it_off_the_queue(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    sent = queue.mark_sent(notice.id)
    assert sent.status is NoticeStatus.SENT
    assert sent.sent_at is not None
    assert queue.pending() == []


def test_a_failure_counts_attempts(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    failed = queue.mark_failed(notice.id, "mailbox full")
    assert failed.attempts == 1
    assert failed.last_error == "mailbox full"
    assert not failed.is_stuck


def test_too_many_failures_needs_a_person(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    for _ in range(MAX_ATTEMPTS):
        queue.mark_failed(notice.id, "mailbox full")
        queue.retry(notice.id)
        queue.mark_failed(notice.id, "mailbox full")
    assert queue.get(notice.id).is_stuck
    assert [n.id for n in queue.stuck()] == [notice.id]


def test_retrying_something_that_did_not_fail(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    with pytest.raises(ValidationError, match="not failed"):
        queue.retry(notice.id)


def test_a_retry_goes_back_on_the_queue(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    queue.mark_failed(notice.id, "mailbox full")
    assert queue.pending() == []
    assert queue.retry(notice.id).status is NoticeStatus.QUEUED
    assert len(queue.pending()) == 1


def test_cancelling(queue: NoticeQueue) -> None:
    notice = queue.queue("x", "d-100", "subject", "body")
    assert queue.cancel(notice.id).status is NoticeStatus.CANCELLED
    assert queue.pending() == []


def test_everything_for_one_party(queue: NoticeQueue) -> None:
    queue.queue("a", "d-100", "one", "body")
    queue.queue("b", "d-100", "two", "body")
    assert len(queue.for_party("d-100")) == 2
    assert queue.for_party("d-999") == []


def test_the_counts(queue: NoticeQueue) -> None:
    first = queue.queue("a", "d-100", "one", "body")
    queue.queue("b", "d-100", "two", "body")
    queue.mark_sent(first.id)
    counts = queue.counts()
    assert counts[NoticeStatus.SENT] == 1
    assert counts[NoticeStatus.QUEUED] == 1


def test_the_templates_all_say_something_useful() -> None:
    subject, body = claim_decided("A-2024-000001", "repair_allowance", Money.parse("600.00"))
    assert "A-2024-000001" in subject
    assert "three business days to appeal" in body

    subject, body = payment_due(Money.parse("8545.00"), date(2024, 3, 14))
    assert "8,545.00" in subject
    assert "late fee" in body

    subject, body = proceeds_released("C-2024-000412", Money.parse("7965.00"))
    assert "C-2024-000412" in subject
    assert "7,965.00" in body


def test_which_channels_are_immediate() -> None:
    assert Channel.EMAIL.is_immediate
    assert Channel.SMS.is_immediate
    assert not Channel.POST.is_immediate
