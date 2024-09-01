-- Accounting periods, and locking them.
--
-- A closed period does not take new entries. That sounds obvious and is the
-- single most argued about rule in this system, because somebody always wants
-- to put one more thing into last month. The answer is a correcting entry in
-- this month, which is what an auditor expects to find anyway.

CREATE TABLE accounting_period (
    id         TEXT PRIMARY KEY,
    starts_on  TEXT NOT NULL,
    ends_on    TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('open', 'closing', 'closed')),
    closed_by  TEXT,
    closed_at  TEXT,
    note       TEXT,
    UNIQUE (starts_on, ends_on)
);

CREATE INDEX accounting_period_status_idx ON accounting_period (status, starts_on);
