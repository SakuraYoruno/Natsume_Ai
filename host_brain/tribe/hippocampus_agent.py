from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class HippocampusAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="hippocampus_agent",
            family="hippocampus",
            capabilities=("episodic_store", "recall_fusion", "retrieval_loop"),
        )
    )

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
