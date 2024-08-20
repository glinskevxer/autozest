-- Dealer groups.
--
-- A group buys and sells under several account numbers. That is not a
-- reporting nicety: it is the mechanism by which a seller ends up bidding on
-- their own consignment, which is the thing the own unit rule exists to stop
-- and which a per account check does not catch.

CREATE TABLE dealer_group (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    shares_credit INTEGER NOT NULL DEFAULT 0 CHECK (shares_credit IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE group_member (
    group_id   TEXT NOT NULL REFERENCES dealer_group (id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    joined_on  TEXT NOT NULL,
    left_on    TEXT,
    PRIMARY KEY (group_id, account_id)
);

CREATE INDEX group_member_account_idx ON group_member (account_id, left_on);
