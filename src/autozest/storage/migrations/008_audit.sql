-- An audit trail for anything that changes money or status.
--
-- Deliberately not a copy of every row. What gets kept is who did what to
-- which record and when, plus a short description of the change. A full before
-- and after of every row was the first design and it produced a table larger
-- than the rest of the database inside a month, which nobody read.

CREATE TABLE audit_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    subject   TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    detail    TEXT,
    made_at   TEXT NOT NULL
);

CREATE INDEX audit_event_subject_idx ON audit_event (subject, subject_id, id);
CREATE INDEX audit_event_actor_idx ON audit_event (actor, made_at);
