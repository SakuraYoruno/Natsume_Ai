CREATE CONSTRAINT bond_user_user_id IF NOT EXISTS
FOR (u:User)
REQUIRE u.user_id IS UNIQUE;

CREATE CONSTRAINT bond_session_session_id IF NOT EXISTS
FOR (s:Session)
REQUIRE s.session_id IS UNIQUE;

CREATE CONSTRAINT bond_event_event_id IF NOT EXISTS
FOR (e:Event)
REQUIRE e.event_id IS UNIQUE;

CREATE CONSTRAINT bond_topic_topic_id IF NOT EXISTS
FOR (t:Topic)
REQUIRE t.topic_id IS UNIQUE;

CREATE CONSTRAINT bond_capability_hint_id IF NOT EXISTS
FOR (c:CapabilityHint)
REQUIRE c.capability_hint IS UNIQUE;

CREATE INDEX bond_event_ts IF NOT EXISTS
FOR (e:Event)
ON (e.ts_ms);

CREATE INDEX bond_event_evidence_refs IF NOT EXISTS
FOR (e:Event)
ON (e.evidence_refs);
