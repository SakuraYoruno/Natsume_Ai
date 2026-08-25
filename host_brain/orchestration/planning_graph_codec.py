"""Pure validation and canonical codecs for PlanningGraph compilation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    ContractViolation,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SENSITIVE_WORD_KEYS = frozenset({"token_count"})
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "auth",
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "authentication",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "raw_auth",
        "rawauth",
        "oauth",
        "secret",
        "token",
    }
)
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
        "tokens",
    }
)


def _validate_virtual_state_provenance(*, schema: str, digest: str) -> None:
    if bool(schema) != bool(digest):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "virtual state schema and digest must be supplied together",
        )
    if digest and not SHA256_RE.fullmatch(digest):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "virtual state digest must be a lowercase sha256 digest",
        )


def _virtual_state_provenance_dict(*, schema: str, digest: str) -> dict[str, str] | None:
    return {"schema": schema, "digest": digest} if schema and digest else None


def _assert_secret_free(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                raise ContractViolation(
                    CONTRACT_FIELD_INVALID,
                    f"sensitive key is forbidden in planning payload: {path}.{key}",
                )
            _assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    if normalized in _SAFE_SENSITIVE_WORD_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    parts = tuple(part for part in normalized.split("_") if part)
    if any(part in _SENSITIVE_KEY_PARTS for part in parts):
        return True
    compact = normalized.replace("_", "")
    if any(
        marker in compact
        for marker in (
            "apikey",
            "authheader",
            "authentication",
            "credential",
            "password",
            "passwd",
            "privatekey",
            "rawauth",
            "secret",
            "token",
        )
    ):
        return True
    return any(
        normalized.startswith(prefix) or normalized.endswith(suffix)
        for prefix, suffix in (
            ("secret", "secret"),
            ("token", "token"),
            ("credential", "credential"),
            ("rawauth", "rawauth"),
        )
    )


def _normalize_evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            "failure_evidence_refs must be a sequence of refs",
        )
    result: list[str] = []
    for index, value in enumerate(values):
        ref = _required_text(
            value,
            field_name=f"failure_evidence_refs[{index}]",
            max_length=1_000,
        )
        if "://" not in ref:
            raise ContractViolation(
                CONTRACT_FIELD_INVALID,
                f"failure_evidence_refs[{index}] must be a governed ref",
            )
        if ref not in result:
            result.append(ref)
    return tuple(result)


def _initial_lineage(
    *,
    request_id: str,
    selected_plan_digest: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(
        (
            "Natsume.planning_search.lineage.v1\n"
            f"{request_id}\n{selected_plan_digest}"
        ).encode("utf-8")
    ).hexdigest()
    return f"graph-planning-{digest[:40]}", f"wo-planning-{digest[24:64]}"


def _node_id(*, graph_id: str, position: int, candidate_id: str) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "graph_id": graph_id,
                "position": position,
                "candidate_id": candidate_id,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"node-planning-{position + 1:04d}-{digest[:24]}"


def _planner_ref(
    *,
    request_id: str,
    graph_id: str,
    revision: int,
    trace_digest: str,
    virtual_state_digest: str = "",
) -> str:
    request_digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
    state_segment = (
        f"/state/sha256:{virtual_state_digest}"
        if virtual_state_digest
        else ""
    )
    return (
        f"planner://planning-search/{request_digest}/graphs/{quote(graph_id, safe='')}"
        f"/revisions/{revision}{state_segment}/trace/sha256:{trace_digest}"
    )


def _sha256_ref(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _compilation_digest(
    *,
    work_order: WorkOrderV3,
    task_graph: TaskGraph,
    trace_digest: str,
    selected_candidate_ids: Sequence[str],
    virtual_state_schema: str,
    virtual_state_digest: str,
    previous_revision: int | None,
    failure_evidence_refs: Sequence[str],
) -> str:
    return _sha256_ref(
        {
            "schema": "Natsume.planning_search.compiled_graph.v1",
            "work_order": work_order.to_dict(),
            "task_graph": task_graph.to_dict(),
            "trace_digest": trace_digest,
            "selected_candidate_ids": list(selected_candidate_ids),
            "virtual_state": _virtual_state_provenance_dict(
                schema=virtual_state_schema,
                digest=virtual_state_digest,
            ),
            "previous_revision": previous_revision,
            "failure_evidence_refs": list(failure_evidence_refs),
        }
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "planning payload must be JSON-safe") from exc


def _json_copy(value: Any) -> Any:
    """Copy a JSON-safe planning value without sharing mutable containers."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, "planning payload must be JSON-safe") from exc
    return json.loads(encoded)


def _required_text(value: Any, *, field_name: str, max_length: int = 1_000) -> str:
    if not isinstance(value, str):
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be text",
        )
    text = value.strip()
    if not text or len(text) > max_length:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be non-empty and at most {max_length} characters",
        )
    return text


def _required_text_list(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an explicit list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _required_text(item, field_name=f"{field_name}[{index}]", max_length=4_000)
        if text not in result:
            result.append(text)
    if not result and not allow_empty:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must not be empty")
    return tuple(result)


def _dedupe_text(*groups: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
    return tuple(result)


def _exact_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractViolation(
            CONTRACT_FIELD_INVALID,
            f"{field_name} must be an integer >= 0",
        )
    return value

# Runtime and persistence authority intentionally remain in the compiler owner.
