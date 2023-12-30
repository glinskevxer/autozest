-- A consignment is one unit offered by one seller for one run through the
-- lanes. The vehicle record outlives it; the consignment does not.
--
-- The same vin can be consigned many times over the years and must not be
-- consigned twice at once, so the uniqueness is partial: it applies to the
-- statuses that are still live and lets the closed ones pile up.

CREATE TABLE consignment (
    id               TEXT PRIMARY KEY,
    vin              TEXT NOT NULL REFERENCES vehicle (vin),
    seller_id        TEXT NOT NULL REFERENCES account (id),
    status           TEXT NOT NULL CHECK (status IN (
                         'registered', 'arrived', 'inspected', 'catalogued',
                         'sold', 'no_sale', 'withdrawn', 'released')),
    floor_cents      INTEGER CHECK (floor_cents IS NULL OR floor_cents >= 0),
    floor_set_at     TEXT,
    keys_count       INTEGER NOT NULL DEFAULT 0 CHECK (keys_count BETWEEN 0 AND 6),
    gate_pass        TEXT,
    transport_ref    TEXT,
    title_expected   INTEGER NOT NULL DEFAULT 1 CHECK (title_expected IN (0, 1)),
    arrived_at       TEXT,
    closed_at        TEXT,
    note             TEXT,
    created_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX consignment_live_vin_idx ON consignment (vin)
    WHERE status NOT IN ('sold', 'withdrawn', 'released');

CREATE INDEX consignment_seller_idx ON consignment (seller_id, status);

CREATE TABLE condition_report (
    id               TEXT PRIMARY KEY,
    consignment_id   TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    inspector        TEXT NOT NULL,
    inspected_at     TEXT NOT NULL,
    grade            REAL CHECK (grade IS NULL OR (grade >= 0.0 AND grade <= 5.0)),
    frame_checked    INTEGER NOT NULL DEFAULT 0 CHECK (frame_checked IN (0, 1)),
    drivable         INTEGER NOT NULL DEFAULT 1 CHECK (drivable IN (0, 1)),
    engine_starts    INTEGER NOT NULL DEFAULT 1 CHECK (engine_starts IN (0, 1)),
    tread_32nds      INTEGER CHECK (tread_32nds IS NULL OR tread_32nds BETWEEN 0 AND 12),
    finalised_at     TEXT,
    UNIQUE (consignment_id, inspected_at)
);

CREATE TABLE damage_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id        TEXT NOT NULL REFERENCES condition_report (id) ON DELETE CASCADE,
    panel            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    severity         INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    estimate_cents   INTEGER NOT NULL CHECK (estimate_cents >= 0),
    structural       INTEGER NOT NULL DEFAULT 0 CHECK (structural IN (0, 1)),
    note             TEXT
);

CREATE INDEX damage_line_report_idx ON damage_line (report_id);

-- What gets called at the block. One row per code per consignment, because
-- calling the same thing twice is noise and the block screen has no room.
CREATE TABLE announcement (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    consignment_id   TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    code             TEXT NOT NULL,
    detail           TEXT,
    source           TEXT NOT NULL CHECK (source IN (
                         'inspection', 'title', 'odometer', 'seller', 'manual')),
    made_at          TEXT NOT NULL,
    UNIQUE (consignment_id, code)
);

CREATE INDEX announcement_code_idx ON announcement (code);
