-- The sale itself: lanes, the order units run in, and what happened when they
-- crossed the block.
--
-- A run is the join between a consignment and a sale. It carries the light,
-- because the light is decided when the unit is catalogued and must not move
-- afterwards. An arbitration months later asks what light the unit ran under,
-- and recomputing it from today's announcements would give the wrong answer if
-- anything was called after the fact.

CREATE TABLE sale (
    code         TEXT PRIMARY KEY,
    sale_date    TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('planned', 'open', 'closed', 'settled')),
    opened_at    TEXT,
    closed_at    TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE lane (
    sale_code    TEXT NOT NULL REFERENCES sale (code) ON DELETE CASCADE,
    letter       TEXT NOT NULL CHECK (length(letter) = 1),
    capacity     INTEGER NOT NULL CHECK (capacity > 0),
    auctioneer   TEXT,
    PRIMARY KEY (sale_code, letter)
);

CREATE TABLE run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_code      TEXT NOT NULL,
    lane           TEXT NOT NULL,
    position       INTEGER NOT NULL CHECK (position > 0),
    consignment_id TEXT NOT NULL REFERENCES consignment (id),
    light          TEXT NOT NULL CHECK (light IN ('green', 'yellow', 'red', 'blue')),
    status         TEXT NOT NULL CHECK (status IN (
                       'scheduled', 'sold', 'no_sale', 'if_bid', 'pulled')),
    hammer_cents   INTEGER CHECK (hammer_cents IS NULL OR hammer_cents >= 0),
    buyer_id       TEXT REFERENCES account (id),
    ran_at         TEXT,
    FOREIGN KEY (sale_code, lane) REFERENCES lane (sale_code, letter) ON DELETE CASCADE,
    UNIQUE (sale_code, lane, position),
    UNIQUE (sale_code, consignment_id)
);

CREATE INDEX run_consignment_idx ON run (consignment_id);
CREATE INDEX run_buyer_idx ON run (buyer_id, status);

-- Every bid, kept whether or not it won. The losing bids are what a seller
-- looks at when deciding whether to take an if bid, and they are the only
-- evidence of what the unit was actually worth on the night.
CREATE TABLE bid (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    buyer_id     TEXT REFERENCES account (id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    kind         TEXT NOT NULL CHECK (kind IN (
                     'live', 'proxy', 'floor', 'counter', 'accept', 'decline')),
    made_at      TEXT NOT NULL
);

CREATE INDEX bid_run_idx ON bid (run_id, made_at);
