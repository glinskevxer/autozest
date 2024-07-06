-- Floor plan credit.
--
-- Most dealers do not pay for stock out of their own money. A finance company
-- gives them a line, the auction draws on it at the fall of the hammer, and
-- the dealer repays the lender when the unit retails. What that means here is
-- that a buyer's ability to bid is a question about somebody else's balance
-- sheet, and it has to be answered before the hammer rather than afterwards.

CREATE TABLE credit_line (
    id           TEXT PRIMARY KEY,
    buyer_id     TEXT NOT NULL REFERENCES account (id),
    lender       TEXT NOT NULL,
    limit_cents  INTEGER NOT NULL CHECK (limit_cents >= 0),
    status       TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    opened_on    TEXT NOT NULL,
    note         TEXT,
    UNIQUE (buyer_id, lender)
);

CREATE INDEX credit_line_buyer_idx ON credit_line (buyer_id, status);

CREATE TABLE credit_draw (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id      TEXT NOT NULL REFERENCES credit_line (id) ON DELETE CASCADE,
    run_id       INTEGER REFERENCES run (id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    drawn_at     TEXT NOT NULL,
    repaid_at    TEXT,
    note         TEXT
);

CREATE INDEX credit_draw_line_idx ON credit_draw (line_id, repaid_at);
CREATE UNIQUE INDEX credit_draw_run_idx ON credit_draw (run_id) WHERE run_id IS NOT NULL;
