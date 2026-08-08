from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time

import pytest

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.host_runtime_context_wiring import compose_host_runtime_context
from host_brain.app.acceptance_effect_handlers import (
    _surface_delivery_id,
    _surface_gateway_payload_digest,
    build_production_acceptance_effect_handlers,
)
from host_brain.app.conversation_writer import (
    record_assistant_turn,
    record_user_turn,
)
from host_brain.app.governed_task_adapter import build_governed_host_runtime
from host_brain.app.host_life_scheduler_wiring import compose_life_dispatchers
from host_brain.app.user_preference_store import UserPreferenceStore
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition import process_services as dependencies
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.composition.memory import build_process_memory_container
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.emotion_context import resolve_chat_emotion_context
from host_brain.app.production_authority import (
    build_durable_production_authority_verifier,
)
from host_brain.app.surface_runtime_composition import build_surface_runtime_ports
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.universal_route_acceptance_reader import (
    UniversalRouteAcceptanceReader,
)
from host_brain.model_runtime import (
    CallableProviderAdapter,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)
from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.persona.context.topic_ledger import SessionTopicLedgerStore
from host_brain.release.universal_route_acceptance import (
    build_universal_route_acceptance_receipt,
)
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.runtime.environment_state import build_environment_snapshot
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)
from natsume_contracts.edge.transport_v1 import EdgeTransportResult
from natsume_contracts.task import HostCommandSurface


_CAPABILITY_PLAN_ARGS = {
    "planning_mode": "CAPABILITY_SEARCH_V1",
    # The production registry contributes the candidates.  The caller cannot
    # name a worker or smuggle an ordered execution list into this plan.
    "planning_capability_tags": ["production"],
    "planning_max_risk_level": 2,
}


def _intent(
    *,
    request_id: str,
    session_id: str,
    seq: int,
    route: str,
    target_id: str,
    task: str,
    plan_args: dict[str, object] | None = None,
) -> dict:
    value = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-{request_id}",
        "request_id": request_id,
        "ts_ms": int(time.time() * 1000),
        "session_id": session_id,
        "seq": seq,
        "nonce": f"nonce-{request_id}",
        "type": "query",
        "action": "narrative_only" if route == "chat" else "exec_intent",
        "target": "host",
        "route": route,
        "target_id": target_id,
        "task": task,
        "person_id": "person-gr05-universal",
        "principal_id": "person-gr05-universal",
        "args": {"text": task},
    }
    if plan_args is not None:
        assert route == "plan"
        value["args"].update(
            {
                "task": task,
                **plan_args,
            }
        )
    return value


def _production_acceptance_effect_ports(
    *,
    ledger,
    life,
    life_memory_service,
    model_attempt_service,
) -> AgentRuntimeApplicationPorts:  # noqa: ANN001
    """Bind the same durable conversation writer used by Host composition.

    This integration fixture exercises the governed runtime directly, so it
    does not build the entire ASGI lifecycle container.  The Acceptance Effect
    dispatcher still needs the real application port for its first durable
    write; omitting it intentionally leaves the graph queue in reconciliation
    and correctly prevents any outward reply delivery.
    """

    async def _unused_async(**_kwargs):  # noqa: ANN003, ANN202
        return None

    topic_ledger_store = SessionTopicLedgerStore(ledger)
    tactic_ledger_store = SessionTacticLedgerStore(ledger)

    def _record_user_turn(**kwargs):  # noqa: ANN003
        return record_user_turn(
            ledger=ledger,
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            **kwargs,
        )

    def _record_assistant_turn(**kwargs):  # noqa: ANN003
        return record_assistant_turn(
            ledger=ledger,
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            session_topic_ledger_store=topic_ledger_store,
            session_tactic_ledger_store=tactic_ledger_store,
            **kwargs,
        )

    return AgentRuntimeApplicationPorts(
        record_user_turn=_record_user_turn,
        record_assistant_turn=_record_assistant_turn,
        run_retrieval=_unused_async,
        filter_retrieval_evidence_items_for_prompt=lambda **_kwargs: [],
        build_user_preference_pack=lambda **_kwargs: {},
        resolve_host_llm_runtime_async=_unused_async,
        resolve_surface_reply_llm_runtime=lambda **kwargs: dict(
            kwargs.get("base_runtime") or {}
        ),
        build_conversation_experience_read_pack=lambda **_kwargs: {},
        conversation_history=lambda: None,
        life_memory_service=lambda: life_memory_service,
        audit_ledger=lambda: ledger,
        prompt_registry=lambda: None,
        model_attempt_service=lambda: model_attempt_service,
        context_read_ports=ConversationContextReadPorts(
            conversation_history=lambda: None,
            conversation_turn_store=lambda: None,
            session_topic_ledger_store=lambda: topic_ledger_store,
            session_tactic_ledger_store=lambda: tactic_ledger_store,
        ),
        task_status_projection=lambda: None,
        persist_post_process_audit=_unused_async,
    )


class _AlwaysWriteMemoryWorth:
    def evaluate(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        return SimpleNamespace(
            decision="write",
            worth_score=0.8,
            tags=["governed-runtime"],
            reason="gr05",
        )


class _AllowMemoryGuard:
    def validate(self, _candidate, *, tags):  # noqa: ANN001, ANN201
        del tags
        return None


async def _execute(
    runtime,
    *,
    intent: dict,
    governor: dict,
    request_body: object | None = None,
    command_authority: dict | None = None,
    emotion_context: dict | None = None,
) -> list[dict]:  # noqa: ANN001
    if request_body is None:
        request_body = SimpleNamespace()
        if intent["route"] == "chat":
            request_body = SimpleNamespace(
                input_source="GATEWAY",
                reply_channel="qq",
                reply_target="qq_dm_gr05_universal",
            )
    return [
        event
        async for event in runtime.execute_stream(
            request_body=request_body,
            request_id=intent["request_id"],
            session_id=intent["session_id"],
            ts_ms=intent["ts_ms"],
            intent_draft=intent,
            governor_result=governor,
            person_id=intent["person_id"],
            sanitized_message=intent["task"],
            emotion_context=(
                dict(emotion_context)
                if isinstance(emotion_context, dict)
                else {"emotion_tag": "NEUTRAL"}
            ),
            render_target_lang="zh",
            command_authority=command_authority,
        )
    ]


class _HostReviewContinuationRuntime:
    def __init__(self, *, runtime, command_repository) -> None:  # noqa: ANN001
        self._runtime = runtime
        self._commands = command_repository
        self.results: list[dict] = []
        self.errors: list[BaseException] = []

    async def continue_review_continuation(self, claim: dict) -> dict:
        try:
            record = self._commands.get_by_graph(
                graph_id=str(claim.get("graph_id") or ""),
                graph_revision=int(claim.get("graph_revision") or 0),
            )
            assert record is not None
            result = await self._runtime.continue_review_admission(record, claim)
        except BaseException as exc:
            self.errors.append(exc)
            raise
        self.results.append(dict(result))
        return dict(result)


def _gateway_ingress_authority(
    db_path,
    *,
    request_id: str,
    suffix: str,
) -> tuple[SQLiteSecureIngressStore, dict[str, str]]:
    body_sha256 = hashlib.sha256(
        f"gr05-universal-gateway:{suffix}".encode("utf-8")
    ).hexdigest()
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    claim = store.try_claim(
        principal_id="service.edge.gateway",
        surface="host.input",
        action="host:input",
        required_scopes=("host:input",),
        idempotency_key=f"gr05-universal:{suffix}:idempotency",
        nonce=f"gr05-universal:{suffix}:nonce",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=1_800_000_000_000,
        expires_at_ms=1_800_000_060_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"gr05-universal:{suffix}:correlation",
            surface="host.input",
            action="host:input",
            method="POST",
            normalized_path="/v1/host/input",
            principal_id="service.edge.gateway",
            tenant_id="gr05-universal",
            key_id="gr05-universal-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=("host:input",),
            required_scopes=("host:input",),
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + "2" * 64,
            nonce_digest="sha256:" + "3" * 64,
            transport_key_digest="sha256:" + "4" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=1_800_000_000_000,
        ),
    )
    return store, {
        "principal_id": "service.edge.gateway",
        "tenant_id": "gr05-universal",
        "claim_ref": str(claim.claim_ref),
        "audit_ref": str(audit_ref),
        "body_sha256": body_sha256,
    }


@pytest.mark.asyncio
async def test_chat_skill_delegate_and_plan_share_real_governed_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    provider_calls: list[object] = []
    skill_calls: list[str] = []
    delivered_replies: list[dict] = []

    async def controlled_provider(attempt):  # noqa: ANN001, ANN202
        """Replace only the terminal provider transport, never the model runtime."""

        provider_calls.append(attempt)
        reply_text = (
            "已根据两项已验收执行结果完成汇总。"
            if "plan" in str(attempt.request_id)
            else "先固定目标和边界，再执行下一步。"
        )
        return ProviderResponse(
            content=json.dumps(
                {
                    "reply_text": reply_text,
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            ),
            finish_reason="stop",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 6,
                "total_tokens": 18,
                "model_cost_micros": 27,
            },
            metadata={
                "transport_identity": "controlled-provider://gr05/chat-budget",
                "cache_hit": False,
            },
        )

    async def controlled_provider_stream(attempt):  # noqa: ANN001, ANN202
        """Emit the same controlled provider through the typed final stream.

        This is deliberately a local transport double, not an external-model
        acceptance claim.  The Host composition, ModelAttemptService,
        TaskGraph, budget ledger and SQLite delivery ledger remain real.
        """

        provider_calls.append(attempt)
        sentence_one = "先固定目标和边界。"
        sentence_two = "再执行下一步。"
        yield ProviderStreamEvent.delta(sentence_one)
        yield ProviderStreamEvent.delta(sentence_two)
        yield ProviderStreamEvent.final(
            ProviderResponse(
                content=sentence_one + sentence_two,
                finish_reason="stop",
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                    "model_cost_micros": 30,
                },
                metadata={
                    "transport_identity": "controlled-provider://gr05/chat-stream",
                    "cache_hit": False,
                },
            )
        )

    def frozen_skill_worker(_order, *, store, run_dir, **_kwargs):  # noqa: ANN001, ARG001
        skill_id = str(_order.metadata.get("skill_id") or "")
        skill_calls.append(skill_id)
        result_text = (
            "文档审查结果：先核对目标、信息层级与关键说明，再记录缺失证据和待确认边界。"
            if skill_id == "agent_document_digest"
            else "前端验收结果：覆盖主要流程、空态、错误态、响应式与键盘无障碍，并按影响分级。"
        )
        return {
            "status": "EXECUTED",
            "summary": "frozen skill completed",
            "result_text": result_text,
            "metadata": {"worker_terminal": True},
        }

    monkeypatch.setattr(
        "host_brain.domain_packs.production_runtime._execute_worker",
        frozen_skill_worker,
    )

    async def frozen_gateway_transport(*, path, payload, timeout_ms):  # noqa: ANN001
        request_id = str(payload.get("request_id") or "")
        with sqlite3.connect(db_path) as conn:
            user_turn_count = conn.execute(
                """
                SELECT COUNT(*) FROM conversation_turn
                WHERE request_id = ? AND role = 'user'
                """,
                (request_id,),
            ).fetchone()[0]
            assistant_turn_count = conn.execute(
                """
                SELECT COUNT(*) FROM conversation_turn
                WHERE request_id = ? AND role = 'assistant'
                """,
                (request_id,),
            ).fetchone()[0]
            memory_receipt_count = conn.execute(
                """
                SELECT COUNT(*) FROM life_memory_admission_receipt
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()[0]
            projection_intent_count = conn.execute(
                """
                SELECT COUNT(*) FROM life_projection_intent
                WHERE target = 'surface.post_process'
                  AND json_extract(payload_json, '$.request_id') = ?
                """,
                (request_id,),
            ).fetchone()[0]
        # Only the canonical user turn belongs to the ordinary Chat reply
        # prefix. Life, assistant/memory projections and expression intents
        # remain durable but settle after Gateway intake.
        assert user_turn_count == 1
        assert projection_intent_count == 0
        delivered_replies.append(
            {
                "request_id": request_id,
                "payload": dict(payload),
                "path": path,
                "timeout_ms": timeout_ms,
                "user_turn_count_before_delivery": user_turn_count,
                "assistant_turn_count_before_delivery": assistant_turn_count,
                "memory_receipt_count_before_delivery": memory_receipt_count,
                "projection_intent_count_before_delivery": (
                    projection_intent_count
                ),
            }
        )
        surface_value = {
            "request_id": str(payload.get("request_id") or ""),
            "reply_channel": str(payload.get("platform") or ""),
            "reply_target": str(payload.get("conversation_id") or ""),
            "narrative": str(payload.get("content") or ""),
        }
        return EdgeTransportResult(
            success=True,
            status="SENT",
            reason_code=None,
            detail="frozen terminal network transport accepted",
            event={
                "delivery_id": _surface_delivery_id(surface_value),
                "outbox_id": "gateway-outbox-gr05-universal-runtime",
                "platform": str(payload.get("platform") or ""),
                "conversation_id": str(payload.get("conversation_id") or ""),
                    "payload_digest": _surface_gateway_payload_digest(
                        surface_value,
                        effect_id=str(payload.get("effect_id") or ""),
                    ),
            },
            latency_ms=1,
        )

    monkeypatch.setattr(
        "host_brain.channels.edge_transport._submit_gateway_payload",
        frozen_gateway_transport,
    )

    configured_database = str(
        os.environ.get("NATSUME_UNIVERSAL_ACCEPTANCE_DATABASE") or ""
    ).strip()
    db_path = (
        Path(configured_database).expanduser().resolve()
        if configured_database
        else tmp_path / "universal-host-runtime.sqlite3"
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if configured_database and db_path.exists():
        db_path.unlink()
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    model_composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(
            RouteDecision(
                decision_id="gr05-controlled-surface-route",
                targets=(
                    ProviderTarget(
                        adapter_id="gr05.controlled-provider",
                        provider="controlled-provider",
                        model_id="controlled-chat-model",
                        timeout_ms=5_000,
                    ),
                ),
                reason="GR05_TERMINAL_TRANSPORT_ONLY",
                max_attempts=2,
            )
        ),
        provider_adapters=(
            CallableProviderAdapter(
                "gr05.controlled-provider",
                complete_fn=controlled_provider,
                stream_result_fn=controlled_provider_stream,
            ),
        ),
        ledger=ledger,
    )
    model_attempt_port = ApplicationModelAttemptServicePort(
        service_provider=lambda: model_composition.attempt_service,
    )
    authority_verifier = build_durable_production_authority_verifier(
        ledger_provider=lambda: ledger,
    )
    container = None
    runtime = None
    embodiment_runtime = None
    life = None
    memory = None
    chat_ingress_store = None
    ingress_store = None
    mismatch_ingress_store = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        PROCESS_LIFE_PROVIDER.clear()
        life = build_process_life_container(
            ledger=ledger,
            adopted_at_ms=int(time.time() * 1000),
        )
        PROCESS_LIFE_PROVIDER.install(life)
        memory = build_process_memory_container(
            ledger=ledger,
            canonical_db_path=db_path,
            worth_evaluator=_AlwaysWriteMemoryWorth(),
            memory_guard=_AllowMemoryGuard(),
        )
        monkeypatch.setattr(dependencies.get_process_service_container(), "ledger", ledger)
        monkeypatch.setattr(
            dependencies.get_process_service_container(),
            "capability_runtime_composition",
            None,
        )
        registry = build_agent_runtime_skill_registry()
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=registry,
        )

        class UnusedEmbodimentCommandPort:
            async def submit(self, _command):  # noqa: ANN001
                raise AssertionError(
                    "the governed Host integration must not bypass Acceptance effects"
                )

        # Reuse the same production composition boundary as the Host ASGI
        # lifespan.  The real durable embodiment lease fences World, Scene and
        # Guardian writes; ActionPreview then reopens the canonical
        # HostCommand/TaskGraph, active node grant and frozen Runtime Context.
        # This keeps the positive Gateway path production-equivalent instead
        # of weakening the fail-closed legacy-effect quarantine.
        embodiment_runtime = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="gr05-universal-runtime-context-owner",
            command_port=UnusedEmbodimentCommandPort(),
            authority_lease_ms=600_000,
        )
        runtime_context_application = compose_host_runtime_context(
            task_runtime=container,
            ledger=ledger,
            embodiment_runtime=embodiment_runtime,
            environment_observation_provider=(
                lambda *, now_ms: {
                    **build_environment_snapshot(now_ms=now_ms),
                    "activity_state": "NORMAL",
                    "mode_identity": "normal",
                }
            ),
        )
        action_preview_service = ActionPreviewService(
            runtime_event_store=runtime_context_application.composition.event_store,
            guardian=runtime_context_application.composition.guardian,
            authority_resolver=ProductionActionPreviewAuthorityResolver(
                command_repository=container.command_repository,
                task_store=container.task_store,
                node_grant_verifier=DurableNodeGrantVerifier(
                    ledger=ledger,
                    review_store=container.review_continuation_store,
                ),
                runtime_context_application=runtime_context_application,
            ),
        )
        agent_runtime_ports = _production_acceptance_effect_ports(
            ledger=ledger,
            life=life,
            life_memory_service=memory.service,
            model_attempt_service=model_composition.attempt_service,
        )
        surface_runtime_ports = build_surface_runtime_ports(
            agent_runtime_ports=agent_runtime_ports,
            life_authority_provider=lambda: (
                life.command_service,
                life.snapshot_reader,
            ),
        )
        projection_dispatcher, _outbox_dispatcher = compose_life_dispatchers(
            ledger,
            embodiment_runtime=embodiment_runtime,
            action_preview_service=action_preview_service,
            agent_runtime_ports=agent_runtime_ports,
        )
        acceptance_effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                conversation_turn_reader=ContextRepository.from_ledger(ledger),
                conversation_event_reader=MemoryEventRepository.from_ledger(
                    ledger
                ),
                    preference_store=UserPreferenceStore(ledger=ledger),
                life_command_service_provider=lambda: life.command_service,
                life_receipt_reader_provider=lambda: life.snapshot_reader,
                life_projection_dispatcher=projection_dispatcher,
                agent_runtime_ports=agent_runtime_ports,
                # The Host ASGI composition supplies the same durable
                # embodiment runtime to accepted companion-expression effects.
                # This integration fixture creates that real Host runtime above,
                # so keep its wiring production-equivalent rather than leaving
                # expression receipts without a committed handler.
                embodiment_runtime_provider=lambda: embodiment_runtime,
            ),
            owner_id="gr05-universal-acceptance-effects",
            action_preview_service=action_preview_service,
        )
        runtime = build_governed_host_runtime(
            task_runtime_container=container,
            ledger=ledger,
            skill_registry=registry,
            token_service=token_service,
            capability_broker=dependencies.get_capability_broker(),
            model_attempt_service=model_attempt_port,
            production_authority_verifier=authority_verifier,
            resource_manager=None,
            episodic_store=None,
            semantic_recaller=None,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="gr05-universal-owner",
            runtime_context_application=runtime_context_application,
            degradation_capability_port=(
                runtime_context_application.composition.guardian
            ),
            action_preview_service=action_preview_service,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
            surface_runtime_ports=surface_runtime_ports,
        )
        await runtime.activate()

        chat = _intent(
            request_id="request-gr05-chat",
            session_id="session-gr05-universal",
            seq=1,
            route="chat",
            target_id="surface_reply",
            task="reply through governed chat",
        )
        chat_ingress_store, chat_authority = _gateway_ingress_authority(
            db_path,
            request_id=chat["request_id"],
            suffix="chat",
        )
        chat["principal_id"] = chat_authority["principal_id"]
        # Build the emotion hand-off through the actual canonical fusion
        # service before the governed runtime freezes it.  In particular, the
        # secure transport principal remains separate from the relationship
        # subject and can never become Natsume's self-affect identity.
        chat_fusion_request = SimpleNamespace(
            message=chat["task"],
            workspace_id="tenant:gr05-universal",
            person_id=chat["person_id"],
            session_id=chat["session_id"],
            emotion_context={},
            emotion_tag=None,
            emotion_intensity=None,
            multimodal_observations={},
        )
        chat_affect_fusion = resolve_chat_emotion_context(
            affect_fusion_service=AffectFusionService(
                command_service_provider=lambda: life.command_service,
                snapshot_reader_provider=lambda: life.snapshot_reader,
                clock_ms=lambda: chat["ts_ms"],
            ),
            request_body=chat_fusion_request,
            request_id=chat["request_id"],
        )
        assert chat_affect_fusion["_affective_life_internal"] is True
        skill = _intent(
            request_id="request-gr05-skill",
            session_id="session-gr05-universal",
            seq=2,
            route="skill",
            target_id="agent_workbench_brief",
            task="build a bounded governed work brief",
        )
        delegate = _intent(
            request_id="request-gr05-delegate",
            session_id="session-gr05-universal",
            seq=3,
            route="delegate",
            target_id="pentest_agent",
            task="prepare a read-only threat model for example.test",
        )
        plan = _intent(
            request_id="request-gr05-plan",
            session_id="session-gr05-universal",
            seq=4,
            route="plan",
            target_id="planning_search",
            task="select and synthesize a bounded production capability plan",
            plan_args=_CAPABILITY_PLAN_ARGS,
        )
        ingress_store, plan_authority = _gateway_ingress_authority(
            db_path,
            request_id=plan["request_id"],
            suffix="plan",
        )

        results: dict[str, list[dict]] = {}
        governors: dict[str, dict] = {}
        for value in (chat, skill, delegate, plan):
            # The real Host Input pipeline freezes the authenticated Secure
            # Ingress principal into the intent *before* Governor admission.
            # Keep this direct-runtime test on that same side of the boundary:
            # person_id remains the conversational projection, while the
            # durable Governor grant is bound to the command principal.
            if value["route"] == "chat":
                value["principal_id"] = chat_authority["principal_id"]
            if value["route"] == "plan":
                value["principal_id"] = plan_authority["principal_id"]
            governor = engine.evaluate_intent(
                raw_intent=value,
                request_id=value["request_id"],
                current_ms=value["ts_ms"],
                governance_snapshot={"trust_level": 100, "energy_level": 100},
            )
            assert governor["decision"] == "APPROVE", governor
            assert runtime.supports(value) is True
            governors[value["route"]] = governor
            execution_kwargs: dict[str, object] = {}
            if value["route"] == "chat":
                execution_kwargs = {
                    "request_body": SimpleNamespace(
                        input_source="GATEWAY",
                        reply_channel="qq",
                        reply_target="qq_dm_gr05_universal",
                    ),
                    "command_authority": chat_authority,
                    "emotion_context": chat_affect_fusion,
                }
            if value["route"] == "plan":
                execution_kwargs = {
                    "request_body": SimpleNamespace(input_source="GATEWAY"),
                    "command_authority": plan_authority,
                }
            results[value["route"]] = await _execute(
                runtime,
                intent=value,
                governor=governor,
                **execution_kwargs,
            )
        assert results["chat"][-1]["execution_receipt"]["status"] == "ACCEPTED", (
            json.dumps(results["chat"], ensure_ascii=False, indent=2)
        )
        delegate_waiting = results["delegate"][-1]["execution_receipt"]
        assert delegate_waiting["status"] == "WAITING_APPROVAL"
        assert delegate_waiting["attempt_id"] is None
        delegate_review = delegate_waiting["projection"]["review"]
        assert delegate_review["review_status"] == "PENDING"
        approved_delegate_review = container.review_store.approve_review(
            delegate_review["review_id"],
            actor_id="gr05-universal-delegate-reviewer",
            expected_subject_ref=delegate_review["subject_ref"],
            expected_subject_version=delegate_review["subject_version"],
            expected_resume_phase=delegate_review["resume_phase"],
            expected_checkpoint_id=delegate_review["checkpoint_id"],
        )
        container.review_store.resume_review(
            delegate_review["review_id"],
            resume_token_ref=approved_delegate_review["resume_token_ref"],
            subject_ref=delegate_review["subject_ref"],
            subject_version=delegate_review["subject_version"],
            resume_phase=delegate_review["resume_phase"],
            checkpoint_id=delegate_review["checkpoint_id"],
        )
        delegate_continuation_runtime = _HostReviewContinuationRuntime(
            runtime=runtime,
            command_repository=container.command_repository,
        )
        delivered_delegate_continuations = await ReviewContinuationWorker(
            store=container.review_continuation_store,
            runtime=delegate_continuation_runtime,
            worker_id="gr05-universal-delegate-continuation",
            claim_lease_ms=30_000,
            poll_interval_ms=60_000,
            retry_delay_ms=0,
            batch_limit=1,
        ).run_once()
        assert delivered_delegate_continuations == 1
        if delegate_continuation_runtime.errors:
            raise delegate_continuation_runtime.errors[0]
        results["delegate"].append(delegate_continuation_runtime.results[-1])
        assert (
            container.review_continuation_store.get_review_continuation(
                delegate_review["review_id"]
            )
            is None
        )

        plan_replay = await _execute(
            runtime,
            intent=plan,
            governor=governors["plan"],
            request_body=SimpleNamespace(input_source="GATEWAY"),
            command_authority=plan_authority,
        )
        chat_replay = await _execute(
            runtime,
            intent=chat,
            governor=governors["chat"],
            request_body=SimpleNamespace(
                input_source="GATEWAY",
                reply_channel="qq",
                reply_target="qq_dm_gr05_universal",
            ),
            command_authority=chat_authority,
            emotion_context=chat_affect_fusion,
        )

        # A direct caller that skips Cortex keeps the conversational principal
        # in its Governor grant while presenting an authenticated service
        # principal to planning admission. The planning preflight must reject
        # that drift before HostCommand/TaskGraph persistence.
        mismatched_plan = _intent(
            request_id="request-gr05-plan-principal-drift",
            session_id="session-gr05-universal",
            seq=5,
            route="plan",
            target_id="planning_search",
            task="reject mismatched planning principal before admission",
            plan_args=_CAPABILITY_PLAN_ARGS,
        )
        mismatch_ingress_store, mismatch_authority = _gateway_ingress_authority(
            db_path,
            request_id=mismatched_plan["request_id"],
            suffix="plan-principal-drift",
        )
        mismatch_governor = engine.evaluate_intent(
            raw_intent=mismatched_plan,
            request_id=mismatched_plan["request_id"],
            current_ms=mismatched_plan["ts_ms"],
            governance_snapshot={"trust_level": 100, "energy_level": 100},
        )
        assert mismatch_governor["decision"] == "APPROVE", mismatch_governor
        with sqlite3.connect(db_path) as conn:
            command_count_before_drift = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal"
            ).fetchone()[0]
        with pytest.raises(Exception, match="NODE_GRANT_PARENT_INVALID"):
            await _execute(
                runtime,
                intent=mismatched_plan,
                governor=mismatch_governor,
                request_body=SimpleNamespace(input_source="GATEWAY"),
                command_authority=mismatch_authority,
            )
        with sqlite3.connect(db_path) as conn:
            command_count_after_drift = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal"
            ).fetchone()[0]
        assert command_count_after_drift == command_count_before_drift

        assert results["chat"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["skill"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["delegate"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["plan"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert plan_replay[-1]["execution_receipt"]["command_id"] == (
            results["plan"][-1]["execution_receipt"]["command_id"]
        )
        assert chat_replay[-1]["execution_receipt"]["command_id"] == (
            results["chat"][-1]["execution_receipt"]["command_id"]
        )
        assert len(provider_calls) == 2
        assert len(skill_calls) == 3
        assert all(skill_id for skill_id in skill_calls)
        assert len(delivered_replies) == 1
        assert delivered_replies[0]["request_id"] == chat["request_id"]
        assert delivered_replies[0]["payload"]["platform"] == "qq"
        assert (
            delivered_replies[0]["payload"]["conversation_id"]
            == "qq_dm_gr05_universal"
        )
        assert (
            delivered_replies[0]["user_turn_count_before_delivery"],
            delivered_replies[0]["assistant_turn_count_before_delivery"],
            delivered_replies[0]["memory_receipt_count_before_delivery"],
            delivered_replies[0]["projection_intent_count_before_delivery"],
        ) == (1, 0, 0, 0)
        assert all(
            attempt.model_budget_guard is not None
            for attempt in provider_calls
        )

        # The interactive path commits the user turn and visible reply first.
        # Exercise the same durable background pass owned by the app-local
        # Acceptance Effect recovery Runtime.
        background_effects = await acceptance_effect_dispatcher.drain_ready(
            limit=100
        )
        assert [row["effect_type"] for row in background_effects] == [
            "life.conversation_turn_recorded",
            "life.foreground_completed",
            "host.companion_expression_enqueue",
            "conversation.preference_signal",
        ]
        assert await acceptance_effect_dispatcher.drain_ready(limit=100) == ()

        # Derived Memory and visible-conversation projections are drained by
        # their ordinary app-local worker after Gateway intake.
        projection = await projection_dispatcher.dispatch_pending_async(limit=100)
        projection_replay = await projection_dispatcher.dispatch_pending_async(
            limit=100
        )
        assert projection.failed == 0
        assert projection.claimed > 0
        assert projection.delivered == projection.claimed
        assert projection_replay.claimed == 0

        with sqlite3.connect(db_path) as conn:
            routes = conn.execute(
                "SELECT route_type FROM host_command_journal ORDER BY created_at_ms, command_id"
            ).fetchall()
            attempts = conn.execute(
                "SELECT COUNT(*) FROM task_runtime_attempt"
            ).fetchone()[0]
            projections = conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection"
            ).fetchone()[0]
            chat_projection_payloads = [
                json.loads(row[0])
                for row in conn.execute(
                    """
                    SELECT projection_json
                    FROM task_runtime_worker_projection
                    WHERE route_type = 'chat'
                    ORDER BY created_ts_ms, projection_id
                    """
                ).fetchall()
            ]
            delegate_projection_payloads = [
                json.loads(row[0])
                for row in conn.execute(
                    """
                    SELECT projection_json
                    FROM task_runtime_worker_projection
                    WHERE route_type = 'delegate'
                    ORDER BY created_ts_ms, projection_id
                    """
                ).fetchall()
            ]
            chat_usage_ids = [
                projection["budget_usage"]["usage_id"]
                for projection in chat_projection_payloads
            ]
            chat_budget_usage_rows = [
                conn.execute(
                    """
                    SELECT attempt_id, charge_kind, delta_json, accepted
                    FROM task_runtime_budget_usage
                    WHERE usage_id = ?
                    """,
                    (usage_id,),
                ).fetchone()
                for usage_id in chat_usage_ids
            ]
            chat_node_usage_rows = [
                conn.execute(
                    """
                    SELECT attempt_id, accepted
                    FROM task_runtime_node_budget_usage
                    WHERE usage_id = ?
                    """,
                    (usage_id,),
                ).fetchone()
                for usage_id in chat_usage_ids
            ]
            model_attempt_rows = conn.execute(
                """
                SELECT attempt_id, request_id, route_decision_id, status,
                       adapter_id, provider, model_id, prompt_digest,
                       output_digest, metadata
                FROM model_attempt_log
                WHERE status = 'succeeded'
                ORDER BY request_id, attempt_id
                """
            ).fetchall()
            assert len(delegate_projection_payloads) == 1
            delegate_usage_id = delegate_projection_payloads[0][
                "budget_usage"
            ]["usage_id"]
            delegate_budget_usage = conn.execute(
                """
                SELECT charge_kind, delta_json, accepted
                FROM task_runtime_budget_usage
                WHERE usage_id = ?
                """,
                (delegate_usage_id,),
            ).fetchone()
            legacy_delegate_logs = conn.execute(
                """
                SELECT COUNT(*) FROM skill_exec_log
                WHERE request_id IN (?, ?)
                  AND skill_name LIKE 'sub_agent:%'
                """,
                (delegate["request_id"], plan["request_id"]),
            ).fetchone()[0]
            acceptance_effects = conn.execute(
                """
                SELECT effect_type, commit_scope, status, COUNT(*)
                FROM task_runtime_acceptance_effect_intent
                GROUP BY effect_type, commit_scope, status
                ORDER BY effect_type
                """
            ).fetchall()
            plan_graph = conn.execute(
                """
                SELECT graph_id, graph_revision, surface, principal_id,
                       ingress_claim_ref, ingress_audit_ref, input_sha256
                FROM host_command_journal
                WHERE request_id = ?
                """,
                (plan["request_id"],),
            ).fetchone()
            chat_graph = conn.execute(
                """
                SELECT graph_id, graph_revision, surface, principal_id
                FROM host_command_journal
                WHERE request_id = ?
                """,
                (chat["request_id"],),
            ).fetchone()
            chat_delivery_rows = conn.execute(
                """
                SELECT event_kind, request_id, principal_id,
                       relationship_subject_id, workspace_id
                FROM task_runtime_chat_delivery_event
                WHERE request_id = ?
                ORDER BY created_ts_ms, event_id
                """,
                (chat["request_id"],),
            ).fetchall()
            assistant_turns = conn.execute(
                """
                SELECT request_id, content
                FROM conversation_turn
                WHERE session_id = ? AND role = 'assistant'
                ORDER BY request_id
                """,
                (chat["session_id"],),
            ).fetchall()
            chat_conversation_scopes = conn.execute(
                """
                SELECT role, person_id
                FROM conversation_turn
                WHERE request_id = ?
                ORDER BY turn_index
                """,
                (chat["request_id"],),
            ).fetchall()
            chat_life_receipt_scopes = conn.execute(
                """
                SELECT command_id, person_id
                FROM life_command_receipt
                WHERE command_id IN (?, ?)
                ORDER BY command_id
                """,
                (
                    f"life::conversation_turn::{chat['request_id']}::user",
                    f"life::foreground_completed::{chat['request_id']}",
                ),
            ).fetchall()
            # DL-05 must not merely mark the frozen Task stream complete.
            # Once its ``life.foreground_completed`` Acceptance Effect has
            # committed, the same immutable affective plan must be projected
            # to the durable Host Voice/Avatar protocol ledger.  This query
            # intentionally reads the real SQLite projection rather than a
            # worker-local trace or an Edge double; this integration fixture
            # does not start a remote Edge and therefore must never claim
            # physical playback/transition.
            chat_expression_delivery_rows = conn.execute(
                """
                SELECT delivery_kind, channel, action, plan_id,
                       synchronization_id, self_affect_revision,
                       receipt_status, person_id, workspace_id,
                       relationship_subject_id, session_id
                FROM affective_expression_delivery_event_v1
                WHERE person_id = ?
                  AND workspace_id = ?
                  AND relationship_subject_id = ?
                  AND session_id = ?
                ORDER BY created_at_ms, event_id, delivery_id
                """,
                (
                    chat["person_id"],
                    "tenant:gr05-universal",
                    chat["person_id"],
                    chat["session_id"],
                ),
            ).fetchall()
        assert sorted(row[0] for row in routes) == [
            "chat",
            "delegate",
            "plan",
            "skill",
        ]
        assert plan_graph[2] == HostCommandSurface.GATEWAY.value
        assert plan_graph[3] == plan_authority["principal_id"]
        assert plan_graph[4] == plan_authority["claim_ref"]
        assert plan_graph[5] == plan_authority["audit_ref"]
        assert plan_graph[6] == "sha256:" + plan_authority["body_sha256"]
        assert chat_graph[2:] == (
            HostCommandSurface.GATEWAY.value,
            chat_authority["principal_id"],
        )
        assert [row[0] for row in chat_delivery_rows] == [
            "PLAN_FROZEN",
            "SENTENCE_STAGED",
            "SENTENCE_STAGED",
            "STREAM_COMPLETED",
        ]
        assert all(row[1] == chat["request_id"] for row in chat_delivery_rows)
        assert all(row[2] == chat_authority["principal_id"] for row in chat_delivery_rows)
        assert all(row[3] == chat["person_id"] for row in chat_delivery_rows)
        assert all(row[4] == "tenant:gr05-universal" for row in chat_delivery_rows)
        assert chat_conversation_scopes == [
            ("user", chat["person_id"]),
            ("assistant", chat["person_id"]),
        ]
        assert len(chat_life_receipt_scopes) == 2
        assert all(
            person_id == chat["person_id"]
            and person_id != chat_authority["principal_id"]
            for _command_id, person_id in chat_life_receipt_scopes
        )
        # Two stable text sentences become two paired Host intents; the
        # accepted completion adds one paired RELEASE control.  The
        # projection is still deliberately Host-staged, never an
        # ``EDGE_APPLIED`` substitute, because no remote Edge is running in
        # this test or this DL-05 Host slice.
        assert len(chat_expression_delivery_rows) == 6
        assert sum(
            row[:3] == ("INTENT", "VOICE", "SPEAK")
            and row[6] == "HOST_PROTOCOL_STAGED"
            for row in chat_expression_delivery_rows
        ) == 2
        assert sum(
            row[:3] == ("INTENT", "AVATAR", "TRANSITION")
            and row[6] == "HOST_PROTOCOL_STAGED"
            for row in chat_expression_delivery_rows
        ) == 2
        assert sum(
            row[:3] == ("RELEASE", "VOICE", "RELEASE")
            and row[6] == "HOST_RELEASE_STAGED"
            for row in chat_expression_delivery_rows
        ) == 1
        assert sum(
            row[:3] == ("RELEASE", "AVATAR", "RELEASE")
            and row[6] == "HOST_RELEASE_STAGED"
            for row in chat_expression_delivery_rows
        ) == 1
        assert len({row[3] for row in chat_expression_delivery_rows}) == 1
        assert all(row[3] for row in chat_expression_delivery_rows)
        assert len({row[4] for row in chat_expression_delivery_rows}) == 1
        assert all(row[4] for row in chat_expression_delivery_rows)
        assert all(type(row[5]) is int and row[5] >= 0 for row in chat_expression_delivery_rows)
        assert all(
            row[7:] == (
                chat["person_id"],
                "tenant:gr05-universal",
                chat["person_id"],
                chat["session_id"],
            )
            for row in chat_expression_delivery_rows
        )
        assert attempts == 6
        # Chat/delegate already froze worker projections; the plan's skill
        # node now does so as well, giving every accepted route the same B5
        # result-authority journal.
        assert projections == 6
        assert len(chat_projection_payloads) == 2
        assert all(
            "governed_model_budget"
            not in projection["audit_refs"]["trace_stage_meta"]
            for projection in chat_projection_payloads
        )
        assert all(
            projection["budget_usage"]["measurements"]
            == {
                "model_cost_micros": (
                    30 if projection["request_id"] == chat["request_id"] else 27
                ),
                "retry_count": 0,
                "token_count": (
                    20 if projection["request_id"] == chat["request_id"] else 18
                ),
            }
            for projection in chat_projection_payloads
        )
        assert all(
            projection["budget_usage"]["evidence_refs"]
            == [
                projection["audit_refs"]["trace_stage_meta"][
                    "surface_model_attempt"
                ]["budget_measurement"]["measurement_ref"]
            ]
            for projection in chat_projection_payloads
        )
        assert all(
            projection["audit_refs"]["trace_stage_meta"][
                "surface_model_attempt"
            ]["cached"]
            is False
            and projection["audit_refs"]["trace_stage_meta"][
                "surface_model_attempt"
            ]["success"]
            is True
            for projection in chat_projection_payloads
        )
        assert all(row is not None for row in chat_budget_usage_rows)
        assert all(row is not None for row in chat_node_usage_rows)
        for projection, budget_row, child_row in zip(
            chat_projection_payloads,
            chat_budget_usage_rows,
            chat_node_usage_rows,
            strict=True,
        ):
            assert budget_row[0] == projection["attempt_id"]
            assert budget_row[1] == "WORKER_USAGE:COST+RETRY+TIME+TOKEN"
            delta = json.loads(budget_row[2])
            assert {
                key: delta[key]
                for key in (
                    "concurrency",
                    "model_cost_micros",
                    "retry_count",
                    "token_count",
                    "tool_call_count",
                )
            } == {
                "concurrency": 0,
                "model_cost_micros": (
                    30 if projection["request_id"] == chat["request_id"] else 27
                ),
                "retry_count": 0,
                "token_count": (
                    20 if projection["request_id"] == chat["request_id"] else 18
                ),
                "tool_call_count": 0,
            }
            assert type(delta["time_ms"]) is int and delta["time_ms"] >= 0
            assert budget_row[3] == 1
            assert child_row == (projection["attempt_id"], 1)
        assert len(model_attempt_rows) == 2
        projected_model_attempt_ids = {
            attempt_id
            for projection in chat_projection_payloads
            for attempt_id in projection["audit_refs"]["trace_stage_meta"][
                "surface_model_attempt"
            ]["attempt_ids"]
        }
        assert {row[0] for row in model_attempt_rows} == (
            projected_model_attempt_ids
        )
        assert {row[1] for row in model_attempt_rows} == {
            chat["request_id"],
            plan["request_id"],
        }
        model_node_by_request = {
            projection["request_id"]: projection["node_id"]
            for projection in chat_projection_payloads
        }
        for row in model_attempt_rows:
            assert row[2:7] == (
                "gr05-controlled-surface-route",
                "succeeded",
                "gr05.controlled-provider",
                "controlled-provider",
                "controlled-chat-model",
            )
            assert len(row[7]) == 64
            assert len(row[8]) == 64
            metadata = json.loads(row[9])
            assert metadata["provider_budget_measurements"] == {
                "completion_tokens": 8 if row[1] == chat["request_id"] else 6,
                "model_cost_micros": 30 if row[1] == chat["request_id"] else 27,
                "prompt_tokens": 12,
                "token_count": 20 if row[1] == chat["request_id"] else 18,
            }
            assert metadata["model_node_id"] == model_node_by_request[row[1]]
        assert delegate_projection_payloads[0]["budget_usage"][
            "measurements"
        ] == {
            "model_cost_micros": 0,
            "retry_count": 0,
            "token_count": 0,
            "tool_call_count": 0,
        }
        delegate_attestation = delegate_projection_payloads[0]["audit_refs"][
            "delegate_runtime_usage_attestation"
        ]
        assert delegate_attestation["target_id"] == "pentest_agent"
        assert delegate_attestation["attestation_ref"].startswith(
            "delegate-usage://sha256/"
        )
        assert delegate_budget_usage is not None
        assert delegate_budget_usage[0] == (
            "WORKER_USAGE:COST+RETRY+TIME+TOKEN+TOOL"
        )
        assert {
            key: value
            for key, value in json.loads(delegate_budget_usage[1]).items()
            if key != "time_ms"
        } == {
            "concurrency": 0,
            "model_cost_micros": 0,
            "retry_count": 0,
            "token_count": 0,
            "tool_call_count": 0,
        }
        assert delegate_budget_usage[2] == 1
        assert legacy_delegate_logs == 0
        assert acceptance_effects == [
            ("conversation.preference_signal", "GRAPH", "COMMITTED", 4),
            ("conversation.user_turn", "GRAPH", "COMMITTED", 4),
            # One accepted aggregate freezes two stable sentences and one
            # terminal release for both Voice and Avatar.  The expression
            # delivery ledger below expands it into six protocol intents.
            ("host.companion_expression_enqueue", "GRAPH", "COMMITTED", 1),
            ("life.conversation_turn_recorded", "GRAPH", "COMMITTED", 4),
            ("life.foreground_completed", "GRAPH", "COMMITTED", 4),
            ("surface.reply_delivery", "GRAPH", "COMMITTED", 1),
        ]
        assert results["chat"][-1]["audit_refs"]["acceptance_effects"] == {
            "count": 6,
            "committed": 2,
            "failed": 0,
            "pending": 4,
            "recovery_pending": 0,
            "dispatch_status": "DELIVERY_COMMITTED_BACKGROUND_PENDING",
            "delivery_committed": True,
            "background_pending": 4,
        }
        assert all(
            results[route][-1]["audit_refs"]["acceptance_effects"]
            == {
                "count": 4,
                "committed": 4,
                "failed": 0,
                "pending": 0,
                "recovery_pending": 0,
                "dispatch_status": "COMMITTED",
            }
            for route in ("skill", "delegate", "plan")
        )
        assert {row[0] for row in assistant_turns} == {
            chat["request_id"],
            skill["request_id"],
            delegate["request_id"],
            plan["request_id"],
        }
        assert len(assistant_turns) == 4
        graph = container.task_store.get_graph(plan_graph[0], plan_graph[1])
        assert graph["status"] == TaskGraphStatus.COMPLETED.value
        assert [node["status"] for node in graph["nodes"]] == [
            "ACCEPTED",
            "ACCEPTED",
            "ACCEPTED",
        ]
        acceptance_request_ids = {
            "chat": chat["request_id"],
            "skill": skill["request_id"],
            "delegate": delegate["request_id"],
            "plan": plan["request_id"],
        }
        acceptance_snapshot = UniversalRouteAcceptanceReader(db_path).capture(
            acceptance_request_ids
        )
        applied = acceptance_snapshot["migration"]
        acceptance_receipt = build_universal_route_acceptance_receipt(
            source_binding={
                "commit_sha": "a" * 40,
                "migration_head": (
                    f"{int(applied['migration_id']):04d}_{applied['name']}.sql"
                ),
                "repository_clean": True,
            },
            snapshot=acceptance_snapshot,
            request_ids=acceptance_request_ids,
            generated_at_ms=int(time.time() * 1000),
        )
        assert {
            route: record["status"]
            for route, record in acceptance_receipt[
                "route_acceptance"
            ].items()
        } == {
            "chat": "PASS",
            "skill": "PASS",
            "plan": "PASS",
            "delegate": "PASS",
            "scheduler": "NOT_RUN",
            "internal": "NOT_RUN",
            "operator": "NOT_RUN",
        }
        assert acceptance_receipt["overall_status"] == "INCOMPLETE"
        assert acceptance_receipt["route_acceptance"]["chat"][
            "evidence_counts"
        ]["model_attempts"] == 2
        assert acceptance_receipt["route_acceptance"]["chat"][
            "evidence_counts"
        ]["worker_model_budgets"] == 1
        assert acceptance_receipt["route_acceptance"]["plan"][
            "evidence_counts"
        ]["model_attempts"] == 2
        assert acceptance_receipt["route_acceptance"]["plan"][
            "evidence_counts"
        ]["worker_model_budgets"] == 1
        assert acceptance_receipt["privacy"] == {
            "contains_user_text": False,
            "contains_model_output": False,
            "contains_secrets": False,
            "identifiers_are_digested": True,
        }
    finally:
        if chat_ingress_store is not None:
            chat_ingress_store.close()
        if mismatch_ingress_store is not None:
            mismatch_ingress_store.close()
        if ingress_store is not None:
            ingress_store.close()
        if runtime is not None:
            await runtime.aclose()
        if embodiment_runtime is not None:
            embodiment_runtime.close()
        if container is not None:
            await container.aclose()
        if life is not None:
            PROCESS_LIFE_PROVIDER.clear(life)
        if memory is not None:
            memory.close()
        ledger.close()
