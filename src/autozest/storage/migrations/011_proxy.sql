-- Absentee bids left with the block before a unit runs.
--
-- A buyer who cannot be in the room leaves a maximum. The block bids on their
-- behalf, one increment at a time, and stops at their number. The maximum is
-- never shown, which is the whole point of leaving one.

CREATE TABLE proxy_bid (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    buyer_id    TEXT NOT NULL REFERENCES account (id),
    maximum_cents INTEGER NOT NULL CHECK (maximum_cents > 0),
    placed_at   TEXT NOT NULL,
    withdrawn_at TEXT
);

-- Partial, so a buyer who pulls their maximum can leave a different one. A
-- plain unique index locks them out of the run for the rest of the night.
CREATE UNIQUE INDEX proxy_bid_live_idx ON proxy_bid (run_id, buyer_id)
    WHERE withdrawn_at IS NULL;

CREATE INDEX proxy_bid_run_idx ON proxy_bid (run_id, maximum_cents);
