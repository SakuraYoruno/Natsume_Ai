from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class HippocampusAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="hippocampus_agent",
            family="hippocampus",
            capabilities=("episodic_store", "semantic_memory", "autobiographical_memory", "recall_fusion", "retrieval_loop"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)  # T-36：使用全局单例

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    async def recall(
        self,
        *,
        request_id: str,
        message: str,
        person_id: str,
        session_id: str,
        ts_ms: int,
        emotion_tag: str | None,
        render_target_lang: str,
        mood_state: str | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        motive_state: Mapping[str, Any] | None = None,
        top_k: int | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from host_brain.app.dependencies import get_retrieval_orchestrator  # noqa: PLC0415
        from host_brain.retrieval.orchestrator import RetrievalOrchestratorRequest  # noqa: PLC0415

        if top_k is None:
            try:
                resolved_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "8").strip())
            except ValueError:
                resolved_top_k = 8
        else:
            resolved_top_k = int(top_k)

        normalized_bond_summary = dict(bond_summary) if isinstance(bond_summary, Mapping) and bond_summary else None
        normalized_motive_state = dict(motive_state) if isinstance(motive_state, Mapping) and motive_state else None
        session_state: dict[str, Any] = {
            "person_id": person_id,
            "session_id": session_id,
            "emotion_tag": emotion_tag,
        }
        if mood_state:
            session_state["mood_state"] = mood_state
        if normalized_bond_summary:
            session_state["bond_summary"] = dict(normalized_bond_summary)
        if normalized_motive_state:
            session_state["motive_state"] = dict(normalized_motive_state)
        # T-49：将 ImmuneAgent 运行时状态注入 session_state，
        # 供 RetrievalOrchestrator 在检索时参考安全状态（如 risk_level/block_rate）。
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
            _immune = get_immune_agent().export_runtime_state()
            session_state["immune_state"] = {
                "risk_level": _immune.get("risk_level", "none"),
                "block_rate": _immune.get("block_rate", 0.0),
                "last_decision": _immune.get("last_decision", "ALLOW"),
            }
        except Exception:
            pass

        orchestrator = get_retrieval_orchestrator()
        result = await orchestrator.arun(
            request=RetrievalOrchestratorRequest(
                trace_id=request_id,
                query=message,
                session_state=session_state,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                top_k=max(1, resolved_top_k),
                ts_ms=ts_ms,
                emotion_context=emotion_tag,
                mood_state=mood_state,
                bond_summary=normalized_bond_summary,
                motive_state=normalized_motive_state,
                render_target_lang=render_target_lang,
            )
        )
        return (
            dict(result.retrieval_summary),
            list(result.retrieval_evidence_items),
        )

    def build_prompt_retrieval_rows(
        self,
        *,
        retrieval_summary: Mapping[str, Any],
        retrieval_evidence_items: list[dict[str, Any]],
        max_items: int = 10,
        snippet_limit: int = 240,
    ) -> list[dict[str, Any]]:
        from host_brain.app.prompt_builder import _truncate_for_prompt  # noqa: PLC0415

        query_plan_raw = retrieval_summary.get("query_plan")
        query_plan = dict(query_plan_raw) if isinstance(query_plan_raw, Mapping) else {}
        query_plan_intent = str(query_plan.get("intent") or "").strip().lower()
        query_plan_text_len = len(str(query_plan.get("original_query") or "").strip())

        rows: list[dict[str, Any]] = []
        for row in list(retrieval_evidence_items or [])[: max(1, int(max_items))]:
            if not isinstance(row, Mapping):
                continue
            snippet = _truncate_for_prompt(row.get("snippet"), limit=snippet_limit)
            if not snippet:
                continue
            metadata_raw = row.get("metadata")
            metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
            store_id = str(
                row.get("store_id")
                or metadata.get("store_id")
                or ""
            ).strip().lower()
            if self._should_drop_persona_row(
                store_id=store_id,
                score=row.get("score"),
                confidence=row.get("confidence"),
                query_plan_intent=query_plan_intent,
                query_plan_text_len=query_plan_text_len,
            ):
                continue
            retrieval_kind = "persona_utterance" if store_id == "persona_utterance" else "episodic"
            emotion_color_tag = str(
                metadata.get("emotion_color_tag") or metadata.get("emotion_tag") or ""
            ).strip().upper() or None
            suppression_flag = self._coerce_optional_bool(metadata.get("suppression_flag"))
            distortion_level = self._coerce_optional_float(metadata.get("distortion_level"))
            if distortion_level is not None:
                distortion_level = round(self._clamp_float(float(distortion_level), minimum=0.0, maximum=1.0), 4)
            rows.append(
                {
                    "slot": "retrieval",
                    "kind": retrieval_kind,
                    "source_ref": str(row.get("source_ref") or "").strip() or None,
                    "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
                    "content": snippet,
                    "score": row.get("score"),
                    "confidence": row.get("confidence"),
                    "store_id": store_id or None,
                    "canon_id": str(metadata.get("canon_id") or "").strip() or None,
                    "lang": str(metadata.get("lang") or "").strip().lower() or None,
                    "speaker": str(metadata.get("speaker") or "").strip() or None,
                    "file_rel_path": str(metadata.get("file_rel_path") or "").strip() or None,
                    "block_id": str(metadata.get("block_id") or "").strip() or None,
                    "line_index": metadata.get("line_index"),
                    "emotion_color_tag": emotion_color_tag,
                    "suppression_flag": suppression_flag,
                    "distortion_level": distortion_level,
                }
            )
        return rows

    @staticmethod
    def _coerce_optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    @staticmethod
    def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, float(value)))

    @classmethod
    def _should_drop_persona_row(
        cls,
        *,
        store_id: str,
        score: Any,
        confidence: Any,
        query_plan_intent: str,
        query_plan_text_len: int,
    ) -> bool:
        low_signal_intents = {"phatic", "affection"}
        short_chat_score_floor = 0.12
        if str(store_id or "").strip().lower() != "persona_utterance":
            return False
        if query_plan_intent in low_signal_intents:
            return True
        if query_plan_intent == "chat" and 0 < query_plan_text_len <= 8:
            row_score = cls._coerce_optional_float(confidence)
            if row_score is None:
                row_score = cls._coerce_optional_float(score)
            if row_score is not None and row_score < short_chat_score_floor:
                return True
        return False


    # ── T-36： TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-36：订阅 INTERACTION_RECORDED，每轮对话结束后将对话元数据写入
        hippocampus_agent 命名空间快照，供诊断端点查询。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # T-61：订阅孤独感信号，写入 loneliness 快照
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            # T-85：订阅任务成功事件，将任务成功写入快照
            bus.subscribe("TASK_SUCCESS", self._on_task_success)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.hippocampus_agent").warning(
                "HippocampusAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：将对话元数据写入 hippocampus_agent 快照。

        T-36：记录最近一次对话的 session_id/user_id/emotion_tag，
        供诊断端点查询和调试使用。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            user_id = str(payload.get("user_id") or "").strip()
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            self.state_registry.set_state(
                "hippocampus_agent",
                {
                    "last_interaction_session_id": session_id,
                    "last_interaction_user_id": user_id,
                    "last_interaction_emotion_tag": emotion_tag,
                    "last_interaction_ts_ms": ts_ms,
                },
                writer="hippocampus_agent",
            )
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：将孤独感数据写入 hippocampus_agent 快照。

        T-61：记录最近一次孤独感信号的强度和时间戳，
        供 recall() 注入 session_state 和诊断端点查询使用。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            bond_stage = str(payload.get("bond_stage") or "").strip().lower()
            self.state_registry.set_state(
                "hippocampus_agent",
                {
                    "last_loneliness_level": round(loneliness, 4),
                    "last_loneliness_ts_ms": ts_ms,
                    "last_loneliness_bond_stage": bond_stage,
                },
                writer="hippocampus_agent",
            )
        except Exception:
            pass

    async def _on_task_success(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：将任务成功写入 hippocampus_agent 快照。

        T-85：记录最近一次任务成功的 task_type / intensity / ts_ms，
        供 recall() 注入 session_state 和诊断端点查询使用。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            task_type = str(payload.get("task_type") or "unknown").strip().lower() or "unknown"
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            self.state_registry.set_state(
                "hippocampus_agent",
                {
                    "last_task_success_type": task_type,
                    "last_task_success_intensity": round(intensity, 4),
                    "last_task_success_ts_ms": ts_ms,
                },
                writer="hippocampus_agent",
            )
        except Exception:
            pass


    # ── T-160：user_identity 族化入口 ────────────────────────────────────────

    def resolve_bond_user_id(
        self,
        *,
        fallback: str = "user_1",
    ) -> str:
        """T-160：获取规范化 Bond 用户 ID，将 user_identity 族化到 HippocampusAgent。

        HippocampusAgent 作为 Bond 用户 ID 的唯一解析入口，封装
        `get_canonical_bond_user_id()` 调用，确保用户 ID 解析逻辑不散落在
        chat_context_builder 等模块中。

        Args:
            fallback: 解析失败时的降级值，默认 "user_1"。

        Returns:
            规范化 Bond 用户 ID 字符串。
        """
        try:
            from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

            uid = get_canonical_bond_user_id()
            return str(uid or fallback).strip() or str(fallback)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.hippocampus_agent").debug(
                "HippocampusAgent.resolve_bond_user_id 失败（降级）: %s", exc
            )
            return str(fallback or "user_1")

    def _write_memory_scope(
        self,
        *,
        writer_agent_id: str,
        payload: Mapping[str, Any],
        request_id: str = "",
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_state_write_arbiter  # noqa: PLC0415
        from host_brain.app.memory.state_write_arbiter import StateScope  # noqa: PLC0415

        arbiter = get_state_write_arbiter()
        result = arbiter.write(
            scope=StateScope.MEMORY_STATE,
            writer_agent_id=str(writer_agent_id or "").strip(),
            payload=dict(payload or {}),
            request_id=str(request_id or "").strip(),
            merge=True,
        )
        return {
            "status": str(result.status or "ERROR"),
            "written": bool(result.written),
            "state_version": int(result.state_version or 0),
            "reason_code": result.reason_code,
            "detail": result.detail,
            "payload": dict(result.payload or {}),
        }

    def _merge_memory_layers(
        self,
        *,
        updates: Mapping[str, Any],
        last_write_type: str,
    ) -> dict[str, Any]:
        snapshot = self.state_registry.get_state("hippocampus_agent") or {}
        state = dict(snapshot.get("state") or {})
        memory_layers = dict(state.get("memory_layers") or {})
        for key, value in dict(updates or {}).items():
            if isinstance(value, Mapping):
                memory_layers[str(key)] = dict(value)
            else:
                memory_layers[str(key)] = value
        memory_layers["last_write_type"] = str(last_write_type or "").strip()
        return memory_layers

    def write_episodic_memory_trace(
        self,
        *,
        person_id: str,
        session_id: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        payload = {
            "episodic_last": {
                "person_id": str(person_id or "user_1").strip() or "user_1",
                "session_id": str(session_id or "default").strip() or "default",
                "content": str(content or "").strip(),
                "metadata": dict(metadata or {}),
            }
        }
        arbiter_result = self._write_memory_scope(
            writer_agent_id="episodic_memory_agent",
            payload=payload,
            request_id=request_id,
        )
        self.state_registry.write(
            namespace="hippocampus_agent",
            state={
                "memory_layers": self._merge_memory_layers(
                    updates={"last_episodic": payload["episodic_last"]},
                    last_write_type="episodic",
                )
            },
            writer="hippocampus_agent",
            merge=True,
            reason="episodic_memory_write",
            input_refs=[str(request_id or "").strip()] if str(request_id or "").strip() else None,
        )
        return {"layer": "episodic", "arbiter": arbiter_result, "payload": payload["episodic_last"]}

    def write_semantic_memory_fact(
        self,
        *,
        person_id: str,
        fact: str,
        source_refs: list[str] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        payload = {
            "semantic_last": {
                "person_id": str(person_id or "user_1").strip() or "user_1",
                "fact": str(fact or "").strip(),
                "source_refs": [str(item).strip() for item in list(source_refs or []) if str(item).strip()],
            }
        }
        arbiter_result = self._write_memory_scope(
            writer_agent_id="semantic_memory_agent",
            payload=payload,
            request_id=request_id,
        )
        self.state_registry.write(
            namespace="hippocampus_agent",
            state={
                "memory_layers": self._merge_memory_layers(
                    updates={"last_semantic": payload["semantic_last"]},
                    last_write_type="semantic",
                )
            },
            writer="hippocampus_agent",
            merge=True,
            reason="semantic_memory_write",
            input_refs=[str(request_id or "").strip()] if str(request_id or "").strip() else None,
        )
        return {"layer": "semantic", "arbiter": arbiter_result, "payload": payload["semantic_last"]}

    def write_autobiographical_memory(
        self,
        *,
        person_id: str,
        narrative: str,
        gate_result: Mapping[str, Any] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        gate = dict(gate_result or {})
        allow = str(gate.get("decision") or "").strip().upper() == "ALLOW"
        if not allow:
            return {
                "layer": "autobiographical",
                "status": "BLOCKED",
                "reason_code": str(gate.get("reason_code") or "AUTOBIO_GATE_BLOCKED"),
            }
        payload = {
            "autobiographical_last": {
                "person_id": str(person_id or "user_1").strip() or "user_1",
                "narrative": str(narrative or "").strip(),
                "gate_trace_id": str(gate.get("trace_id") or "").strip() or None,
            }
        }
        arbiter_result = self._write_memory_scope(
            writer_agent_id="resident_memory_agent",
            payload=payload,
            request_id=request_id,
        )
        self.state_registry.write(
            namespace="hippocampus_agent",
            state={
                "memory_layers": self._merge_memory_layers(
                    updates={"last_autobiographical": payload["autobiographical_last"]},
                    last_write_type="autobiographical",
                )
            },
            writer="hippocampus_agent",
            merge=True,
            reason="autobiographical_memory_write",
            input_refs=[str(request_id or "").strip()] if str(request_id or "").strip() else None,
            decision_trace_id=str(gate.get("trace_id") or "").strip() or None,
        )
        return {"layer": "autobiographical", "arbiter": arbiter_result, "payload": payload["autobiographical_last"]}

    def resolve_memory_layer_snapshot(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("hippocampus_agent") or {}
        state = dict(snap.get("state") or {})
        layers = dict(state.get("memory_layers") or {})
        return {
            "episodic": dict(layers.get("last_episodic") or {}),
            "semantic": dict(layers.get("last_semantic") or {}),
            "autobiographical": dict(layers.get("last_autobiographical") or {}),
            "last_write_type": str(layers.get("last_write_type") or ""),
        }

    def export_runtime_state(self) -> dict[str, Any]:
        """T-271：导出 HippocampusAgent 当前运行时状态快照。

        供 CortexAgent.collect_tribe_context() 和 chat_context_builder 使用，
        不触发任何副作用（不 emit 事件，不写 state_registry）。

        Returns:
            dict: 包含 last_write_type/episodic_count/semantic_count 等关键字段。
        """
        snap = self.state_registry.get_state("hippocampus_agent") or {}
        state = dict(snap.get("state") or {})
        layers = dict(state.get("memory_layers") or {})
        last_episodic = dict(layers.get("last_episodic") or {})
        last_semantic = dict(layers.get("last_semantic") or {})
        last_autobiographical = dict(layers.get("last_autobiographical") or {})
        return {
            "last_write_type": str(layers.get("last_write_type") or ""),
            "has_episodic": bool(last_episodic),
            "has_semantic": bool(last_semantic),
            "has_autobiographical": bool(last_autobiographical),
            "last_episodic_session_id": str(last_episodic.get("session_id") or ""),
            "last_semantic_person_id": str(last_semantic.get("person_id") or ""),
            "last_autobiographical_person_id": str(last_autobiographical.get("person_id") or ""),
            "write_count": int(state.get("write_count") or 0),
        }


_HIPPOCAMPUS_AGENT: HippocampusAgent | None = None


def get_hippocampus_agent() -> HippocampusAgent:
    global _HIPPOCAMPUS_AGENT
    if _HIPPOCAMPUS_AGENT is None:
        _HIPPOCAMPUS_AGENT = HippocampusAgent()
    return _HIPPOCAMPUS_AGENT


def set_hippocampus_agent(agent: HippocampusAgent | None) -> None:
    global _HIPPOCAMPUS_AGENT
    _HIPPOCAMPUS_AGENT = agent


__all__ = ["HippocampusAgent", "get_hippocampus_agent", "set_hippocampus_agent"]
