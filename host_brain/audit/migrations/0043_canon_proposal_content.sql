-- Migration 0043: persist reviewable World Canon proposal content.
-- Existing runtimes also apply these columns defensively from SQLiteLedger._ensure_schema.
ALTER TABLE canon_proposal_log ADD COLUMN proposed_title TEXT;
ALTER TABLE canon_proposal_log ADD COLUMN proposed_tags TEXT;
ALTER TABLE canon_proposal_log ADD COLUMN proposed_body TEXT;
