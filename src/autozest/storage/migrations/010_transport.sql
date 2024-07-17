-- Transport. Units arrive on a truck and a third of them leave on one.
--
-- An order is kept against the consignment rather than the run, because
-- transport is booked before anyone knows whether the unit will sell, and a
-- unit that does not sell still has to get home.

CREATE TABLE carrier (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    contact   TEXT,
    status    TEXT NOT NULL CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL
);

CREATE TABLE transport_order (
    id             TEXT PRIMARY KEY,
    consignment_id TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    carrier_id     TEXT REFERENCES carrier (id),
    direction      TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    status         TEXT NOT NULL CHECK (status IN (
                       'booked', 'collected', 'delivered', 'cancelled')),
    origin         TEXT NOT NULL,
    destination    TEXT NOT NULL,
    quoted_cents   INTEGER CHECK (quoted_cents IS NULL OR quoted_cents >= 0),
    payer          TEXT NOT NULL CHECK (payer IN ('buyer', 'seller', 'auction')),
    booked_on      TEXT NOT NULL,
    collected_on   TEXT,
    delivered_on   TEXT,
    note           TEXT
);

CREATE INDEX transport_order_consignment_idx ON transport_order (consignment_id);
CREATE INDEX transport_order_status_idx ON transport_order (status, direction);
