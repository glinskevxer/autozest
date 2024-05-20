import sqlite3

import pytest

from autozest.catalog.accounts import Account as PartyAccount
from autozest.catalog.accounts import AccountClass, AccountRepository
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.storage.db import table_names


@pytest.fixture()
def ledger(db: sqlite3.Connection) -> Ledger:
    accounts = AccountRepository(db)
    for account_id in ("d-100", "d-200"):
        accounts.add(
            PartyAccount(
                id=account_id,
                name=f"Dealer {account_id}",
                account_class=AccountClass.INDEPENDENT_DEALER,
                dealer_licence=f"TX-{account_id}",
                state="TX",
            )
        )
    return Ledger(db)


def sale_entry(amount: str = "8200.00") -> Entry:
    money = Money.parse(amount)
    return Entry(
        reference="S-20240312/A001",
        memo="unit sold",
        source="run",
        source_id="1",
        lines=(
            debit(Account.BUYER_RECEIVABLE, money, "d-200"),
            credit(Account.SELLER_PAYABLE, money, "d-100"),
        ),
    )


def test_the_ledger_tables_are_there(db: sqlite3.Connection) -> None:
    assert "ledger_entry" in table_names(db)
    assert "ledger_line" in table_names(db)


def test_an_entry_has_to_balance() -> None:
    with pytest.raises(ValidationError, match="does not balance"):
        Entry(
            reference="x",
            memo="wrong",
            source="test",
            lines=(
                debit(Account.CASH, Money.parse("100.00")),
                credit(Account.FEE_INCOME, Money.parse("90.00")),
            ),
        )


def test_an_entry_with_no_lines() -> None:
    with pytest.raises(ValidationError, match="no lines"):
        Entry(reference="x", memo="empty", source="test")


def test_a_line_is_one_side_or_the_other() -> None:
    with pytest.raises(ValidationError, match="not both"):
        line = debit(Account.CASH, Money.parse("100.00"))
        Entry(
            reference="x",
            memo="y",
            source="test",
            lines=(
                type(line)(
                    account=Account.CASH,
                    debit=Money.parse("100.00"),
                    credit=Money.parse("100.00"),
                ),
            ),
        )


def test_a_negative_line_is_refused() -> None:
    with pytest.raises(ValidationError, match="never negative"):
        debit(Account.CASH, Money(-1))


def test_a_party_account_needs_a_party() -> None:
    with pytest.raises(ValidationError, match="kept per party"):
        debit(Account.BUYER_RECEIVABLE, Money.parse("100.00"))


def test_a_house_account_takes_no_party() -> None:
    with pytest.raises(ValidationError, match="house account"):
        credit(Account.FEE_INCOME, Money.parse("100.00"), "d-100")


def test_posting_and_reading_it_back(ledger: Ledger) -> None:
    posted = ledger.post(sale_entry())
    assert posted.id is not None
    assert posted.total == Money.parse("8200.00")
    again = ledger.get(posted.id)
    assert again.memo == "unit sold"
    assert len(again.lines) == 2
    assert again.posted_at is not None


def test_balances_are_signed_the_way_the_account_runs(ledger: Ledger) -> None:
    ledger.post(sale_entry())
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("8200.00")
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.parse("8200.00")


def test_a_balance_narrows_to_one_party(ledger: Ledger) -> None:
    ledger.post(sale_entry())
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-100") == Money.zero()
    assert ledger.balance(Account.BUYER_RECEIVABLE) == Money.parse("8200.00")


def test_fees_land_on_the_house_account(ledger: Ledger) -> None:
    ledger.post(
        Entry(
            reference="S-20240312/A001",
            memo="buyer fee",
            source="run",
            source_id="1",
            lines=(
                debit(Account.BUYER_RECEIVABLE, Money.parse("345.00"), "d-200"),
                credit(Account.FEE_INCOME, Money.parse("345.00")),
            ),
        )
    )
    assert ledger.balance(Account.FEE_INCOME) == Money.parse("345.00")


def test_the_whole_ledger_always_balances(ledger: Ledger) -> None:
    ledger.post(sale_entry())
    ledger.post(sale_entry("1200.00"))
    assert ledger.is_balanced()
    assert Money(sum(m.cents for m in ledger.trial_balance().values())) == Money.zero()


def test_a_reversal_is_posted_rather_than_a_deletion(ledger: Ledger) -> None:
    posted = ledger.post(sale_entry())
    assert posted.id is not None
    mirror = ledger.reverse(posted.id, "wrong buyer")
    assert "reversal: wrong buyer" in mirror.memo
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.zero()
    assert len(ledger.entries_for("run", "1")) == 2


def test_a_reversal_keeps_the_original_on_the_record(ledger: Ledger) -> None:
    posted = ledger.post(sale_entry())
    assert posted.id is not None
    ledger.reverse(posted.id, "wrong buyer")
    original = ledger.get(posted.id)
    assert original.total == Money.parse("8200.00")


def test_looking_up_everything_posted_for_one_thing(ledger: Ledger) -> None:
    ledger.post(sale_entry())
    ledger.post(sale_entry("345.00"))
    entries = ledger.entries_for("run", "1")
    assert len(entries) == 2
    assert ledger.entries_for("run", "999") == []


def test_an_entry_that_was_never_posted(ledger: Ledger) -> None:
    with pytest.raises(ValidationError, match="no ledger entry"):
        ledger.get(999)


def test_which_accounts_are_kept_per_party() -> None:
    assert Account.BUYER_RECEIVABLE.is_per_party
    assert Account.SELLER_PAYABLE.is_per_party
    assert Account.PROCEEDS_HELD.is_per_party
    assert not Account.FEE_INCOME.is_per_party
    assert not Account.CASH.is_per_party


def test_which_way_each_account_runs() -> None:
    assert Account.CASH.normal_side_is_debit
    assert Account.BUYER_RECEIVABLE.normal_side_is_debit
    assert not Account.SELLER_PAYABLE.normal_side_is_debit
    assert not Account.FEE_INCOME.normal_side_is_debit


def test_the_trial_balance_lists_only_touched_accounts(ledger: Ledger) -> None:
    ledger.post(sale_entry())
    balances = ledger.trial_balance()
    assert set(balances) == {Account.BUYER_RECEIVABLE, Account.SELLER_PAYABLE}
