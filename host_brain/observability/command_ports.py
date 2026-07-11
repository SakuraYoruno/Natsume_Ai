"""Typed ports and the server-owned OBS command manifest.

The manifest describes every writer removed from the query-only OBS routers.
It deliberately contains no legacy function imports and binds no default
handler.  Composition must explicitly provide a domain port for a command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable, Mapping, Protocol


OBS_COMMAND_ENVELOPE_SCHEMA = "natsume.obs-command.v1"
AUTHORITY_PAYLOAD_FIELDS = frozenset({
    "actor",
    "actor_id",
    "approver",
    "approver_id",
    "approver_node_id",
    "operator",
    "operator_id",
    "operator_node_id",
    "owner",
    "owner_id",
    "owner_organ",
    "principal_id",
    "proposer",
    "proposer_id",
    "proposer_node_id",
    "tenant_id",
})


class ObsCommandContractError(ValueError):
    """Stable validation failure raised before a domain port is invoked."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class ObsCommandPayloadSchema:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    string_fields: tuple[str, ...] = ()
    mapping_fields: tuple[str, ...] = ()
    boolean_fields: tuple[str, ...] = ()
    integer_fields: tuple[str, ...] = ()
    number_fields: tuple[str, ...] = ()

    def validate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or len(payload) > 64:
            raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_INVALID")
        normalized = {str(key): value for key, value in payload.items()}
        known = set(self.required) | set(self.optional) | set(AUTHORITY_PAYLOAD_FIELDS)
        if set(normalized) - known:
            raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_FIELD_DENIED")
        if any(key not in normalized for key in self.required):
            raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_REQUIRED")
        for key in self.string_fields:
            if key in normalized and normalized[key] is not None:
                value = normalized[key]
                if not isinstance(value, str) or len(value) > 4_096:
                    raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_TYPE_INVALID")
        for key in self.mapping_fields:
            if key in normalized and not isinstance(normalized[key], Mapping):
                raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_TYPE_INVALID")
        for key in self.boolean_fields:
            if key in normalized and type(normalized[key]) is not bool:
                raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_TYPE_INVALID")
        for key in self.integer_fields:
            if key in normalized and type(normalized[key]) is not int:
                raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_TYPE_INVALID")
        for key in self.number_fields:
            if key in normalized and (
                isinstance(normalized[key], bool)
                or not isinstance(normalized[key], (int, float))
            ):
                raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_TYPE_INVALID")
        return normalized


@dataclass(frozen=True, slots=True)
class ObsCommandSpec:
    command_id: str
    action: str
    required_scopes: tuple[str, ...]
    payload_schema: ObsCommandPayloadSchema
    authority_field: str = "operator"

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "authority_field": self.authority_field,
            "command_id": self.command_id,
            "required_scopes": list(self.required_scopes),
            "schema": {
                "boolean_fields": list(self.payload_schema.boolean_fields),
                "integer_fields": list(self.payload_schema.integer_fields),
                "mapping_fields": list(self.payload_schema.mapping_fields),
                "number_fields": list(self.payload_schema.number_fields),
                "optional": list(self.payload_schema.optional),
                "required": list(self.payload_schema.required),
                "string_fields": list(self.payload_schema.string_fields),
            },
        }


@dataclass(frozen=True, slots=True)
class ObsCommandAuthority:
    principal_id: str
    tenant_id: str
    request_id: str
    proposer: str
    approver: str
    operator: str
    owner: str

    @classmethod
    def bind(cls, *, principal_id: str, tenant_id: str, request_id: str) -> "ObsCommandAuthority":
        normalized = str(principal_id).strip()
        return cls(
            principal_id=normalized,
            tenant_id=str(tenant_id).strip(),
            request_id=str(request_id).strip(),
            proposer=normalized,
            approver=normalized,
            operator=normalized,
            owner=normalized,
        )


@dataclass(frozen=True, slots=True)
class ObsCommandEnvelope:
    schema_version: str
    command_id: str
    action: str
    required_scopes: tuple[str, ...]
    payload: Mapping[str, Any]
    authority: ObsCommandAuthority
    claim_ref: str
    audit_ref: str
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObsCommandExecutionResult:
    success: bool
    result_ref: str
    result: Mapping[str, Any] = field(default_factory=dict)
    side_effects_executed: bool = True


class ObsCommandPort(Protocol):
    def execute(
        self,
        envelope: ObsCommandEnvelope,
    ) -> ObsCommandExecutionResult | Awaitable[ObsCommandExecutionResult]: ...


class SecureIngressClaimResultStore(Protocol):
    def complete_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        at_ms: int | None = None,
    ) -> None: ...


def bind_server_authority(
    payload: Mapping[str, Any],
    *,
    spec: ObsCommandSpec,
    authority: ObsCommandAuthority,
) -> Mapping[str, Any]:
    """Erase caller authority and inject the authenticated principal."""

    def _erase(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): _erase(child)
                for key, child in value.items()
                if str(key) not in AUTHORITY_PAYLOAD_FIELDS
            }
        if isinstance(value, list):
            return [_erase(child) for child in value]
        return value

    bound = _erase(payload)
    if not isinstance(bound, dict):  # payload is validated as a mapping first
        raise ObsCommandContractError("OBS_COMMAND_PAYLOAD_INVALID")
    bound.update({
        "principal_id": authority.principal_id,
        "tenant_id": authority.tenant_id,
        "proposer": authority.proposer,
        "approver": authority.approver,
        "operator": authority.operator,
        "owner": authority.owner,
        "owner_organ": authority.owner,
        spec.authority_field: authority.principal_id,
    })
    return MappingProxyType(bound)


class ObsCommandPortRegistry:
    """Immutable command policy plus explicitly injected domain adapters."""

    def __init__(
        self,
        *,
        specs: tuple[ObsCommandSpec, ...] | None = None,
        ports: Mapping[str, ObsCommandPort] | None = None,
    ) -> None:
        selected = DEFAULT_OBS_COMMAND_SPECS if specs is None else specs
        spec_map = {spec.command_id: spec for spec in selected}
        if len(spec_map) != len(selected):
            raise ValueError("OBS_COMMAND_MANIFEST_DUPLICATE")
        unknown_ports = set(ports or {}) - set(spec_map)
        if unknown_ports:
            raise ValueError("OBS_COMMAND_PORT_NOT_IN_MANIFEST")
        self._specs = MappingProxyType(spec_map)
        self._ports = MappingProxyType(dict(ports or {}))

    def resolve_spec(self, command_id: str) -> ObsCommandSpec | None:
        return self._specs.get(str(command_id).strip())

    def resolve_port(self, command_id: str) -> ObsCommandPort | None:
        return self._ports.get(str(command_id).strip())

    def manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._specs[key].manifest_entry()
            for key in sorted(self._specs)
        )

    @property
    def bound_command_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ports))


def _schema(
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    strings: tuple[str, ...] = (),
    mappings: tuple[str, ...] = (),
    booleans: tuple[str, ...] = (),
    integers: tuple[str, ...] = (),
    numbers: tuple[str, ...] = (),
) -> ObsCommandPayloadSchema:
    return ObsCommandPayloadSchema(
        required=required,
        optional=optional,
        string_fields=strings,
        mapping_fields=mappings,
        boolean_fields=booleans,
        integer_fields=integers,
        number_fields=numbers,
    )


def _spec(
    command_id: str,
    domain_scope: str,
    schema: ObsCommandPayloadSchema,
    *,
    authority_field: str = "operator",
) -> ObsCommandSpec:
    return ObsCommandSpec(
        command_id=command_id,
        action=f"obs:command:{command_id}",
        required_scopes=("obs:write", domain_scope),
        payload_schema=schema,
        authority_field=authority_field,
    )


_RUN = _schema(optional=("run_id", "inputs"), strings=("run_id",), mappings=("inputs",))
_OWNER_FILTER = _schema(optional=("run_id", "owner_organ"), strings=("run_id", "owner_organ"))
_OWNER_WRITE = _schema(
    optional=("run_id", "owner_organ", "dry_run"),
    strings=("run_id", "owner_organ"),
    booleans=("dry_run",),
)
_OWNER_DECISION = _schema(
    required=("candidate_id",),
    optional=("run_id", "reason", "dry_run"),
    strings=("candidate_id", "run_id", "reason"),
    booleans=("dry_run",),
)


DEFAULT_OBS_COMMAND_SPECS: tuple[ObsCommandSpec, ...] = (
    _spec("bio.daily_cycle.run", "obs:bio-ops:write", _RUN),
    _spec("bio.adoption.accept", "obs:bio-ops:write", _OWNER_DECISION),
    _spec("bio.adoption.reject", "obs:bio-ops:write", _OWNER_DECISION),
    _spec("bio.owner_inbox.materialize", "obs:bio-ops:write", _OWNER_FILTER),
    _spec("bio.owner_inbox.intake_packets", "obs:bio-ops:write", _OWNER_FILTER),
    _spec("bio.owner_queue.consume", "obs:bio-ops:write", _OWNER_WRITE),
    _spec("bio.owner_state.import", "obs:bio-ops:write", _OWNER_WRITE),
    _spec("bio.owner_state.accept", "obs:bio-ops:write", _OWNER_DECISION),
    _spec("bio.owner_state.reject", "obs:bio-ops:write", _OWNER_DECISION),
    _spec(
        "capability_forge.proposal.create",
        "obs:capability-forge:write",
        _schema(
            required=("session_id",),
            optional=("candidate_key", "canary_traffic_percent"),
            strings=("session_id", "candidate_key"),
            integers=("canary_traffic_percent",),
        ),
        authority_field="proposer",
    ),
    _spec(
        "capability_forge.proposal.approve",
        "obs:capability-forge:write",
        _schema(
            required=("proposal_id", "approval_token_ref"),
            optional=("target_stage",),
            strings=("proposal_id", "approval_token_ref", "target_stage"),
        ),
        authority_field="approver",
    ),
    _spec(
        "capability_forge.proposal.rollback",
        "obs:capability-forge:write",
        _schema(
            required=("proposal_id",),
            optional=("reason",),
            strings=("proposal_id", "reason"),
        ),
    ),
    _spec(
        "avatar.behavior.apply",
        "obs:avatar:write",
        _schema(
            required=("behavior_id",),
            optional=(
                "emotion_tag", "body_action_id", "emotion_intensity",
                "duration_s", "talk_state", "source",
            ),
            strings=("behavior_id", "emotion_tag", "body_action_id", "talk_state", "source"),
            numbers=("emotion_intensity", "duration_s"),
        ),
    ),
    _spec(
        "sys_mode.switch",
        "obs:sys-mode:write",
        _schema(
            required=("target_mode",),
            optional=("reason", "source"),
            strings=("target_mode", "reason", "source"),
        ),
    ),
    _spec(
        "sys_mode.auto_rules.update",
        "obs:sys-mode:write",
        _schema(required=("rules",), mappings=("rules",)),
    ),
    _spec(
        "sys_mode.event.submit",
        "obs:sys-mode:write",
        _schema(required=("event_type",), strings=("event_type",)),
    ),
)


if len(DEFAULT_OBS_COMMAND_SPECS) != 16:  # defensive import-time invariant
    raise RuntimeError("OBS_COMMAND_MANIFEST_MUST_HAVE_16_ACTIVE_FAMILIES")


__all__ = [
    "AUTHORITY_PAYLOAD_FIELDS",
    "DEFAULT_OBS_COMMAND_SPECS",
    "OBS_COMMAND_ENVELOPE_SCHEMA",
    "ObsCommandAuthority",
    "ObsCommandContractError",
    "ObsCommandEnvelope",
    "ObsCommandExecutionResult",
    "ObsCommandPayloadSchema",
    "ObsCommandPort",
    "ObsCommandPortRegistry",
    "ObsCommandSpec",
    "SecureIngressClaimResultStore",
    "bind_server_authority",
]
