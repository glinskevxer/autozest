-- Guide values, and what units like this actually brought.
--
-- The guide is bought in from a third party and is a starting point. What the
-- lanes here paid last month is better evidence and it is free, so both are
-- kept and the advice uses whichever there is more of.

CREATE TABLE guide_value (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    make         TEXT NOT NULL,
    model        TEXT NOT NULL,
    model_year   INTEGER NOT NULL,
    grade_band   TEXT NOT NULL,
    value_cents  INTEGER NOT NULL CHECK (value_cents >= 0),
    source       TEXT NOT NULL,
    as_at        TEXT NOT NULL,
    UNIQUE (make, model, model_year, grade_band, source, as_at)
);

CREATE INDEX guide_value_lookup_idx ON guide_value (make, model, model_year, as_at);
