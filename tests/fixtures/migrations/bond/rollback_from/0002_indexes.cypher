CREATE INDEX bond_event_ts IF NOT EXISTS
FOR (e:Event)
ON (e.ts_ms);
