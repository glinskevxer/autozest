-- Sale templates.
--
-- The same three sales run every week with the same lanes, the same crew and
-- the same start time. Building each one by hand is four minutes and one
-- forgotten lane a quarter, and the forgotten lane is only noticed when
-- forty units have nowhere to go.

CREATE TABLE sale_template (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    title       TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT NOT NULL
);

CREATE TABLE template_lane (
    template_id TEXT NOT NULL REFERENCES sale_template (id) ON DELETE CASCADE,
    letter      TEXT NOT NULL CHECK (length(letter) = 1),
    capacity    INTEGER NOT NULL CHECK (capacity > 0),
    PRIMARY KEY (template_id, letter)
);
