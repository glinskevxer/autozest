-- Indexes the queries actually turned out to want.
--
-- Added after reading the plans rather than before. Every one of these was
-- doing a scan on a table that grows with the sales, and every one of them was
-- fine at twenty units and not at three hundred.
--
-- The two composite ones matter most. Aged receivables joins the journal on
-- account and party and sorts by entry, and the seller statement does the
-- same thing the other way round. Both were reading every line in the ledger.

CREATE INDEX ledger_line_party_account_idx ON ledger_line (party_id, account, entry_id);
CREATE INDEX ledger_entry_posted_idx ON ledger_entry (posted_at, id);
CREATE INDEX run_sale_status_idx ON run (sale_code, status);
CREATE INDEX consignment_status_idx ON consignment (status, id);
CREATE INDEX arbitration_claim_opened_idx ON arbitration_claim (status, opened_at);
CREATE INDEX title_record_received_idx ON title_record (received_on);
CREATE INDEX payment_reference_idx ON payment (reference);
