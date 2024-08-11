-- Sales tax, and the certificates that get you out of it.
--
-- A certificate is a document with an expiry on it. Keeping the expiry is the
-- entire reason this is a table rather than a flag on the account: a dealer
-- whose resale certificate lapsed in March is not exempt in April, and the
-- first anybody hears about it is an assessment two years later.

CREATE TABLE tax_rate (
    state       TEXT PRIMARY KEY,
    rate_basis_points INTEGER NOT NULL CHECK (rate_basis_points BETWEEN 0 AND 2000),
    effective_from TEXT NOT NULL,
    note        TEXT
);

CREATE TABLE exemption_certificate (
    id          TEXT PRIMARY KEY,
    party_id    TEXT NOT NULL REFERENCES account (id),
    state       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('resale', 'export', 'government', 'charity')),
    number      TEXT NOT NULL,
    issued_on   TEXT NOT NULL,
    expires_on  TEXT,
    revoked_on  TEXT,
    UNIQUE (party_id, state, kind)
);

CREATE INDEX exemption_certificate_party_idx ON exemption_certificate (party_id, state);
