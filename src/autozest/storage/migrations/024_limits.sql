-- Caps a buyer agrees to, or has imposed on them.
--
-- Two different things share a table because they behave identically at the
-- block. A limit somebody asked for and a limit the credit desk set both stop
-- a bid, and only the reason differs. Keeping the reason is what makes the
-- conversation afterwards possible.

CREATE TABLE purchase_limit (
    id         TEXT PRIMARY KEY,
    buyer_id   TEXT NOT NULL REFERENCES account (id),
    scope      TEXT NOT NULL CHECK (scope IN ('per_unit', 'per_sale', 'per_day')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    imposed    INTEGER NOT NULL DEFAULT 0 CHECK (imposed IN (0, 1)),
    reason     TEXT NOT NULL,
    set_by     TEXT NOT NULL,
    set_on     TEXT NOT NULL,
    lifted_on  TEXT,
    UNIQUE (buyer_id, scope, lifted_on)
);

CREATE INDEX purchase_limit_buyer_idx ON purchase_limit (buyer_id, lifted_on);
