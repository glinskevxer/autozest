-- Who is on which lane.
--
-- An auctioneer works one lane at a time and a ring man works one auctioneer.
-- Double booking either of them is not a scheduling inconvenience, it is a
-- lane that does not run, so the overlap is checked rather than trusted.

CREATE TABLE staff (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN (
                   'auctioneer', 'ring', 'clerk', 'inspector', 'title', 'driver')),
    active     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (name, role)
);

CREATE TABLE lane_assignment (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_code  TEXT NOT NULL,
    lane       TEXT NOT NULL,
    staff_id   TEXT NOT NULL REFERENCES staff (id),
    role       TEXT NOT NULL,
    FOREIGN KEY (sale_code, lane) REFERENCES lane (sale_code, letter) ON DELETE CASCADE,
    UNIQUE (sale_code, lane, staff_id)
);

CREATE INDEX lane_assignment_staff_idx ON lane_assignment (staff_id, sale_code);
