-- What a seller wants looked at.
--
-- A fleet's contract says every unit gets a tyre depth and an underbody check.
-- A finance company's repossessions want the interior graded because that is
-- where the money is. An inspector holding a phone in the rain cannot be
-- expected to remember which is which.

CREATE TABLE inspection_template (
    id          TEXT PRIMARY KEY,
    seller_id   TEXT REFERENCES account (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT NOT NULL,
    UNIQUE (seller_id, name)
);

CREATE TABLE template_check (
    template_id TEXT NOT NULL REFERENCES inspection_template (id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL CHECK (ordinal > 0),
    prompt      TEXT NOT NULL,
    panel       TEXT,
    required    INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    PRIMARY KEY (template_id, ordinal)
);
