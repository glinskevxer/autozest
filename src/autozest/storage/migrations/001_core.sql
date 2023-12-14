-- Accounts, the units themselves, and the odometer history behind a unit.
--
-- An account is a party we deal with, and the same party is very often on both
-- sides: a franchise dealer sells its trades here and buys its used stock back
-- out of the same lanes. So the side is a property of the transaction, not of
-- the account, and only the class lives here.

CREATE TABLE account (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    class        TEXT NOT NULL CHECK (class IN (
                     'franchise_dealer', 'independent_dealer', 'fleet_lease',
                     'finance', 'factory', 'wholesaler', 'public')),
    status       TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'suspended', 'closed')),
    tax_id       TEXT,
    dealer_licence TEXT,
    state        TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (name, state)
);

CREATE INDEX account_class_idx ON account (class, status);

CREATE TABLE vehicle (
    vin          TEXT PRIMARY KEY,
    model_year   INTEGER NOT NULL CHECK (model_year BETWEEN 1900 AND 2100),
    make         TEXT NOT NULL,
    model        TEXT NOT NULL,
    series       TEXT,
    body_style   TEXT,
    drivetrain   TEXT CHECK (drivetrain IN ('fwd', 'rwd', 'awd', '4wd')),
    fuel         TEXT CHECK (fuel IN ('gasoline', 'diesel', 'hybrid', 'electric', 'other')),
    exterior     TEXT,
    interior     TEXT,
    check_digit_ok INTEGER NOT NULL DEFAULT 1 CHECK (check_digit_ok IN (0, 1)),
    created_at   TEXT NOT NULL
);

CREATE INDEX vehicle_year_make_idx ON vehicle (model_year, make, model);

-- The readings are kept as a history rather than a single number on the
-- vehicle. A rollback is only visible as a sequence, and the sequence is the
-- evidence an arbitration hangs on months later.
CREATE TABLE odometer_reading (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vin          TEXT NOT NULL REFERENCES vehicle (vin) ON DELETE CASCADE,
    miles        INTEGER NOT NULL CHECK (miles >= 0),
    read_on      TEXT NOT NULL,
    source       TEXT NOT NULL,
    digits       INTEGER NOT NULL DEFAULT 6 CHECK (digits IN (5, 6, 7)),
    recorded_at  TEXT NOT NULL
);

CREATE INDEX odometer_reading_vin_idx ON odometer_reading (vin, recorded_at);
