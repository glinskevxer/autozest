-- Keys, and the authority to let a unit off the site.
--
-- Keys are held in a cabinet by tag number and handed over exactly once. The
-- gate release is a separate record from the handover because they happen at
-- different windows and, more usefully, because the release is the thing that
-- gets audited when a unit leaves that should not have.

CREATE TABLE key_set (
    consignment_id TEXT PRIMARY KEY REFERENCES consignment (id) ON DELETE CASCADE,
    tag            TEXT NOT NULL UNIQUE,
    count          INTEGER NOT NULL CHECK (count >= 0),
    location       TEXT NOT NULL,
    held_since     TEXT NOT NULL,
    handed_to      TEXT,
    handed_at      TEXT
);

CREATE TABLE gate_release (
    id             TEXT PRIMARY KEY,
    consignment_id TEXT NOT NULL REFERENCES consignment (id) ON DELETE CASCADE,
    released_to    TEXT NOT NULL,
    authorised_by  TEXT NOT NULL,
    holds_overridden TEXT,
    released_at    TEXT NOT NULL,
    UNIQUE (consignment_id)
);

CREATE INDEX gate_release_time_idx ON gate_release (released_at);
