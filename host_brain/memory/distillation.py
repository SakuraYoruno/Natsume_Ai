"""记忆蒸馏引擎（M10-C/D）。

从可信生活事件或兼容 Episodic 记忆选取 top-k 来源，产出一个可追溯的语义记忆。
可选 LLM 摘要失效时使用确定性合并；无论何种策略，都不能将审计噪声、已抑制
记忆、私有内部事件或未证明的传输细节升级为长期事实。
"""

from __future__ import annotations

import hashlib
import json
import threading
import os
import re
import uuid
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms, new_request_id
from host_brain.integrations.codex_cli_provider import resolve_codex_cli_model
from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.model_runtime.live_prompts import MEMORY_DISTILLATION_PROMPT_ID
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator
from host_brain.memory.life_memory.store import LifeMemoryStore


DEFAULT_CODEX_DISTILLATION_MODEL = "gpt-5.5"


@runtime_checkable
class LlmSummaryClient(Protocol):
    """外部摘要适配端口；实现只返回摘要，不拥有记忆写入或路由选择权。"""

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        ...


def _load_distillation_llm_templates() -> dict[str, Any]:
    raw = resolve_prompt_contract_json("memory_distillation.llm_summary.prompt")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _truncate_text(text: Any, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max(1, int(limit)):
        return normalized
    return normalized[: max(1, int(limit)) - 1].rstrip() + "…"


def _resolve_template_text(templates: Mapping[str, Any], key: str, *, lang: str) -> str:
    raw = templates.get(key)
    if isinstance(raw, Mapping):
        localized = raw.get(lang) or raw.get("zh") or raw.get("ja")
        return str(localized or "").strip()
    return str(raw or "").strip()


class PoolSummaryClient:
    """经统一模型尝试服务调用的语义蒸馏摘要客户端。

    领域层只提供受截断的证据片段与场景身份，供应商选择、预算与重试仍由模型运行时
    治理；解析不到合格摘要时显式失败，交由引擎选择确定性降级。
    """

    def __init__(
        self,
        *,
        scene_type: str = "MEMORY_DISTILLATION",
        cognitive_tier: str = "B",
        timeout_ms: int = 2600,
        render_target_lang: str = "zh",
        model_attempt_service: Any,
    ) -> None:
        self._scene_type = str(scene_type or "MEMORY_DISTILLATION").strip().upper() or "MEMORY_DISTILLATION"
        self._cognitive_tier = str(cognitive_tier or "B").strip().upper() or "B"
        self._timeout_ms = max(800, int(timeout_ms))
        self._render_target_lang = "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"
        self._model_attempt_service = model_attempt_service

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        snippets = [
            f"{index + 1}. {_truncate_text(text, limit=220)}"
            for index, text in enumerate(list(texts or [])[:6])
            if str(text or "").strip()
        ]
        if not snippets:
            raise ValueError("no texts to summarize")

        templates = _load_distillation_llm_templates()
        system_prompt = _resolve_template_text(
            templates,
            "system_prompt",
            lang=self._render_target_lang,
        ) or "你负责做长期记忆蒸馏，请输出简洁、准确、可复用的语义摘要。"
        user_template = _resolve_template_text(
            templates,
            "user_prompt",
            lang=self._render_target_lang,
        ) or (
            "查询主题：{query_text}\n"
            "记忆片段：\n{memory_lines}\n\n"
            "请输出 JSON：{{\"summary\":\"...\"}}"
        )
        user_prompt = user_template.format(
            query_text=_truncate_text(query_text, limit=120),
            memory_lines="\n".join(snippets),
            max_tokens=max(64, min(int(max_tokens or 200), 400)),
        )
        result = complete_compiled_prompt_sync(
            attempt_service=self._model_attempt_service,
            prompt_id=MEMORY_DISTILLATION_PROMPT_ID,
            request_id=new_request_id(),
            scene_type=self._scene_type,
            cognitive_tier=self._cognitive_tier,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            deadline_ms=self._timeout_ms,
        )
        if not result.success:
            raise RuntimeError(result.error_code or "MODEL_ATTEMPT_FAILED")
        parsed = json.loads(result.content)
        summary = _truncate_text(parsed.get("summary"), limit=320)
        if not summary:
            raise ValueError("empty distillation summary")
        return summary


class CodexCliSummaryClient:
    """受治理后台记忆蒸馏的兼容名称。

    provider/model 选择不再归此领域客户端所有；服务端 RoutePolicy 仍可为该场景
    选择 Codex 适配器，但客户端不能借兼容名称绕过路由、预算或审计边界。
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        timeout_ms: int = 120_000,
        render_target_lang: str = "zh",
        model_attempt_service: Any,
    ) -> None:
        del model_id
        self._timeout_ms = max(5_000, int(timeout_ms))
        self._render_target_lang = "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"
        self._model_attempt_service = model_attempt_service

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        snippets = [
            f"{index + 1}. {_truncate_text(text, limit=900)}"
            for index, text in enumerate(list(texts or [])[:6])
            if str(text or "").strip()
        ]
        if not snippets:
            raise ValueError("no texts to summarize")
        if self._render_target_lang == "ja":
            system_prompt = (
                "あなたは長期記憶の蒸留器です。証拠に基づき、後で再利用できる短い意味記憶だけを出力します。"
                "推測を事実にしないでください。"
            )
        else:
            system_prompt = (
                "你负责做长期记忆蒸馏。只根据给定 episode/evidence，抽象出后续可复用的语义记忆。"
                "不要把推测写成事实，不要保留低层日志细节，输出一句自然、准确的中文摘要。"
            )
        user_prompt = (
            f"查询主题：{_truncate_text(query_text, limit=160)}\n"
            f"最大长度：{max(64, min(int(max_tokens or 200), 400))} tokens 左右\n"
            "证据片段：\n"
            f"{chr(10).join(snippets)}\n\n"
            "请输出 JSON：{\"summary\":\"...\"}"
        )
        result = complete_compiled_prompt_sync(
            attempt_service=self._model_attempt_service,
            prompt_id=MEMORY_DISTILLATION_PROMPT_ID,
            request_id=new_request_id(),
            scene_type="MEMORY_DISTILLATION_BACKGROUND",
            cognitive_tier="B",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            deadline_ms=self._timeout_ms,
            foreground=False,
        )
        if not result.success:
            raise RuntimeError(result.error_code or "MODEL_ATTEMPT_FAILED")
        parsed = _parse_summary_json(result.content)
        summary = _truncate_text(parsed.get("summary"), limit=320)
        if not summary:
            raise ValueError("empty distillation summary")
        return summary


def resolve_memory_distillation_codex_model(model_id: str | None = None) -> str:
    return resolve_codex_cli_model(
        model_id
        or os.environ.get("HOST_MEMORY_DISTILLATION_CODEX_MODEL_ID")
        or os.environ.get("HOST_MEMORY_DISTILLATION_CODEX_MODEL")
        or DEFAULT_CODEX_DISTILLATION_MODEL
    )


def _parse_summary_json(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def build_default_llm_summary_client(
    *,
    render_target_lang: str = "zh",
    timeout_ms: int = 2600,
    model_attempt_service: Any,
) -> LlmSummaryClient:
    return PoolSummaryClient(
        scene_type="MEMORY_DISTILLATION",
        cognitive_tier="B",
        timeout_ms=timeout_ms,
        render_target_lang=render_target_lang,
        model_attempt_service=model_attempt_service,
    )


def build_codex_cli_summary_client(
    *,
    model_id: str | None = None,
    render_target_lang: str = "zh",
    timeout_ms: int = 120_000,
    model_attempt_service: Any,
) -> LlmSummaryClient:
    return CodexCliSummaryClient(
        model_id=model_id,
        timeout_ms=timeout_ms,
        render_target_lang=render_target_lang,
        model_attempt_service=model_attempt_service,
    )


@dataclass(frozen=True)
class SemanticMemoryRecord:
    """蒸馏生成的一条语义记忆投影，来源 ID 与哈希用于追溯而非替代原始证据。"""

    semantic_id: str
    summary: str
    source_memory_ids: list[str]
    source_hash: str
    distill_strategy: str
    confidence: float
    tags: list[str]
    ts_ms: int
    operator_node_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_id": self.semantic_id,
            "summary": self.summary,
            "source_memory_ids": list(self.source_memory_ids),
            "source_hash": self.source_hash,
            "distill_strategy": self.distill_strategy,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "ts_ms": self.ts_ms,
            "operator_node_id": self.operator_node_id,
        }


@dataclass(frozen=True)
class DistillResult:
    """蒸馏尝试的终态；失败或跳过不等于来源事实被删除。"""

    success: bool
    semantic_id: str | None
    source_count: int
    detail: str
    ts_ms: int
    skipped: bool = False


class MemoryDistillationEngine:
    """从可信生活事件蒸馏语义记忆，必要时兼容旧 Episodic 来源。

    选源、幂等检查与写入临界区在锁内协调，防止并发任务为同一批来源重复创建语义
    记录；Life Memory 可用时优先使用其纠正/保留状态作为更强的事实边界。
    """

    def __init__(
        self,
        *,
        episodic_store: EpisodicStore,
        ledger: SQLiteLedger,
        life_memory_store: LifeMemoryStore | None = None,
        prefer_life_episode: bool = True,
    ) -> None:
        """注入存储与账本端口；锁仅保护同进程幂等区，跨进程仍由持久化层裁决。"""
        self.episodic = episodic_store
        self.ledger = ledger
        self.life_memory_store = life_memory_store
        self.prefer_life_episode = bool(prefer_life_episode)
        # F-41: protect idempotency check + write critical section.
        self._distill_lock = threading.Lock()

    def distill_top_k(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        distill_strategy: str = "merge_top_k",
        tags: list[str] | None = None,
        operator_node_id: str = "distillation_engine",
        ts_ms: int | None = None,
        llm_client: LlmSummaryClient | None = None,
        require_llm: bool = False,
    ) -> DistillResult:
        distill_ts = int(ts_ms if ts_ms is not None else now_ms())
        top_k = min(max(1, top_k), 20)

        if self.prefer_life_episode and self.life_memory_store is not None:
            return self._distill_life_semantics(
                query_text=query_text,
                top_k=top_k,
                distill_strategy=distill_strategy,
                tags=list(tags or []),
                operator_node_id=operator_node_id,
                ts_ms=distill_ts,
                llm_client=llm_client,
                require_llm=bool(require_llm),
            )

        recall_k = min(max(top_k * 3, top_k), 20)
        recalled, source_layer = self._select_distillation_sources(
            query_text=query_text,
            recall_k=recall_k,
            top_k=top_k,
        )
        if not recalled:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="no trusted life episodes or episodic memories found for query",
                ts_ms=distill_ts,
            )

        source_ids = [r["memory_id"] for r in recalled]
        source_hash = _compute_source_hash(source_ids)

        existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
        if existing is not None:
            return DistillResult(
                success=True,
                semantic_id=existing["semantic_id"],
                source_count=len(source_ids),
                detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                ts_ms=distill_ts,
                skipped=True,
            )

        texts = [r["text"] for r in recalled]
        if require_llm and llm_client is None:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=len(source_ids),
                detail="llm distillation required but llm_client is unavailable",
                ts_ms=distill_ts,
            )
        if llm_client is not None:
            try:
                summary = llm_client.summarize(
                    texts,
                    query_text=query_text,
                    max_tokens=200,
                )
                distill_strategy = "life_episode_llm_summary" if source_layer == "life_episode" else "llm_summary"
            except Exception as exc:
                if require_llm:
                    return DistillResult(
                        success=False,
                        semantic_id=None,
                        source_count=len(source_ids),
                        detail=f"llm distillation failed: {type(exc).__name__}: {exc}",
                        ts_ms=distill_ts,
                    )
                summary = _merge_texts(texts, query_text=query_text)
        else:
            summary = _merge_texts(texts, query_text=query_text)
        if source_layer == "life_episode" and distill_strategy == "merge_top_k":
            distill_strategy = "life_episode_merge_top_k"

        semantic_id = f"sem_{uuid.uuid4().hex}"
        record = SemanticMemoryRecord(
            semantic_id=semantic_id,
            summary=summary,
            source_memory_ids=source_ids,
            source_hash=source_hash,
            distill_strategy=distill_strategy,
            confidence=_compute_confidence(recalled),
            tags=_merge_tags(list(tags or []), [f"source:{source_layer}"]),
            ts_ms=distill_ts,
            operator_node_id=operator_node_id,
        )

        with self._distill_lock:
            existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if existing is not None:
                return DistillResult(
                    success=True,
                    semantic_id=existing["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=distill_ts,
                    skipped=True,
                )

            self.ledger.log_semantic_memory(record.to_dict())
            persisted = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if persisted is None:
                return DistillResult(
                    success=False,
                    semantic_id=None,
                    source_count=len(source_ids),
                    detail="distillation write failed: semantic_memory missing after insert",
                    ts_ms=distill_ts,
                )
            if persisted["semantic_id"] != semantic_id:
                return DistillResult(
                    success=True,
                    semantic_id=persisted["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=distill_ts,
                    skipped=True,
                )

        try:
            self.ledger.log_system_event(
                {
                    "request_id": semantic_id,
                    "event_type": "MEMORY_DISTILLED",
                    "source": operator_node_id,
                    "detail": (
                        f"semantic_id={semantic_id} "
                        f"source_count={len(source_ids)} "
                        f"strategy={distill_strategy} "
                        f"confidence={record.confidence:.3f}"
                    ),
                    "ts_ms": distill_ts,
                }
            )
        except Exception:
            pass

        return DistillResult(
            success=True,
            semantic_id=semantic_id,
            source_count=len(source_ids),
            detail=f"distilled {len(source_ids)} {source_layer} memories into semantic_id={semantic_id}",
            ts_ms=distill_ts,
        )

    def _distill_life_semantics(
        self,
        *,
        query_text: str,
        top_k: int,
        distill_strategy: str,
        tags: list[str],
        operator_node_id: str,
        ts_ms: int,
        llm_client: LlmSummaryClient | None,
        require_llm: bool,
    ) -> DistillResult:
        if self.life_memory_store is None:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="life memory store unavailable",
                ts_ms=ts_ms,
            )
        llm_fallback_detail = ""
        if llm_client is not None:
            llm_result = self._distill_life_semantics_with_llm(
                query_text=query_text,
                top_k=top_k,
                distill_strategy=distill_strategy,
                tags=tags,
                operator_node_id=operator_node_id,
                ts_ms=ts_ms,
                llm_client=llm_client,
            )
            if llm_result.success or llm_result.skipped:
                return llm_result
            if require_llm:
                return llm_result
            llm_fallback_detail = llm_result.detail
        elif require_llm:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="life llm distillation required but llm_client is unavailable",
                ts_ms=ts_ms,
            )
        result = LifeMemoryConsolidator(store=self.life_memory_store).distill_semantic_memories(
            limit=max(top_k * 12, 80),
            max_records=1,
            query_text=query_text,
            ts_ms=ts_ms,
            operator_node_id=operator_node_id,
        )
        written = [item for item in list(result.get("written") or []) if isinstance(item, Mapping)]
        if written:
            item = written[0]
            semantic_id = str(item.get("semantic_id") or "").strip()
            source_count = int(item.get("source_count") or 0)
            return DistillResult(
                success=bool(semantic_id),
                semantic_id=semantic_id or None,
                source_count=source_count,
                detail=(
                    f"life consolidator wrote semantic_id={semantic_id} "
                    f"from {source_count} trusted episode/collection sources"
                    + (f"; llm_fallback={llm_fallback_detail}" if llm_fallback_detail else "")
                ),
                ts_ms=ts_ms,
                skipped=False,
            )
        skipped = [item for item in list(result.get("skipped") or []) if isinstance(item, Mapping)]
        existing = next(
            (
                item
                for item in skipped
                if str(item.get("reason_code") or "").strip() == "SEMANTIC_MEMORY_EXISTS"
                and str(item.get("semantic_id") or "").strip()
            ),
            None,
        )
        if existing is not None:
            semantic_id = str(existing.get("semantic_id") or "").strip()
            persisted = self.ledger.get_semantic_memory_by_id(semantic_id) if semantic_id else None
            source_count = len(list((persisted or {}).get("source_memory_ids") or []))
            return DistillResult(
                success=True,
                semantic_id=semantic_id,
                source_count=source_count,
                detail=f"skipped: life consolidated semantic memory already exists for query={query_text[:50]!r}",
                ts_ms=ts_ms,
                skipped=True,
            )
        return DistillResult(
            success=False,
            semantic_id=None,
            source_count=0,
            detail=(
                "life consolidator found no durable semantic proposal for query "
                f"{query_text[:80]!r}; legacy episodic distillation disabled while LifeMemoryStore is active"
                + (f"; llm_fallback={llm_fallback_detail}" if llm_fallback_detail else "")
            ),
            ts_ms=ts_ms,
        )

    def _distill_life_semantics_with_llm(
        self,
        *,
        query_text: str,
        top_k: int,
        distill_strategy: str,
        tags: list[str],
        operator_node_id: str,
        ts_ms: int,
        llm_client: LlmSummaryClient,
    ) -> DistillResult:
        recalled = self._recall_life_episode_llm_sources(
            query_text=query_text,
            top_k=top_k,
            recall_k=min(max(top_k * 4, top_k), 30),
        )
        if not recalled:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="life llm distillation found no trusted evidence sources",
                ts_ms=ts_ms,
            )
        source_ids = [r["memory_id"] for r in recalled]
        source_hash = _compute_source_hash(["life_llm:v1", *source_ids])
        existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
        if existing is not None:
            return DistillResult(
                success=True,
                semantic_id=existing["semantic_id"],
                source_count=len(source_ids),
                detail=f"skipped: life llm semantic memory already exists for source_hash={source_hash[:12]}",
                ts_ms=ts_ms,
                skipped=True,
            )

        try:
            summary = llm_client.summarize(
                [r["text"] for r in recalled],
                query_text=query_text,
                max_tokens=240,
            )
            summary = _truncate_text(summary, limit=900)
        except Exception as exc:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=len(source_ids),
                detail=f"life llm distillation failed: {type(exc).__name__}: {exc}",
                ts_ms=ts_ms,
            )
        if not summary:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=len(source_ids),
                detail="life llm distillation returned empty summary",
                ts_ms=ts_ms,
            )

        semantic_id = f"sem_{uuid.uuid4().hex}"
        strategy = str(distill_strategy or "").strip()
        if strategy in {"", "merge_top_k"}:
            strategy = "life_episode_llm_summary"
        record = SemanticMemoryRecord(
            semantic_id=semantic_id,
            summary=summary,
            source_memory_ids=source_ids,
            source_hash=source_hash,
            distill_strategy=strategy,
            confidence=_compute_confidence(recalled),
            tags=_merge_tags(list(tags or []), ["source:life_episode", "life_consolidated", "llm_distilled"]),
            ts_ms=ts_ms,
            operator_node_id=operator_node_id,
        )

        with self._distill_lock:
            existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if existing is not None:
                return DistillResult(
                    success=True,
                    semantic_id=existing["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: life llm semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=ts_ms,
                    skipped=True,
                )
            self.ledger.log_semantic_memory(record.to_dict())
            persisted = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if persisted is None:
                return DistillResult(
                    success=False,
                    semantic_id=None,
                    source_count=len(source_ids),
                    detail="life llm distillation write failed: semantic_memory missing after insert",
                    ts_ms=ts_ms,
                )
            if persisted["semantic_id"] != semantic_id:
                return DistillResult(
                    success=True,
                    semantic_id=persisted["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: life llm semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=ts_ms,
                    skipped=True,
                )

        try:
            self.ledger.log_system_event(
                {
                    "request_id": semantic_id,
                    "event_type": "MEMORY_DISTILLED",
                    "source": operator_node_id,
                    "detail": (
                        f"semantic_id={semantic_id} "
                        f"source_count={len(source_ids)} "
                        f"strategy={strategy} "
                        f"confidence={record.confidence:.3f}"
                    ),
                    "ts_ms": ts_ms,
                }
            )
        except Exception:
            pass

        return DistillResult(
            success=True,
            semantic_id=semantic_id,
            source_count=len(source_ids),
            detail=f"llm distilled {len(source_ids)} trusted life episodes into semantic_id={semantic_id}",
            ts_ms=ts_ms,
        )

    def _select_distillation_sources(
        self,
        *,
        query_text: str,
        recall_k: int,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], str]:
        if self.prefer_life_episode and self.life_memory_store is not None:
            life_rows = self._recall_life_episode_sources(query_text=query_text, top_k=top_k, recall_k=recall_k)
            if life_rows:
                return life_rows, "life_episode"
        legacy_rows = [
            row
            for row in self.episodic.recall(query_text=query_text, top_k=recall_k)
            if not _is_semantic_memory_projection(row)
        ][:top_k]
        return legacy_rows, "episodic"

    def _recall_life_episode_sources(
        self,
        *,
        query_text: str,
        top_k: int,
        recall_k: int,
    ) -> list[dict[str, Any]]:
        if self.life_memory_store is None:
            return []
        packet = self.life_memory_store.recall_episode_packet(
            query=query_text,
            top_k=recall_k,
        )
        candidates = list(packet.candidates or [])
        if not candidates and not str(query_text or "").strip():
            candidates = [
                {
                    "memory_id": item.get("episode_id"),
                    "text": item.get("summary"),
                    "score": item.get("memory_strength") or item.get("salience") or 0.0,
                    "metadata": {
                        "episode_id": item.get("episode_id"),
                        "source_ref": item.get("source_ref"),
                        "truth_state": item.get("truth_state"),
                        "retention_state": item.get("retention_state"),
                        "memory_strength": item.get("memory_strength"),
                    },
                }
                for item in LifeMemoryConsolidator(store=self.life_memory_store).build_semantic_candidates(limit=recall_k)
            ]
        rows: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        for item in candidates:
            meta = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), Mapping) else {}
            episode_id = str(meta.get("episode_id") or item.get("memory_id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not episode_id or not text:
                continue
            semantic_text = _sanitize_life_distillation_text(text=text, metadata=meta)
            if not semantic_text or semantic_text in seen_texts:
                continue
            seen_texts.add(semantic_text)
            rows.append(
                {
                    "memory_id": f"life_episode::{episode_id}",
                    "text": semantic_text,
                    "score": float(item.get("score") or meta.get("memory_strength") or meta.get("salience") or 0.0),
                    "metadata": {
                        **meta,
                        "source_table": "life_episode",
                        "memory_layer": "life_episode",
                        "episode_id": episode_id,
                    },
                }
            )
        return rows[:top_k]

    def _recall_life_episode_llm_sources(
        self,
        *,
        query_text: str,
        top_k: int,
        recall_k: int,
    ) -> list[dict[str, Any]]:
        if self.life_memory_store is None:
            return []
        packet = self.life_memory_store.recall_episode_packet(
            query=query_text,
            top_k=recall_k,
        )
        candidates = list(packet.candidates or [])
        if not candidates and not str(query_text or "").strip():
            candidates = [
                {
                    "memory_id": item.get("episode_id"),
                    "text": item.get("summary"),
                    "score": item.get("memory_strength") or item.get("salience") or 0.0,
                    "metadata": {
                        "episode_id": item.get("episode_id"),
                        "source_ref": item.get("source_ref"),
                        "truth_state": item.get("truth_state"),
                        "retention_state": item.get("retention_state"),
                        "memory_strength": item.get("memory_strength"),
                    },
                }
                for item in LifeMemoryConsolidator(store=self.life_memory_store).build_semantic_candidates(limit=recall_k)
            ]
        rows: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        for item in candidates:
            meta = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), Mapping) else {}
            episode_id = str(meta.get("episode_id") or item.get("memory_id") or "").strip()
            if not episode_id:
                continue
            source_text = _life_llm_source_text(
                fallback_text=str(item.get("text") or ""),
                metadata=meta,
            )
            if not source_text or source_text in seen_texts:
                continue
            seen_texts.add(source_text)
            rows.append(
                {
                    "memory_id": f"life_episode::{episode_id}",
                    "text": source_text,
                    "score": float(item.get("score") or meta.get("memory_strength") or meta.get("salience") or 0.0),
                    "metadata": {
                        **meta,
                        "source_table": "life_episode",
                        "memory_layer": "life_episode",
                        "episode_id": episode_id,
                    },
                }
            )
        return rows[:top_k]

    def list_semantic_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.ledger.list_semantic_memories(limit=limit, offset=offset)


def _compute_source_hash(source_ids: list[str]) -> str:
    sorted_ids = sorted(source_ids)
    payload = json.dumps(sorted_ids, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_tags(existing: list[str], additions: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *additions]:
        tag = str(item or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        merged.append(tag)
    return merged


def _is_semantic_memory_projection(row: Mapping[str, Any]) -> bool:
    memory_id = str(row.get("memory_id") or "").strip()
    metadata = row.get("metadata")
    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    source_table = str(meta.get("source_table") or "").strip()
    semantic_id = str(meta.get("semantic_id") or "").strip()
    return (
        memory_id.startswith("semantic::")
        or source_table == "semantic_memory"
        or bool(semantic_id)
    )


_LIFE_DISTILLATION_NOISE_ACTIONS = {
    "lightnovel_recommendation_delivery",
    "lightnovel_recommendation_deep_review",
    "qq_reply_sent",
    "wechat_reply_sent",
    "gateway_reply_sent",
    "channel_message_sent",
    "avatar_action",
    "emotion_decay_tick",
    "body_sense_tick",
    "inner_world_numeric_tick",
    "silence_tick",
    "health_check",
    "heartbeat",
}


_LIFE_DISTILLATION_NOISE_TOKENS = (
    "api_key",
    "apikey",
    "authorization:",
    "cookie",
    "token=",
    "secret",
    "密码",
    "密钥",
    "heartbeat",
    "health_check",
    "emotion_decay_tick",
    "body_sense_tick",
    "inner_world_numeric_tick",
    "silence_tick",
    "input_completed",
    "input_ignored",
    "input_rejected",
    "cortex_orchestrator",
    "surface_production_result_recall",
    "latency_ms",
    "decision=approve",
    "_thalamus_meta",
    "发送了text消息",
    "发送了image消息",
    "发送结果=sent",
    "网页深读结果",
    "正文内容足够支撑判断",
    "lightnovel_recommendation_delivery",
    "lightnovel_recommendation_deep_review",
    "lightnovel_gateway_delivery",
    "codexproductionjobagent_lightnovel_recommender",
    "轻小说推荐报告",
    "详情:https://www.lightnovel.fun",
    "详情：http://www.lightnovel.fun",
    "请根据下面的真实片段",
)


def _sanitize_life_distillation_text(*, text: str, metadata: Mapping[str, Any]) -> str | None:
    """生成可持久化的语义陈述；审计/噪声事件则返回 ``None``。

    该规则路径同时排除未批准的内部事件和无法归类的低层日志，避免“能被读取”被
    误解释为“可以成为长期记忆”。
    """

    action = _compact_text(metadata.get("action"))
    episode_meta = metadata.get("episode_metadata")
    if isinstance(episode_meta, Mapping):
        action = action or _compact_text(episode_meta.get("action_kind"))
        event_source = _compact_text(episode_meta.get("event_source"))
        should_distill = bool(episode_meta.get("should_distill_to_memory"))
    else:
        event_source = ""
        should_distill = False
    if action in _LIFE_DISTILLATION_NOISE_ACTIONS:
        return None
    inner_event = event_source in {"inner_world", "inner_stream", "inner_activity"} or _looks_like_private_inner_event(action)
    if inner_event and not should_distill:
        return None

    compact_whole = _compact_text(text)
    if _life_distillation_text_is_noise(compact_whole):
        return None

    statements: list[str] = []
    for raw_line in re.split(r"[\n\r]+", text):
        line = str(raw_line or "").strip()
        if not line:
            continue
        if re.match(r"^\s*(assistant|perception)\s*[:：]", line, flags=re.IGNORECASE):
            continue
        normalized = _normalize_life_semantic_statement(line)
        semantic = _semantic_statement_from_life_text(
            normalized,
            action=action,
            allow_user_preference=not inner_event,
        )
        if semantic and semantic not in statements:
            statements.append(semantic)

    if not statements:
        normalized = _normalize_life_semantic_statement(text)
        semantic = _semantic_statement_from_life_text(
            normalized,
            action=action,
            allow_user_preference=not inner_event,
        )
        if semantic:
            statements.append(semantic)

    if not statements:
        return None
    return "；".join(statements[:3])


def _life_llm_source_text(*, fallback_text: str, metadata: Mapping[str, Any]) -> str | None:
    """构造由证据支撑的 LLM 蒸馏来源文本。

    此门槛比确定性语义提取器稍宽：LLM 可从工具、感知或聊天证据抽象生活事件，
    但仍不可看到传输噪声、已抑制记忆或秘密，也不能越过内部事件的蒸馏许可。
    """

    action = _compact_text(metadata.get("action"))
    episode_meta = metadata.get("episode_metadata")
    if isinstance(episode_meta, Mapping):
        action = action or _compact_text(episode_meta.get("action_kind"))
        event_source = _compact_text(episode_meta.get("event_source"))
        should_distill = bool(episode_meta.get("should_distill_to_memory"))
    else:
        event_source = ""
        should_distill = False
    if action in _LIFE_DISTILLATION_NOISE_ACTIONS:
        return None
    if (event_source in {"inner_world", "inner_stream", "inner_activity"} or _looks_like_private_inner_event(action)) and not should_distill:
        return None
    truth_state = str(metadata.get("truth_state") or "").strip().upper()
    if truth_state in {"CORRECTED", "SUPPRESSED"}:
        return None
    retention_state = str(metadata.get("retention_state") or "").strip().upper()
    if retention_state == "GONE":
        return None

    evidence_rows = metadata.get("evidence")
    evidence_lines: list[str] = []
    if isinstance(evidence_rows, list):
        for ev in evidence_rows:
            if not isinstance(ev, Mapping):
                continue
            kind = str(ev.get("evidence_kind") or "").strip().lower()
            content = _truncate_text(ev.get("content"), limit=700)
            if not content:
                continue
            if kind in {
                "user_text",
                "gateway_message",
                "vision_observation",
                "audio_asr",
                "scheduler_event",
                "inner_thought",
                "tool_result",
                "reflection",
                "attachment",
            }:
                evidence_lines.append(f"{kind}: {content}")
            elif kind == "assistant_reply":
                evidence_lines.append(f"assistant_reply_as_action_evidence: {content}")

    if not evidence_lines:
        text = _truncate_text(fallback_text, limit=900)
        if text:
            evidence_lines.append(f"episode_summary: {text}")
    if not evidence_lines:
        return None

    source_ref = _truncate_text(metadata.get("source_ref") or metadata.get("evidence_refs_primary"), limit=160)
    header_parts = [
        f"episode_id={metadata.get('episode_id')}",
        f"episode_kind={metadata.get('episode_kind')}",
        f"action={metadata.get('action')}",
        f"result={metadata.get('result')}",
    ]
    if source_ref:
        header_parts.append(f"source_ref={source_ref}")
    source_text = "\n".join(
        [
            "；".join(part for part in header_parts if part and not part.endswith("=None")),
            *evidence_lines[:10],
        ]
    ).strip()
    compact = _compact_text(source_text)
    if _life_distillation_text_is_noise(compact):
        return None
    return _truncate_text(source_text, limit=1600)


def _normalize_life_semantic_statement(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(记忆线索|长期记忆|召回线索)\s*[:：]\s*", "", text).strip()
    text = re.sub(r"^行动记忆：[^；;]{1,120}[；;]\s*", "", text).strip()
    text = re.sub(r"^(记住|请记住|以后记住)[，,：:\s]*", "", text).strip()
    return text.strip("。；; \n\t")[:700]


def _semantic_statement_from_life_text(statement: str, *, action: str, allow_user_preference: bool = True) -> str | None:
    body = _life_semantic_body(statement)
    if len(body) < 4:
        return None
    compact = _compact_text(body)
    if _looks_like_private_inner_event(compact):
        return None
    if _life_distillation_text_is_noise(compact):
        return None
    if _life_preference_has_unresolved_pronoun(compact):
        return None
    if allow_user_preference and _is_life_user_preference(compact):
        return f"用户偏好：{body}"
    if _is_life_commitment(compact):
        return f"承诺和待办：{body}"
    if _is_life_self_change(compact, action=action):
        return f"自我改变：{body}"
    if _is_life_failure_lesson(compact, action=action):
        return f"故障经验：{body}"
    if _is_life_stable_fact(compact):
        return f"稳定事实：{body}"
    return None


def _looks_like_private_inner_event(value: Any) -> bool:
    compact = _compact_text(value)
    return any(
        token in compact
        for token in (
            "innerworld",
            "innerstream",
            "inneractivity",
            "innerworldmonologuetick",
            "innerstreamtick",
            "innerthought",
        )
    )


def _life_semantic_body(statement: str) -> str:
    text = str(statement or "").strip("。；; \n\t")
    text = re.sub(r"^(用户明确要求|用户要求|用户偏好|偏好)[：:\s]*", "", text).strip()
    if text.startswith("我喜欢"):
        text = "用户喜欢" + text.removeprefix("我喜欢")
    if text.startswith("我不喜欢"):
        text = "用户不喜欢" + text.removeprefix("我不喜欢")
    if text.startswith("不要") or text.startswith("别"):
        text = "用户要求" + text
    if text.startswith("主链"):
        text = "用户要求" + text
    return text[:360].rstrip("。；; ")


def _life_distillation_text_is_noise(compact_text: str) -> bool:
    return any(token in compact_text for token in _LIFE_DISTILLATION_NOISE_TOKENS)


def _life_preference_has_unresolved_pronoun(compact: str) -> bool:
    value = str(compact or "")
    if not any(token in value for token in ("这本", "那本", "这部", "那部", "这个", "那个", "它")):
        return False
    if not any(token in value for token in ("喜欢", "不喜欢", "不错", "好看", "对味", "讨厌")):
        return False
    return not any(token in value for token in ("《", "》", "http", "标题", "书名", "作品", "第1", "第2", "第3", "第一", "第二", "第三"))


def _is_life_user_preference(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "用户喜欢",
            "我喜欢",
            "用户不喜欢",
            "我不喜欢",
            "用户要求",
            "要求",
            "偏好",
            "以后",
            "不要",
            "别再",
            "优先使用",
            "推荐格式",
            "格式",
            "主链不要",
            "不要用pro",
            "不用pro",
            "分段",
            "卡片",
            "拟人化发送",
        )
    )


def _is_life_commitment(compact: str) -> bool:
    return any(token in compact for token in ("承诺", "待办", "todo", "openloop", "后续要", "下次继续", "未完成"))


def _is_life_self_change(compact: str, *, action: str) -> bool:
    if action in {"memory_compaction", "model_route_update", "capability_change"}:
        return True
    return any(token in compact for token in ("模型路由", "记忆系统", "能力被删除", "删除资源管理器", "策略被用户纠正", "自我改变", "主链迁移", "改成"))


def _is_life_failure_lesson(compact: str, *, action: str) -> bool:
    if "failed" in action or "failure" in action:
        return True
    return any(
        token in compact
        for token in (
            "失败原因",
            "失败",
            "连不上",
            "没起来",
            "状态库被占用",
            "被占用",
            "chroma",
            "neo4j",
            "deepseek失败",
            "gateway连不上",
            "服务没起来",
        )
    )


def _is_life_stable_fact(compact: str) -> bool:
    return any(token in compact for token in ("onebot在edge", "onebot在", "qq会话", "conversation_id", "session_id", "在edge", "不在本机"))


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _merge_texts(texts: list[str], *, query_text: str) -> str:
    header = f"[语义摘要 | 查询: {query_text[:50]}]"
    body = "\n---\n".join(f"- {t.strip()}" for t in texts if t.strip())
    return f"{header}\n{body}"


def _compute_confidence(recalled: list[dict[str, Any]]) -> float:
    if not recalled:
        return 0.0
    scores = [float(r.get("score", 0.0)) for r in recalled]
    avg = sum(scores) / len(scores)
    return round(min(max(avg, 0.0), 1.0), 4)
