-- Appeals against an arbitration decision.
--
-- One tier and one only. A second tier sounds fairer and in practice means
-- every decision gets appealed twice, the desk stops deciding anything
-- properly because it will be reviewed anyway, and a claim takes six weeks.

CREATE TABLE claim_appeal (
    id          TEXT PRIMARY KEY,
    claim_id    TEXT NOT NULL REFERENCES arbitration_claim (id) ON DELETE CASCADE,
    raised_by   TEXT NOT NULL REFERENCES account (id),
    grounds     TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'upheld', 'overturned', 'varied',
                                                'withdrawn')),
    decided_by  TEXT,
    decision_note TEXT,
    new_outcome_cents INTEGER CHECK (new_outcome_cents IS NULL OR new_outcome_cents >= 0),
    raised_at   TEXT NOT NULL,
    decided_at  TEXT,
    UNIQUE (claim_id)
);

CREATE INDEX claim_appeal_status_idx ON claim_appeal (status, raised_at);
