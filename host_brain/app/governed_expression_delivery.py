"""Task-bound sentence and expression-delivery validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from host_brain.app.governed_route_support import (
    GOVERNED_ROUTE_INPUT_INVALID,
    GOVERNED_ROUTE_OUTPUT_INVALID,
    GOVERNED_ROUTE_ROUTE_DRIFT,
    GovernedRouteExecutorError,
    _json_copy,
    _safe_mapping,
    _strict_non_negative_int,
    _strict_positive_int,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput


def _mapping_child(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    child = value.get(key)
    return child if isinstance(child, Mapping) else {}


def _acceptance_effect_intents(event: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = event.get("acceptance_effect_intents")
    if raw in (None, (), []):
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat acceptance effects must be a sequence",
        )
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "chat acceptance effect must be a mapping",
            )
        effect_type = str(item.get("effect_type") or "").strip()
        scope = str(item.get("commit_scope") or "").strip().upper()
        payload = item.get("payload")
        if (
            effect_type != "life.foreground_completed"
            or scope != "NODE"
            or not isinstance(payload, Mapping)
        ):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "frozen chat may defer only NODE-scoped life.foreground_completed",
            )
        normalized.append(
            {
                "effect_type": effect_type,
                "commit_scope": scope,
                "payload": _json_copy(payload),
            }
        )
    return tuple(normalized)


def _assert_task_bound_expression_delivery(
    effects: Sequence[Mapping[str, Any]],
    *,
    visible_sentences: Sequence[Mapping[str, Any]],
    frozen: FrozenNodeInput,
) -> None:
    """Bind accepted Voice/Avatar protocol to the exact visible sentences.

    A governed native stream is allowed to expose only Task-owned staged text
    before Acceptance.  The deferred foreground effect must therefore carry
    precisely one paired Voice/Avatar intent for every already-visible
    sentence, plus one paired release control for the completed prefix.  A
    worker cannot quietly merge those sentences into a different utterance
    boundary or smuggle a mismatched plan through the generic Life effect.
    """

    if not visible_sentences:
        return
    if len(effects) != 1:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound chat requires exactly one accepted foreground effect",
        )
    outer = _safe_mapping(effects[0].get("payload"))
    payload = _safe_mapping(outer.get("payload"))
    intents = _expression_delivery_items(
        payload.get("expression_delivery_intents"),
        field_name="expression_delivery_intents",
    )
    controls = _expression_delivery_items(
        payload.get("expression_delivery_controls"),
        field_name="expression_delivery_controls",
    )
    receipts = _expression_delivery_items(
        payload.get("expression_delivery_receipts"),
        field_name="expression_delivery_receipts",
    )
    expected_count = len(visible_sentences)
    if len(intents) != expected_count * 2 or len(controls) != 2:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound chat expression protocol count does not match visible sentences",
        )
    if len(receipts) != len(intents) + len(controls):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound chat expression receipt count drifted",
        )
    delivery_ids = [
        str(item.get("delivery_id") or "").strip()
        for item in (*intents, *controls)
    ]
    if not all(delivery_ids) or len(set(delivery_ids)) != len(delivery_ids):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression delivery identities are not unique",
        )

    expected_scope = {
        "plan_id": str(visible_sentences[0].get("affective_turn_plan_id") or ""),
        "synchronization_id": str(visible_sentences[0].get("synchronization_id") or ""),
        "self_affect_revision": _strict_non_negative_int(
            visible_sentences[0].get("self_affect_revision"),
            field_name="self_affect_revision",
        ),
        "workspace_id": str(visible_sentences[0].get("workspace_id") or ""),
        "relationship_subject_id": _frozen_relationship_subject_id(frozen),
        "session_id": str(frozen.session_id or ""),
    }
    if not all(
        value != ""
        for key, value in expected_scope.items()
        if key != "self_affect_revision"
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound chat expression scope is incomplete",
        )
    for sentence in visible_sentences:
        if (
            str(sentence.get("affective_turn_plan_id") or "")
            != expected_scope["plan_id"]
            or str(sentence.get("synchronization_id") or "")
            != expected_scope["synchronization_id"]
            or _strict_non_negative_int(
                sentence.get("self_affect_revision"),
                field_name="self_affect_revision",
            )
            != expected_scope["self_affect_revision"]
            or str(sentence.get("workspace_id") or "")
            != expected_scope["workspace_id"]
        ):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "task-bound visible sentences do not share one expression scope",
            )

    receipt_by_delivery_id: dict[str, Mapping[str, Any]] = {}
    for receipt in receipts:
        delivery_id = str(receipt.get("delivery_id") or "")
        if not delivery_id or delivery_id in receipt_by_delivery_id:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "task-bound expression receipt identity is invalid",
        )
        receipt_by_delivery_id[delivery_id] = receipt
    if len(receipt_by_delivery_id) != len(receipts):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression receipts are not uniquely addressable",
        )

    expected_intent_keys: set[tuple[int, str, str]] = set()
    for index, staged in enumerate(visible_sentences):
        text = str(staged.get("sentence") or "").strip()
        if not text:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "task-bound visible sentence is empty",
            )
        for channel, action in (("VOICE", "SPEAK"), ("AVATAR", "TRANSITION")):
            expected_intent_keys.add((index, channel, action))
            matching = [
                item
                for item in intents
                if _expression_delivery_key(item) == (index, channel, action)
            ]
            if len(matching) != 1:
                raise GovernedRouteExecutorError(
                    GOVERNED_ROUTE_OUTPUT_INVALID,
                    "task-bound expression intent does not map one-to-one to a visible sentence",
                )
            intent = matching[0]
            _assert_expression_delivery_scope(intent, expected_scope=expected_scope)
            if channel == "VOICE":
                voice_payload = _safe_mapping(intent.get("payload"))
                if str(voice_payload.get("text") or "").strip() != text:
                    raise GovernedRouteExecutorError(
                        GOVERNED_ROUTE_OUTPUT_INVALID,
                        "task-bound voice text differs from its visible sentence",
                    )
            _assert_expression_delivery_receipt(
                intent,
                receipt_by_delivery_id=receipt_by_delivery_id,
                expected_status="HOST_PROTOCOL_STAGED",
            )
    if {_expression_delivery_key(item) for item in intents} != expected_intent_keys:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression intents contain an unexpected sentence or channel",
        )

    expected_control_keys = {
        (expected_count, "VOICE", "RELEASE"),
        (expected_count, "AVATAR", "RELEASE"),
    }
    if {_expression_delivery_key(item) for item in controls} != expected_control_keys:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression release controls do not cover the completed prefix",
        )
    for control in controls:
        _assert_expression_delivery_scope(control, expected_scope=expected_scope)
        _assert_expression_delivery_receipt(
            control,
            receipt_by_delivery_id=receipt_by_delivery_id,
            expected_status="HOST_RELEASE_STAGED",
        )


def _expression_delivery_items(value: Any, *, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must be a sequence",
        )
    items = tuple(item for item in value if isinstance(item, Mapping))
    if len(items) != len(value):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must contain mappings only",
        )
    return items


def _expression_delivery_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        _strict_non_negative_int(
            item.get("sentence_index"), field_name="expression sentence_index"
        ),
        str(item.get("channel") or "").strip().upper(),
        str(item.get("action") or "").strip().upper(),
    )


def _assert_expression_delivery_scope(
    item: Mapping[str, Any],
    *,
    expected_scope: Mapping[str, Any],
) -> None:
    for field in (
        "plan_id",
        "synchronization_id",
        "workspace_id",
        "relationship_subject_id",
        "session_id",
    ):
        if str(item.get(field) or "").strip() != str(expected_scope[field]):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                f"task-bound expression {field} drifted from visible sentence authority",
            )
    if str(item.get("self_id") or "").strip() != "natsume_ai":
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression self_id is invalid",
        )
    if _strict_non_negative_int(
        item.get("self_affect_revision"),
        field_name="expression self_affect_revision",
    ) != expected_scope["self_affect_revision"]:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression self-affect revision drifted",
        )


def _assert_expression_delivery_receipt(
    item: Mapping[str, Any],
    *,
    receipt_by_delivery_id: Mapping[str, Mapping[str, Any]],
    expected_status: str,
) -> None:
    delivery_id = str(item.get("delivery_id") or "").strip()
    receipt = receipt_by_delivery_id.get(delivery_id)
    if receipt is None:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression intent lacks its Host staging receipt",
        )
    if (
        str(receipt.get("synchronization_id") or "").strip()
        != str(item.get("synchronization_id") or "").strip()
        or str(receipt.get("channel") or "").strip().upper()
        != str(item.get("channel") or "").strip().upper()
        or str(receipt.get("action") or "").strip().upper()
        != str(item.get("action") or "").strip().upper()
        or str(receipt.get("status") or "").strip().upper() != expected_status
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound expression receipt drifted from its intent",
        )


def _normalize_terminal_decision(event: Mapping[str, Any]) -> str:
    decision = str(event.get("decision") or "").strip().upper()
    if decision in {"APPROVE", "SUCCESS"}:
        return "APPROVE"
    if decision in {"REJECT", "FAILED", "FAILURE", "NO_REPLY"}:
        return "REJECT"
    if event.get("success") is True:
        return "APPROVE"
    if event.get("success") is False:
        return "REJECT"
    raise GovernedRouteExecutorError(
        GOVERNED_ROUTE_OUTPUT_INVALID,
        "chat terminal requires APPROVE or REJECT decision",
    )


def _terminal_reason_code(event: Mapping[str, Any], *, decision: str) -> str | None:
    value = str(event.get("reason_code") or "").strip()
    if value:
        return value
    return None if decision == "APPROVE" else "CHAT_EXECUTION_FAILED"


def _is_terminal(event: Mapping[str, Any]) -> bool:
    return event.get("done") is True or str(event.get("phase") or "").strip().upper() == "COMMIT"


def _task_bound_staged_sentence_event(
    event: Mapping[str, Any],
    *,
    frozen: FrozenNodeInput,
) -> dict[str, Any] | None:
    """Validate the one nonterminal Chat event allowed past the worker.

    Plain ``SENTENCE`` frames remain speculative Surface data and are buffered
    like every other worker event.  A frame claiming task delivery authority
    must prove every immutable identity field; it is never downgraded to an
    ordinary buffered frame on mismatch.
    """

    phase = str(event.get("phase") or "").strip().upper()
    authority_markers = {
        "delivery_id",
        "delivery_status",
        "graph_id",
        "attempt_id",
        "lease_id",
        "node_grant_ref",
        "relationship_subject_id",
    }
    claims_task_delivery = bool(authority_markers.intersection(event))
    if phase != "SENTENCE" and not claims_task_delivery:
        return None
    if phase != "SENTENCE":
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound delivery authority is valid only for a sentence frame",
        )
    if not claims_task_delivery:
        return None
    delivery_id = str(event.get("delivery_id") or "").strip()
    if not re.fullmatch(r"taskchat://[0-9a-f]{64}", delivery_id):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence has no canonical delivery id",
        )
    if str(event.get("delivery_status") or "").strip() != "TASK_NODE_SENTENCE_STAGED":
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence was not durably staged",
        )
    sentence = str(event.get("sentence") or "").strip()
    if not sentence or len(sentence) > 2_000:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence content is invalid",
        )
    sentence_index = _strict_non_negative_int(
        event.get("sentence_index"), field_name="sentence_index"
    )
    graph_revision = _strict_non_negative_int(
        event.get("graph_revision"), field_name="graph_revision"
    )
    self_affect_revision = _strict_non_negative_int(
        event.get("self_affect_revision"), field_name="self_affect_revision"
    )
    relationship_subject_id = _frozen_relationship_subject_id(frozen)
    expected = {
        "request_id": frozen.request_id,
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "node_id": frozen.node_id,
        "attempt_id": frozen.attempt_id,
        "lease_id": frozen.lease_id,
        "lease_owner_id": frozen.lease_owner_id,
        "fencing_token": frozen.fencing_token,
        "node_grant_ref": frozen.node_grant_ref,
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
    }
    actual = {
        "request_id": str(event.get("request_id") or "").strip(),
        "graph_id": str(event.get("graph_id") or "").strip(),
        "graph_revision": graph_revision,
        "node_id": str(event.get("node_id") or "").strip(),
        "attempt_id": str(event.get("attempt_id") or "").strip(),
        "lease_id": str(event.get("lease_id") or "").strip(),
        "lease_owner_id": str(event.get("lease_owner_id") or "").strip(),
        "fencing_token": _strict_positive_int(
            event.get("fencing_token"), field_name="fencing_token"
        ),
        "node_grant_ref": str(event.get("node_grant_ref") or "").strip(),
        "self_id": str(event.get("self_id") or "").strip(),
        "relationship_subject_id": str(event.get("relationship_subject_id") or "").strip(),
    }
    if actual != expected:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            "task-bound sentence identity differs from frozen node authority",
        )
    workspace_id = str(event.get("workspace_id") or "").strip()
    plan_id = str(event.get("affective_turn_plan_id") or "").strip()
    synchronization_id = str(event.get("synchronization_id") or "").strip()
    if not workspace_id or not plan_id or not synchronization_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence lacks affective scope identity",
        )
    return {
        "phase": "SENTENCE",
        "sentence": sentence,
        "sentence_index": sentence_index,
        "delivery_id": delivery_id,
        "delivery_status": "TASK_NODE_SENTENCE_STAGED",
        "replayed": bool(event.get("replayed")),
        "request_id": frozen.request_id,
        "graph_id": frozen.graph_id,
        "graph_revision": graph_revision,
        "node_id": frozen.node_id,
        "attempt_id": frozen.attempt_id,
        # Preserve the exact lease/grant lineage after validating the Surface
        # frame.  GovernedNodeWorker verifies this same receipt one layer
        # later before it allows the already-staged sentence to remain
        # visible; dropping the fields here incorrectly turns a valid stream
        # into an unknown-side-effect recovery boundary.
        "lease_id": frozen.lease_id,
        "lease_owner_id": frozen.lease_owner_id,
        "fencing_token": frozen.fencing_token,
        "node_grant_ref": frozen.node_grant_ref,
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
        "workspace_id": workspace_id,
        "affective_turn_plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "self_affect_revision": self_affect_revision,
    }


def _assert_next_staged_sentence(
    event: Mapping[str, Any],
    *,
    prior: Sequence[Mapping[str, Any]],
) -> None:
    expected_index = len(prior)
    if int(event["sentence_index"]) != expected_index:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence indices must be contiguous from zero",
        )
    if str(event["delivery_id"]) in {
        str(item.get("delivery_id") or "") for item in prior
    }:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "task-bound sentence delivery id was repeated in one stream",
        )


def _frozen_relationship_subject_id(frozen: FrozenNodeInput) -> str:
    input_pack = frozen.input_pack
    request_body = input_pack.get("request_body")
    request_body = request_body if isinstance(request_body, Mapping) else {}
    context_schema = str(input_pack.get("runtime_context_schema") or "").strip()
    if context_schema:
        # New Host Input freezes the relationship subject in the typed Runtime
        # Context.  The authenticated transport principal is intentionally
        # separate, and a request-body relationship_subject_id must not get a
        # chance to override the canonical value while a sentence is being
        # made visible.
        if context_schema != "natsume.runtime-context.request.v1":
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_INPUT_INVALID,
                "frozen Chat runtime context schema is invalid",
            )
        subject = str(input_pack.get("subject_id") or "").strip()
    else:
        # Compatibility only for accepted graphs that predate typed Runtime
        # Context.  New input always takes the canonical branch above.
        subject = str(
            input_pack.get("relationship_subject_id")
            or request_body.get("relationship_subject_id")
            or ""
        ).strip()
    if not subject:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen Chat context lacks canonical relationship subject",
        )
    return subject
