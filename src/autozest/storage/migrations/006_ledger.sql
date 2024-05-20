-- A double entry journal.
--
-- Every movement of money in the building lands here, and nothing is ever
-- edited or deleted. A mistake is corrected by posting its reverse, which is
-- how the auditors expect to find it and how a settlement clerk can see what
-- actually happened rather than what the current numbers imply.
--
-- Lines carry a party where the account is one that is kept per party. A
-- receivable is meaningless without knowing whose it is; fee income is not.

CREATE TABLE ledger_entry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reference  TEXT NOT NULL,
    memo       TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT,
    posted_at  TEXT NOT NULL,
    reverses   INTEGER REFERENCES ledger_entry (id)
);

CREATE INDEX ledger_entry_source_idx ON ledger_entry (source, source_id);
CREATE INDEX ledger_entry_reference_idx ON ledger_entry (reference);

CREATE TABLE ledger_line (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     INTEGER NOT NULL REFERENCES ledger_entry (id) ON DELETE CASCADE,
    account      TEXT NOT NULL,
    party_id     TEXT REFERENCES account (id),
    debit_cents  INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
    credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
    memo         TEXT,
    CHECK ((debit_cents = 0) <> (credit_cents = 0) OR
           (debit_cents = 0 AND credit_cents = 0))
);

CREATE INDEX ledger_line_entry_idx ON ledger_line (entry_id);
CREATE INDEX ledger_line_account_idx ON ledger_line (account, party_id);
