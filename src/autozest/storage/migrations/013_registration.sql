-- Bidder registration.
--
-- A buyer registers for a sale and gets a badge with a number on it. The
-- number is what the auctioneer calls out, so it is short, it is unique within
-- the sale, and it is reused between sales because a dealer who has had badge
-- forty for six years will not answer to anything else.

CREATE TABLE registration (
    id          TEXT PRIMARY KEY,
    sale_code   TEXT NOT NULL REFERENCES sale (code) ON DELETE CASCADE,
    buyer_id    TEXT NOT NULL REFERENCES account (id),
    badge       INTEGER NOT NULL CHECK (badge BETWEEN 1 AND 9999),
    status      TEXT NOT NULL CHECK (status IN ('active', 'blocked', 'withdrawn')),
    registered_at TEXT NOT NULL,
    blocked_reason TEXT,
    UNIQUE (sale_code, badge),
    UNIQUE (sale_code, buyer_id)
);

CREATE INDEX registration_buyer_idx ON registration (buyer_id, status);

-- The badge a buyer usually gets, so they keep the same one week to week.
CREATE TABLE preferred_badge (
    buyer_id TEXT PRIMARY KEY REFERENCES account (id),
    badge    INTEGER NOT NULL CHECK (badge BETWEEN 1 AND 9999),
    since    TEXT NOT NULL
);

CREATE UNIQUE INDEX preferred_badge_number_idx ON preferred_badge (badge);
