-- Where a unit physically is.
--
-- Six hundred spaces across nine rows and three buildings. A unit nobody can
-- find is a unit that does not run, and a lane that stops for four minutes
-- while somebody walks the yard costs more than the unit is worth.

CREATE TABLE yard_space (
    id        TEXT PRIMARY KEY,
    zone      TEXT NOT NULL,
    row_label TEXT NOT NULL,
    number    INTEGER NOT NULL CHECK (number > 0),
    covered   INTEGER NOT NULL DEFAULT 0 CHECK (covered IN (0, 1)),
    status    TEXT NOT NULL CHECK (status IN ('free', 'occupied', 'blocked')),
    UNIQUE (zone, row_label, number)
);

CREATE INDEX yard_space_status_idx ON yard_space (status, zone);

CREATE TABLE yard_movement (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    consignment_id TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    space_id       TEXT REFERENCES yard_space (id),
    moved_by       TEXT NOT NULL,
    reason         TEXT,
    moved_at       TEXT NOT NULL
);

CREATE INDEX yard_movement_consignment_idx ON yard_movement (consignment_id, id);
CREATE INDEX yard_movement_space_idx ON yard_movement (space_id, id);
