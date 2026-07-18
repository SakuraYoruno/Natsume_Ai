"""Stable error and migration constants for Host command persistence.

The repository, its strict codecs, and revision-supersession helpers share
these values.  Keeping them independent of the SQLite unit-of-work prevents
codec validation from importing the repository implementation back through a
circular dependency.
"""

from __future__ import annotations


HOST_COMMAND_SCHEMA_NOT_READY = "HOST_COMMAND_SCHEMA_NOT_READY"
HOST_COMMAND_INVALID = "HOST_COMMAND_INVALID"
HOST_COMMAND_CONFLICT = "HOST_COMMAND_CONFLICT"
HOST_COMMAND_INGRESS_CONFLICT = "HOST_COMMAND_INGRESS_CONFLICT"
HOST_COMMAND_DURABLE_DATA_INVALID = "HOST_COMMAND_DURABLE_DATA_INVALID"
HOST_COMMAND_SUPERSESSION_CONFLICT = "HOST_COMMAND_SUPERSESSION_CONFLICT"

HOST_COMMAND_MIGRATION_ASSET_KEY = "0062#host_command_admission"
HOST_COMMAND_MIGRATION_SHA256 = (
    "e003783038278ca82c7b8ee18fb87916ee7e6c109592a59a4f3579da378f5d59"
)
HOST_COMMAND_REVISION_MIGRATION_ASSET_KEY = "0088#host_command_graph_revision"
HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY = (
    "0100#task_runtime_graph_revision_supersession"
)
HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256 = (
    "9f7852754e7eab1502abf76deb570827aa4809c7e101940c5cf2a19beeaa499f"
)
GRAPH_REVISION_SUPERSESSION_SCHEMA_VERSION = (
    "Natsume.task_runtime.graph_revision_supersession.v1"
)
ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
HOST_COMMAND_COLUMNS = frozenset(
    {
        "command_id",
        "schema_version",
        "request_id",
        "surface",
        "principal_id",
        "session_id",
        "ingress_claim_ref",
        "ingress_audit_ref",
        "governor_grant_ref",
        "input_ref",
        "input_sha256",
        "route_type",
        "target_id",
        "business_scope",
        "operation_fingerprint",
        "root_work_order_id",
        "graph_id",
        "graph_revision",
        "assignment_id",
        "command_json",
        "command_digest",
        "work_order_json",
        "work_order_digest",
        "assignment_json",
        "assignment_digest",
        "graph_json",
        "graph_digest",
        "created_at_ms",
    }
)
REQUIRED_INDEXES = frozenset(
    {
        "idx_host_command_journal_principal_created",
        "idx_host_command_journal_graph",
    }
)
SUPERSESSION_TABLE = "task_runtime_graph_revision_supersession"


class HostCommandRepositoryError(RuntimeError):
    """Fail-closed command admission error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or HOST_COMMAND_INVALID)
        self.detail = str(detail or "host command persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


__all__ = [
    "ALLOWED_MIGRATION_DISPOSITIONS",
    "GRAPH_REVISION_SUPERSESSION_SCHEMA_VERSION",
    "HOST_COMMAND_COLUMNS",
    "HOST_COMMAND_CONFLICT",
    "HOST_COMMAND_DURABLE_DATA_INVALID",
    "HOST_COMMAND_INGRESS_CONFLICT",
    "HOST_COMMAND_INVALID",
    "HOST_COMMAND_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_MIGRATION_SHA256",
    "HOST_COMMAND_REVISION_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_SCHEMA_NOT_READY",
    "HOST_COMMAND_SUPERSESSION_CONFLICT",
    "HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256",
    "REQUIRED_INDEXES",
    "SUPERSESSION_TABLE",
    "HostCommandRepositoryError",
]
