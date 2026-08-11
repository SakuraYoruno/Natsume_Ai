-- Durable authority is recorded separately so existing v1 command rows retain
-- their historical bytes.  In particular, never infer an owner from a mutable
-- current lease: a legacy command with no explicit authority record is not
-- readable through the receipt-query endpoint.
CREATE TABLE IF NOT EXISTS edge_embodiment_command_authority (
    command_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    admitted_at_ms INTEGER NOT NULL,
    FOREIGN KEY (command_id) REFERENCES edge_embodiment_command(command_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_edge_embodiment_command_authority_owner
    ON edge_embodiment_command_authority(owner_id, command_id);

UPDATE edge_transport_schema
SET schema_version = 2
WHERE component = 'embodiment';
