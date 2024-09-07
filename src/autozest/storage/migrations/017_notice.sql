-- Things the building has to tell somebody.
--
-- Deliberately a queue rather than a send. This system does not have an email
-- server and should not: it writes down what needs saying and something else
-- reads the queue. That way a failed send is somebody else's retry and a sale
-- night is never held up by a mail relay.

CREATE TABLE notice (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    party_id   TEXT NOT NULL REFERENCES account (id),
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    channel    TEXT NOT NULL CHECK (channel IN ('email', 'post', 'sms', 'portal')),
    status     TEXT NOT NULL CHECK (status IN ('queued', 'sent', 'failed', 'cancelled')),
    reference  TEXT,
    queued_at  TEXT NOT NULL,
    sent_at    TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT
);

CREATE INDEX notice_status_idx ON notice (status, queued_at);
CREATE INDEX notice_party_idx ON notice (party_id, kind);
