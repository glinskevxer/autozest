-- What each state does with a title.
--
-- Fifty sets of rules and no two the same. What matters here is only what
-- changes our behaviour: how long a duplicate takes, whether the state
-- recognises a rebuilt brand at all, and whether it needs a physical
-- inspection before it will retitle.

CREATE TABLE title_state_rule (
    state              TEXT PRIMARY KEY,
    duplicate_days     INTEGER NOT NULL CHECK (duplicate_days >= 0),
    transfer_days      INTEGER NOT NULL CHECK (transfer_days >= 0),
    recognises_rebuilt INTEGER NOT NULL DEFAULT 1 CHECK (recognises_rebuilt IN (0, 1)),
    needs_inspection   INTEGER NOT NULL DEFAULT 0 CHECK (needs_inspection IN (0, 1)),
    note               TEXT
);
