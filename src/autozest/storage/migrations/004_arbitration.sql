-- Arbitration: a buyer's complaint about a unit they have already paid for.
--
-- The claim keeps the policy version it was opened under. Policy moves every
-- January and a claim opened in December is decided on December's rules, so
-- storing the version is the only way the decision can be defended two years
-- later when nobody remembers what the windows used to be.

CREATE TABLE arbitration_claim (
    id             TEXT PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES run (id),
    claimant_id    TEXT NOT NULL REFERENCES account (id),
    defect_class   TEXT NOT NULL,
    summary        TEXT NOT NULL,
    claimed_cents  INTEGER CHECK (claimed_cents IS NULL OR claimed_cents >= 0),
    status         TEXT NOT NULL CHECK (status IN (
                       'open', 'under_review', 'resolved', 'denied', 'withdrawn')),
    resolution     TEXT CHECK (resolution IS NULL OR resolution IN (
                       'buy_back', 'price_adjustment', 'repair_allowance', 'split', 'denied')),
    outcome_cents  INTEGER CHECK (outcome_cents IS NULL OR outcome_cents >= 0),
    reason         TEXT,
    policy_version TEXT NOT NULL,
    opened_at      TEXT NOT NULL,
    decided_at     TEXT
);

CREATE INDEX arbitration_claim_run_idx ON arbitration_claim (run_id, status);
CREATE INDEX arbitration_claim_claimant_idx ON arbitration_claim (claimant_id, status);

-- Everything that happened to a claim, in order. The desk works from this,
-- and so does anyone arguing about it later.
CREATE TABLE claim_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id   TEXT NOT NULL REFERENCES arbitration_claim (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    detail     TEXT,
    actor      TEXT NOT NULL,
    made_at    TEXT NOT NULL
);

CREATE INDEX claim_event_claim_idx ON claim_event (claim_id, id);

-- A post sale inspection is a service the buyer buys. It matters here because
-- ordering one stops the clock on the mechanical classes while it is run.
CREATE TABLE post_sale_inspection (
    id           TEXT PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES run (id),
    ordered_at   TEXT NOT NULL,
    completed_at TEXT,
    passed       INTEGER CHECK (passed IS NULL OR passed IN (0, 1)),
    findings     TEXT
);

CREATE UNIQUE INDEX post_sale_inspection_run_idx ON post_sale_inspection (run_id);
