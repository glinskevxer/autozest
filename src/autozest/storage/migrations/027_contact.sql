-- How each party wants to be told things.
--
-- One row per party per kind of message, because the answers are different.
-- A dealer wants an invoice by email and an arbitration decision by phone,
-- and sending both the same way is how a decision gets read four days late.

CREATE TABLE contact_preference (
    party_id   TEXT NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    channel    TEXT NOT NULL CHECK (channel IN ('email', 'post', 'sms', 'portal')),
    address    TEXT NOT NULL,
    verified_on TEXT,
    PRIMARY KEY (party_id, kind)
);

CREATE INDEX contact_preference_channel_idx ON contact_preference (channel);
