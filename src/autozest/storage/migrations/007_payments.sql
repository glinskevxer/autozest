-- Money actually moving, as opposed to money being owed.
--
-- A payment is kept separately from its ledger entry because the two answer
-- different questions. The ledger says what the balance is. This says that a
-- cheque numbered 40182 arrived on the fourteenth, which is what somebody
-- needs when the bank says it did not.

CREATE TABLE payment (
    id           TEXT PRIMARY KEY,
    party_id     TEXT NOT NULL REFERENCES account (id),
    side         TEXT NOT NULL CHECK (side IN ('buyer', 'seller')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    method       TEXT NOT NULL CHECK (method IN (
                     'cash', 'cheque', 'wire', 'card', 'floor_plan', 'offset')),
    reference    TEXT,
    sale_code    TEXT REFERENCES sale (code),
    entry_id     INTEGER REFERENCES ledger_entry (id),
    received_at  TEXT NOT NULL,
    note         TEXT
);

CREATE INDEX payment_party_idx ON payment (party_id, side, received_at);
CREATE INDEX payment_sale_idx ON payment (sale_code);
