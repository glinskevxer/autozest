-- Indexes behind the foreign keys that did not have one.
--
-- Found by the migration checker rather than by anybody noticing. Without an
-- index on the child side, deleting a parent row scans the whole child table,
-- which is invisible at twenty rows and is why closing a sale took four
-- minutes the first time a season of bids had piled up.

CREATE INDEX bid_buyer_idx ON bid (buyer_id);
CREATE INDEX claim_appeal_raised_by_idx ON claim_appeal (raised_by);
CREATE INDEX lane_assignment_lane_idx ON lane_assignment (sale_code, lane);
CREATE INDEX ledger_entry_reverses_idx ON ledger_entry (reverses);
CREATE INDEX payment_entry_idx ON payment (entry_id);
CREATE INDEX proxy_bid_buyer_idx ON proxy_bid (buyer_id);
CREATE INDEX run_lane_idx ON run (sale_code, lane, position);
CREATE INDEX transport_order_carrier_idx ON transport_order (carrier_id);
