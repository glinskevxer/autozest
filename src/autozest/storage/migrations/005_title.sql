-- Titles. The paperwork side of a sale, which outlasts the sale itself.
--
-- A title record hangs off the consignment rather than the run, because a unit
-- that does not sell still has a title sitting in the drawer and it still has
-- to go back to the seller.

CREATE TABLE title_record (
    consignment_id  TEXT PRIMARY KEY REFERENCES consignment (id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN (
                        'expected', 'on_hand', 'attorney', 'absent',
                        'lost_applied', 'released', 'sent')),
    brand           TEXT NOT NULL DEFAULT 'clean',
    issuing_state   TEXT,
    title_number    TEXT,
    received_on     TEXT,
    released_on     TEXT,
    note            TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX title_record_status_idx ON title_record (status, brand);

-- Every movement of a title, because "we sent it" is the single most argued
-- sentence in the building.
CREATE TABLE title_movement (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    consignment_id  TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    actor           TEXT NOT NULL,
    detail          TEXT,
    made_at         TEXT NOT NULL
);

CREATE INDEX title_movement_consignment_idx ON title_movement (consignment_id, id);
