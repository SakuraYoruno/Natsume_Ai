CREATE INDEX bond_event_evidence_refs IF NOT EXISTS
FOR (e:Event)
ON (e.evidence_refs);
