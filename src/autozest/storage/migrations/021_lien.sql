-- Lienholders and payoff quotes.
--
-- A lien is a claim on the title held by whoever financed the unit. It has to
-- come off before the title can go anywhere, and the amount is only good for a
-- few days because interest accrues. A stale quote paid in full still leaves a
-- lien on the title, which is the failure this table exists to stop.

CREATE TABLE lienholder (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    contact    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE payoff_quote (
    id             TEXT PRIMARY KEY,
    consignment_id TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    lienholder_id  TEXT NOT NULL REFERENCES lienholder (id),
    amount_cents   INTEGER NOT NULL CHECK (amount_cents >= 0),
    per_diem_cents INTEGER NOT NULL DEFAULT 0 CHECK (per_diem_cents >= 0),
    good_until     TEXT NOT NULL,
    quoted_on      TEXT NOT NULL,
    paid_on        TEXT,
    paid_cents     INTEGER CHECK (paid_cents IS NULL OR paid_cents >= 0)
);

CREATE INDEX payoff_quote_consignment_idx ON payoff_quote (consignment_id, quoted_on);
CREATE INDEX payoff_quote_open_idx ON payoff_quote (lienholder_id, paid_on);
