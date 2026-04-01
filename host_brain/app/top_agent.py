"""top_agent.py — 顶级 Agent SSE 生成器入口（Phase XV）。

Phase XV 架构：
  Airlock → Governor → 检索 → StateArbiter → RecallPack
  → DispatchAgent（路由决策 + 子 Agent/Skill 调度）
  → ChatAgent（人格渲染 + LLM 流式 + COMMIT）

三层职责分离：
  top_agent  : 前置处理（净化/限速/检索/上下文装配），无 LLM 调用
  dispatch_agent : 路由决策（硬规则优先，LLM 兜底），轻量快速
  chat_agent : 人格渲染（完整 prompt + 流式 LLM + 反思链 + 净化）

向后兼容：
  _async_post_process、_SubAgentGatewayAdapter 等工具函数保留，
  被 dispatch_agent 和 chat_agent 依赖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, Mapping

from host_brain.bond.user_identity import resolve_person_id
from host_brain.app.sub_agent_governor import chat_lite_governor
from host_brain.app.sub_agent_protocol import (
    AGENT_CAPABILITIES,
    ALL_AGENT_IDS,
    SubAgentRequest,
    get_sub_agent_dispatcher,
)

_logger = logging.getLogger("Natsume.top_agent")

# 委托标记正则：LLM 输出中 [DELEGATE:agent_id:task] 或 JSON delegate 字段
_DELEGATE_RE = re.compile(
    r"\[DELEGATE:(\w+?)(?::(.+?))?\]",
    re.DOTALL,
)
_RECENT_TURN_USER_QUERY_RE = re.compile(r"(我刚才|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")
_RECENT_TURN_ASSISTANT_QUERY_RE = re.compile(r"(你刚才|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")
_RECENT_TURN_GENERIC_QUERY_RE = re.compile(r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么).*?(是什么|是什么来着|说了什么)?")
_DELEGATE_INTERNAL_MARKER_RE = re.compile(r"draft_only|intent_draft|ReAct|LLM gateway|agent_id=", re.IGNORECASE)

# 回复内容简单情感推断（从旧链路 chat_stream_handler 归并）
_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起"),
    ("ANGRY", "生气|气死|烦"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你"),
    ("SURPRISED", "哇|真的吗|竟然"),
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_input(message: str) -> tuple[str, bool]:
    """入口净化：null byte + 空消息检测。返回 (sanitized, passed)。"""
    sanitized = message.replace("\x00", "").strip()
    return sanitized, bool(sanitized)


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 安全词 + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _extract_delegate_visible_text(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    try:
        from host_brain.app.intelligent_runtime import _normalize_narrative_payload  # noqa: PLC0415

        normalized = _normalize_narrative_payload(normalized)
    except Exception:
        pass
    return str(normalized or "").strip()


def _render_delegated_result_naturally(*, agent_id: str, result_text: str, reply_template: str | None) -> str:
    visible = _extract_delegate_visible_text(result_text)
    if not visible or _DELEGATE_INTERNAL_MARKER_RE.search(visible):
        if agent_id == "code_agent":
            return "我先帮你整理个更自然一点的大致思路吧。不过刚才那条代码侧结果不稳定，这次我不直接把内部草稿发给你。"
        return "我先帮你把刚才那个结果整理成正常说话的版本，不过上一条子流程返回得不太稳定。"

    if reply_template and "{agent_result}" in reply_template:
        rendered = reply_template.replace("{agent_result}", visible).strip()
        if rendered:
            return rendered
    if agent_id == "code_agent":
        return f"我先帮你整理一个大致思路：{visible}".strip()
    return visible


class _SubAgentGatewayAdapter:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        base_url_override: str | None,
    ) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        from host_brain.app.prompt_builder import extract_langchain_content_text  # noqa: PLC0415

        self._human_message = HumanMessage
        self._system_message = SystemMessage
        self._ai_message = AIMessage
        self._extract_text = extract_langchain_content_text
        self._llm = ChatOpenAI(
            model=str(model_id or "").strip() or "default",
            api_key=api_key,
            base_url=(str(base_url_override or "").strip() or None),
            timeout=max(1.0, float(timeout_ms) / 1000.0),
            max_retries=1,
            temperature=0.3,
        )
        self.provider_name = str(provider_name or "").strip().lower()

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        lc_messages: list[Any] = []
        for row in list(messages or []):
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "user").strip().lower()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                lc_messages.append(self._system_message(content=content))
            elif role == "assistant":
                lc_messages.append(self._ai_message(content=content))
            else:
                lc_messages.append(self._human_message(content=content))
        response = self._llm.invoke(lc_messages)
        return {"content": self._extract_text(getattr(response, "content", ""))}


def _build_subagent_gateway_from_runtime(llm_runtime: Mapping[str, Any]) -> Any | None:
    try:
        if not bool(llm_runtime.get("available")):
            return None
        provider_name = str(llm_runtime.get("provider_name") or "").strip()
        model_id = str(llm_runtime.get("model_id") or "").strip()
        api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
        if not provider_name or not model_id:
            return None
        return _SubAgentGatewayAdapter(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
        )
    except Exception as exc:
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None


def _detect_recent_turn_query_role(user_message: str) -> str | None:
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    if _RECENT_TURN_USER_QUERY_RE.search(normalized):
        return "user"
    if _RECENT_TURN_ASSISTANT_QUERY_RE.search(normalized):
        return "assistant"
    if _RECENT_TURN_GENERIC_QUERY_RE.search(normalized):
        return "both"
    return None


def _maybe_build_recent_turn_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    query_role = _detect_recent_turn_query_role(user_message)
    if query_role is None:
        return None

    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.top_agent_prompt import _fetch_continuity_rows, _voice_rows_to_turns  # noqa: PLC0415

        rows = _fetch_continuity_rows(
            ledger=get_ledger(),
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            fetch_limit=24,
        )
        turns = _voice_rows_to_turns(rows=rows, render_target_lang=render_target_lang)
    except Exception as exc:
        _logger.debug("[%s] recent turn continuity lookup failed: %s", request_id, exc)
        return None

    if not turns:
        return None

    latest_user = next((str(turn.get("content") or "").strip() for turn in reversed(turns) if str(turn.get("role") or "").strip().lower() == "user" and str(turn.get("content") or "").strip()), "")
    latest_assistant = next((str(turn.get("content") or "").strip() for turn in reversed(turns) if str(turn.get("role") or "").strip().lower() == "assistant" and str(turn.get("content") or "").strip()), "")

    if query_role == "user" and latest_user:
        return f"你刚才发的是：「{latest_user}」"
    if query_role == "assistant" and latest_assistant:
        return f"我刚才回的是：「{latest_assistant}」"
    if query_role == "both":
        parts: list[str] = []
        if latest_user:
            parts.append(f"你刚才发的是：「{latest_user}」")
        if latest_assistant:
            parts.append(f"我刚才回的是：「{latest_assistant}」")
        if parts:
            return "；".join(parts)
    return None


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案推断 emotion_tag（从旧链路归并）。"""
    import re as _re
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _should_buffer_stream_output(
    *,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
) -> bool:
    """高风险对话缓冲判断（从旧链路 chat_stream_handler 归并）。

    当 ReflectionChain 判定该轮为高风险时，缓冲全部输出，
    等 apply_response_intelligence 改写后再一次性推送。
    """
    if not _parse_bool_env("HOST_STREAM_REFLECTION_BUFFER_ENABLED", default=True):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415
        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception:
        return False


def _extract_delegate_from_llm_output(
    full_text: str,
) -> tuple[str | None, str | None, str | None]:
    """从 LLM 输出中解析委托标记。

    支持两种格式：
    1. [DELEGATE:code_agent:写快排算法]
    2. JSON: {"delegate": {"agent": "code_agent", "task": "..."}, "reply_template": "..."}

    Returns:
        (agent_id, task, reply_template) — 任一为 None 表示非委托
    """
    # 方式 1：正则标记
    m = _DELEGATE_RE.search(full_text)
    if m:
        agent_id = m.group(1).strip()
        task = (m.group(2) or "").strip()
        before = full_text[: m.start()].strip()
        after = full_text[m.end():].strip()
        template = f"{before}\n{{agent_result}}\n{after}".strip()
        if agent_id in ALL_AGENT_IDS:
            return agent_id, task, template

    # 方式 2：JSON delegate
    try:
        json_text = full_text.strip()
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            json_text = "\n".join(json_lines)

        parsed = json.loads(json_text)
        if isinstance(parsed, dict):
            delegate = parsed.get("delegate")
            if isinstance(delegate, dict):
                agent_id = str(delegate.get("agent") or "").strip()
                task = str(delegate.get("task") or "").strip()
                template = str(parsed.get("reply_template") or "").strip()
                if agent_id in ALL_AGENT_IDS and task:
                    return agent_id, task, template or None
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return None, None, None


def _extract_visible_text(raw: str) -> str:
    """从 LLM 原始输出中提取文字通道文本（Phase XV P6: 取 text_zh）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    # Phase XV P6：双轨语言，文字通道取 text_zh
                    return str(narrative.get("text_zh") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text


# ---------------------------------------------------------------------------
# 顶级 Agent SSE 生成器
# ---------------------------------------------------------------------------

async def top_agent_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any],
    emotion_context: Dict[str, Any],
    emotion_snapshot: Dict[str, Any],
    governor_result: Dict[str, Any],
    intent_id: str | None,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    """顶级 Agent SSE 生成器（Phase XIV v4 核心）。

    流程：
      Airlock → 轻量 Governor → RecallFusion 五路检索 + StateArbiter(bond only)
      → RecallPack 槽位预算 → prompt 构建（完整管线含 build_prompt_intelligence）
      → LLM 流式 + SentenceSplitter
      → apply_response_intelligence（反思链 + 多模态后缀）
      → Airlock + 安全分类 → COMMIT → 异步后处理

    去重策略（Phase XIV v4）：
      - world_canon / episodic_vector：仅走 RecallFusion（语义检索），不走 StateArbiter（避免同源重复）
      - style_canon：仅走 style_pack 常驻注入（prompt #5 位），不走 StateArbiter / RecallFusion
      - bond_graph：StateArbiter 提供结构化快照（stage/score），RecallFusion 提供历史事件（互补保留）
    """
    user_message = str(request_body.message or "").strip()
    thread_id = getattr(request_body, "thread_id", None)
    person_id = resolve_person_id(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )

    # ── 1. Airlock 入口净化 ──────────────────────────────────────────
    sanitized_message, airlock_passed = _airlock_sanitize_input(user_message)
    if not airlock_passed:
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "AIRLOCK_EMPTY_MESSAGE", "request_id": request_id,
        })
        return

    # ── 1.5 PII 入口脱敏 ─────────────────────────────────────────────
    _pii_ingress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("INGRESS"):
            _pii_result = pii_scrub_text(sanitized_message, context="ingress")
            sanitized_message = _pii_result.scrubbed_text
            if _pii_result.has_pii:
                _pii_ingress_stats = {
                    "detections": _pii_result.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_result.detections}),
                }
    except Exception:
        pass  # 降级：PII 过滤不影响主流程

    yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    # ── 2. 极轻量 Governor（速率限制 + sys_mode）──────────────────────
    gov = chat_lite_governor(session_id=session_id, request_id=request_id)
    if gov["decision"] != "APPROVE":
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": gov.get("reason_code", "REJECTED"),
            "request_id": request_id,
        })
        return

    recent_turn_answer = _maybe_build_recent_turn_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang="zh",
    )
    if recent_turn_answer:
        narrative = _airlock_sanitize_output(recent_turn_answer)
        total_latency_ms = int(time.time() * 1000) - ts_ms

        # ── 同步写入 INPUT_COMPLETED（确保下一轮对话可查历史）────────
        try:
            from host_brain.app.dependencies import get_ledger as _get_pre_ledger  # noqa: PLC0415
            _get_pre_ledger().log_voice_event({
                "request_id": request_id,
                "session_id": session_id,
                "event_type": "INPUT_COMPLETED",
                "direction": "HOST_INTERNAL",
                "status": "OK" if narrative.strip() else "DEGRADED",
                "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
                "stage": "top_agent.pre_commit",
                "detail": f"source=CHAT_API text_len={len(sanitized_message)} reply_len={len(narrative)}",
                "payload": {
                    "source": "CHAT_API",
                    "person_id": person_id,
                    "input_preview": _build_reply_preview(sanitized_message, limit=160),
                    "reply_preview": _build_reply_preview(narrative, limit=160),
                    "render_target_lang": "zh",
                },
                "ts_ms": int(time.time() * 1000),
            })
        except Exception as _ve_exc:
            _logger.debug("[%s] pre-commit INPUT_COMPLETED failed: %s", request_id, _ve_exc)

        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "request_id": request_id,
            "audit_refs": {
                "request_id": request_id,
                "path": "chat_recent_turn_direct",
                "latency_ms": total_latency_ms,
                "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            },
        })
        asyncio.create_task(
            _async_post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang="zh",
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "RECENT_TURN_DIRECT"},
                gov_decision=gov,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
            )
        )
        return

    # ── 3. 并行启动检索 ──────────────────────────────────────────────
    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    # Phase XV P4：Gateway 通道已注入 render_target_lang="zh"（永远中文）
    # 其他通道回退到 infer_render_target_lang() 根据输入语言推断
    _injected_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower()
    render_target_lang = _injected_lang if _injected_lang in ("zh", "ja") else "ja"

    try:
        from host_brain.app.chat_retrieval_helpers import _run_retrieval  # noqa: PLC0415
        from host_brain.app.prompt_builder import infer_render_target_lang  # noqa: PLC0415

        # 只在未注入 render_target_lang 时才推断（Gateway 通道已注入 "zh"）
        if not _injected_lang:
            render_target_lang = infer_render_target_lang(user_message=sanitized_message)
        retrieval_task = asyncio.create_task(
            _run_retrieval(
                request_id=request_id,
                message=sanitized_message,
                person_id=person_id,
                session_id=session_id,
                ts_ms=ts_ms,
                emotion_tag=str(emotion_context.get("emotion_tag") or "") or None,
                render_target_lang=render_target_lang,
            )
        )
    except Exception as _retr_exc:
        _logger.debug("[%s] top_agent retrieval init failed: %s", request_id, _retr_exc)

    # ── 4. 等检索结果 + StateArbiter + RecallPack ────────────────────
    recall_slot_text: dict[str, str] = {}
    bond_summary: dict[str, Any] = {}

    if retrieval_task is not None:
        try:
            retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                retrieval_task, timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception) as _retr_exc:
            retrieval_summary = {"status": "DEGRADED", "error": str(_retr_exc)[:100]}
            _logger.debug("[%s] top_agent retrieval failed/timeout: %s", request_id, _retr_exc)

    # StateArbiter 精简查询（Phase XIV v4 去重）
    # ────────────────────────────────────────────────────────────────────
    # 旧链路做了四路并行查询：world_canon / style_canon / episodic / bond
    # 但 world_canon 和 episodic 已经通过 RecallFusion 五路召回覆盖（同源），
    # style_canon 通过 style_pack 常驻注入覆盖（同源 style_profile_v5.json），
    # 保留它们只会在 prompt 中产生重复内容浪费 token。
    #
    # 只保留 bond_graph：它提供的是当前关系阶段/分数的结构化快照，
    # 与 RecallFusion 的 neo4j_bond（历史关系事件语义检索）是互补的。
    # ────────────────────────────────────────────────────────────────────
    try:
        from host_brain.app.state_arbiter_queries import (  # noqa: PLC0415
            _resolve_state_arbiter_bond_fact,
        )
        bond_summary_raw, bond_evidence = await _resolve_state_arbiter_bond_fact(
            session_id=session_id, request_id=request_id,
        )
        if bond_evidence is not None:
            retrieval_evidence_items.append(bond_evidence)
        bond_stage = str(bond_summary_raw.get("bond_stage") or "").strip()
        if bond_stage:
            bond_summary = {
                "stage": bond_stage,
                "trend": str(bond_summary_raw.get("trend") or ""),
                "capability_hint": str(bond_summary_raw.get("capability_hint") or ""),
            }
    except Exception as _sa_exc:
        _logger.debug("[%s] top_agent state_arbiter failed: %s", request_id, _sa_exc)

    # RecallPack 槽位预算分配
    try:
        from host_brain.app.dependencies import get_recall_pack_builder  # noqa: PLC0415
        cpb = get_recall_pack_builder()
        if retrieval_evidence_items:
            _recall_results: dict[str, str] = {}
            for item in retrieval_evidence_items:
                slot = str(item.get("kind") or item.get("source_type") or "retrieval").lower()
                content = str(item.get("content") or item.get("snippet") or "")
                if content:
                    _recall_results[slot] = (_recall_results.get(slot, "") + "\n" + content).strip()
            if _recall_results:
                from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415
                pack = cpb.build(_recall_results, task_type="chat")
                for slot_name in RECALL_SLOT_PRIORITY:
                    slot = pack.get_slot(slot_name)
                    if slot and str(getattr(slot, "content", "") or "").strip():
                        recall_slot_text[slot_name] = str(slot.content).strip()
    except Exception as _cpb_exc:
        _logger.debug("[%s] top_agent recall_pack failed: %s", request_id, _cpb_exc)

    # ── 5. 构建 prompt 前的 LLM 运行时解析（供 DispatchAgent + ChatAgent 共用）──
    from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415
    llm_runtime = await resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )

    if not llm_runtime.get("available"):
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
            "narrative": "服务暂时不可用，请稍后再试。",
        })
        return

    # ── 6. 交给 DispatchAgent（Phase XV 核心）────────────────────────
    # DispatchAgent 负责路由决策，ChatAgent 负责人格渲染，
    # top_agent 只做前置处理（净化/限速/检索/上下文装配）。
    from host_brain.app.dispatch_agent import dispatch_agent_sse_generator  # noqa: PLC0415

    async for event in dispatch_agent_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=gov,
        intent_id=intent_id,
        metrics=metrics,
        # 预处理数据
        person_id=person_id,
        sanitized_message=sanitized_message,
        recall_slot_text=recall_slot_text,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=_pii_ingress_stats,
    ):
        yield event


# ---------------------------------------------------------------------------
# 异步后处理
# ---------------------------------------------------------------------------

async def _async_post_process(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    render_target_lang: str,
    # 链路追踪审计数据
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    # Phase XV trace 增强：LLM 输入摘要 + 召回证据
    llm_input_digest: str | None = None,       # system_prompt 前 500 字
    retrieval_evidence_items: list[dict[str, Any]] | None = None,  # 召回文档片段
) -> None:
    """异步后处理：审计写入 + Airlock 安全分类 + 记忆写入 + Bond 更新 + VTS + TTS。

    fire-and-forget，不阻塞主回复流。
    """
    # ── 链路追踪审计持久化 ────────────────────────────────────────────
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
        _now = int(time.time() * 1000)

        # (1) governor_log — chat_lite_governor 决策
        if gov_decision:
            ledger.log_governor({
                "request_id": request_id,
                "intent_id": request_id,
                "session_id": session_id,
                "decision": gov_decision.get("decision", "APPROVE"),
                "reason_code": gov_decision.get("reason_code"),
                "rule_trace": json.dumps(gov_decision.get("rule_trace", []), ensure_ascii=False, default=str),
                "latency_ms": gov_decision.get("latency_ms", 0),
                "ts_ms": ts_ms,
            })

        # (2) llm_node_call_log — LLM 流式调用记录
        ledger.log_llm_node_call({
            "request_id": request_id,
            "node_id": "top_agent_llm_stream",
            "provider": provider_name,
            "model_id": model_id,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "LLM_EMPTY_OUTPUT",
            "latency_ms": llm_latency_ms,
            # Phase XV trace 增强：LLM 输入摘要（system_prompt 前 500 字）
            "input_digest": (llm_input_digest or "")[:500] or None,
            # output_digest = LLM 回复摘要（前 400 字），供 WebUI 节点详情显示
            "output_digest": (narrative or "")[:400] or None,
            "ts_ms": _now,
        })

        # (2.5) retrieval_event_log — 召回结果落盘（Phase XV trace 增强）
        # 只在有实际召回证据时写入，避免空行污染
        _evidence_items = list(retrieval_evidence_items or [])
        _rs = retrieval_summary or {}
        if _evidence_items or _rs.get("status") == "OK":
            _query_plan = _rs.get("query_plan") or {}
            _topk_snippets = [
                {
                    "evidence_ref": str(item.get("evidence_ref") or ""),
                    "store_id": str(item.get("store_id") or ""),
                    "snippet": str(item.get("snippet") or "")[:200],
                    "score": item.get("score"),
                }
                for item in _evidence_items[:8]  # 最多记 8 条，控制体积
            ]
            ledger.log_retrieval_event({
                "request_id": request_id,
                "intent_id": request_id,
                "op_kind": "PIPELINE",
                "status": _rs.get("status", "OK"),
                "reason_code": _rs.get("reason_code"),
                "stage": "top_agent._async_post_process",
                "payload": {
                    "candidate_count": _rs.get("candidate_count", 0),
                    "route_count": _rs.get("route_count", 0),
                    "rerank_topk": _rs.get("rerank_topk", 0),
                    "degraded": bool(_rs.get("degraded", False)),
                    "query_plan": _query_plan,
                    "topk": _topk_snippets,
                    "agentic_meta": _rs.get("agentic_meta"),
                },
                "detail": (
                    f"candidate={_rs.get('candidate_count', 0)} "
                    f"topk={len(_topk_snippets)} "
                    f"intent={_query_plan.get('intent', 'unknown')}"
                ),
                "ts_ms": _now,
            })

        # (3) graph_run_trace — 端到端链路汇总
        _path = "delegate" if delegated_agents else "chat_direct"
        _retrieval_status = (retrieval_summary or {}).get("status", "SKIPPED")
        _retrieval_count = (retrieval_summary or {}).get("candidate_count", 0)
        _retrieval_output = {
            "candidate_count": _retrieval_count,
            "status": _retrieval_status,
            "reason_code": (retrieval_summary or {}).get("reason_code"),
            "error": (retrieval_summary or {}).get("error"),
            "route_count": (retrieval_summary or {}).get("route_count"),
            "rerank_topk": (retrieval_summary or {}).get("rerank_topk"),
            "degraded": (retrieval_summary or {}).get("degraded"),
            "target_stores": (retrieval_summary or {}).get("target_stores"),
            "query_plan": (retrieval_summary or {}).get("query_plan"),
            "agentic_meta": (retrieval_summary or {}).get("agentic_meta"),
        }

        # 构建 nodes_json：记录每个处理阶段的耗时和状态
        trace_nodes: list[dict[str, Any]] = [
            {"node_id": "airlock_ingress", "node_type": "governor", "display_name": "Airlock 入口",
             "side": "host", "status": "OK", "ts_ms": ts_ms},
            {"node_id": "governor", "node_type": "governor", "display_name": "Governor",
             "side": "host", "status": gov_decision.get("decision", "APPROVE") if gov_decision else "OK",
             "latency_ms": gov_decision.get("latency_ms", 0) if gov_decision else 0, "ts_ms": ts_ms},
            {"node_id": "recall_fusion", "node_type": "recall", "display_name": "Recall Fusion",
             "side": "host", "status": _retrieval_status,
             "output_data": _retrieval_output, "ts_ms": ts_ms},
            {"node_id": "prompt_builder", "node_type": "system", "display_name": "Prompt Builder",
             "side": "host", "status": "OK", "ts_ms": ts_ms},
            {
                "node_id": "llm_stream", "node_type": "llm", "display_name": "LLM 流式生成",
                "side": "host",
                "status": "OK" if narrative.strip() else "DEGRADED",
                "latency_ms": llm_latency_ms,
                "provider": provider_name,
                "model_id": model_id,
                "input_data": {
                    "first_token_ms": first_token_ms,
                    "buffer_stream": buffer_stream,
                    # Phase XV：system_prompt 摘要（前 300 字），供 WebUI Input 面板显示
                    "input_digest": (llm_input_digest or "")[:300] or None,
                },
                # Phase XV：写入 narrative 供 WebUI trace Output 面板显示（截断至 800 字）
                "output_data": {
                    "final_response": (narrative or "")[:800] or None,
                },
                "ts_ms": ts_ms,
            },
        ]
        # 子 Agent 委托阶段
        for da in (delegated_agents or []):
            _agent_id = da.get("agent_id", "?")
            trace_nodes.append({
                "node_id": f"sub_agent:{_agent_id}",
                "node_type": "agent",
                "display_name": f"Sub Agent: {_agent_id}",
                "side": "host",
                "status": "OK" if da.get("success") else "FAILED",
                "latency_ms": da.get("latency_ms", 0),
                "error": da.get("error"),
                "output_data": da.get("audit_refs"),
                "ts_ms": ts_ms,
            })
        trace_nodes.append({
            "node_id": "airlock_egress", "node_type": "governor", "display_name": "Airlock 出口",
            "side": "host", "status": "OK", "ts_ms": _now,
        })

        # PII 统计（如果有）
        _pii_detail = {}
        if pii_ingress_stats:
            _pii_detail["ingress"] = pii_ingress_stats
        if pii_egress_stats:
            _pii_detail["egress"] = pii_egress_stats
        if _pii_detail:
            trace_nodes.append({
                "node_id": "pii_filter", "node_type": "governor", "display_name": "PII 过滤",
                "side": "host", "status": "OK",
                "output_data": _pii_detail, "ts_ms": _now,
            })

        trace_edges = [
            {"source": trace_nodes[i]["node_id"], "target": trace_nodes[i + 1]["node_id"]}
            for i in range(len(trace_nodes) - 1)
        ]

        ledger.log_graph_run_trace({
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None,
            "node_count": len(trace_nodes),
            "nodes_json": trace_nodes,
            "edges_json": trace_edges,
            "ts_ms": _now,
        })

        # NOTE: INPUT_COMPLETED 已在 COMMIT SSE 之前同步写入（pre_commit 阶段），
        # 此处不再重复写入，避免 voice_event_log 出现重复条目。

    except Exception as exc:
        _logger.debug("[%s] top_agent post_process audit persist failed: %s", request_id, exc)

    # ── Airlock 出口安全分类 ──────────────────────────────────────────
    try:
        from host_brain.airlock.quarantine import classify_ingress  # noqa: PLC0415
        await asyncio.to_thread(classify_ingress, narrative)
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process airlock classify failed: %s", request_id, exc)

    # ── 记忆写入 ─────────────────────────────────────────────────────
    try:
        from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

        store = get_episodic_store()
        if store is not None:
            # PII 记忆写入脱敏（防止 PII 持久化到向量数据库）
            _mem_user = user_message
            _mem_narrative = narrative
            try:
                from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled  # noqa: PLC0415
                if _check_channel("MEMORY"):
                    _mem_user = pii_scrub_if_enabled(user_message, context="memory_write_user")
                    _mem_narrative = pii_scrub_if_enabled(narrative, context="memory_write_ai")
            except Exception:
                pass  # 降级：PII 过滤不影响记忆写入

            await asyncio.to_thread(
                persist_chat_episodic_memory,
                request_id=request_id,
                intent_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=_mem_user,
                assistant_reply=_mem_narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=store,
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process episodic failed: %s", request_id, exc)

    # ── Bond 更新 ────────────────────────────────────────────────────
    try:
        from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415
        from host_brain.app.dependencies import get_ledger, get_bond_stage_engine, get_bond_update_queue  # noqa: PLC0415
        from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415

        etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
        intensity = float(emotion_context.get("emotion_intensity") or 0.5)
        update_kind = BondDynamicsWriter.classify_interaction(
            text=user_message, emotion_intensity=intensity,
        )
        bond_payload = {
            "request_id": request_id,
            "intent_id": request_id,
            "session_id": session_id,
            "seq": 1,
            "user_id": resolve_bond_user_id(session_id=session_id),
            "topic_id": user_message[:64],
            "capability_hint": "bond_memory",
            "update_kind": update_kind,
            "evidence_refs": [request_id],
            "emotion_tag": etag,
            "emotion_intensity": intensity,
            "ts_ms": ts_ms,
        }
        queue = get_bond_update_queue()
        result = queue.enqueue(bond_payload)
        if str(result.get("status") or "") != "QUEUED":
            writer = BondDynamicsWriter(
                ledger=get_ledger(), stage_engine=get_bond_stage_engine(),
            )
            await asyncio.to_thread(writer.apply_update, **bond_payload)
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process bond failed: %s", request_id, exc)

    # ── VTS 情感驱动 ─────────────────────────────────────────────────
    try:
        from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion  # noqa: PLC0415
        await _drive_stream_vts_emotion(
            request_id=request_id, emotion_context=emotion_context, ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process vts failed: %s", request_id, exc)

    # ── TTS 语音播报 ─────────────────────────────────────────────────
    try:
        tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
        # Phase XV P7：分句流式 TTS 已在流式阶段逐句触发，跳过整段 TTS（避免重复播放）
        tts_sentence_stream = str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        _skip_bulk_tts = tts_sentence_stream in ("1", "true", "yes", "on")
        if tts_enabled in ("1", "true", "yes", "on") and narrative.strip() and not _skip_bulk_tts:
            from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
            from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

            base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
            tts_route = resolve_tts_route(
                text=narrative,
                emotion_context={**dict(emotion_context), "emotion_tag": base_etag},
                bond_summary=bond_summary or None,
            )
            etag = str(tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, base_etag)).strip()
            _intensity_raw = emotion_context.get("emotion_intensity")
            _intensity: float | None = None
            if _intensity_raw is not None:
                try:
                    _intensity = max(0.0, min(1.0, float(_intensity_raw)))
                except (TypeError, ValueError):
                    pass
            _tts_started = int(time.time() * 1000)
            _tts_result = await post_speak(
                text=narrative,
                request_id=request_id,
                session_id=session_id,
                speed=float(tts_route.get("speed") or 1.0),
                pitch=float(tts_route.get("pitch") or 0.0),
                emotion_tag=etag,
                emotion_intensity=_intensity,
                style_note=str(tts_route.get("style_note") or ""),
                lang_hint=str(tts_route.get("lang_hint") or render_target_lang),
            )

            # TTS 出站审计写入 voice_event_log
            try:
                from host_brain.app.dependencies import get_ledger as _get_tts_ledger  # noqa: PLC0415
                _get_tts_ledger().log_voice_event({
                    "request_id": request_id,
                    "session_id": session_id,
                    "event_type": "TTS_SPEAK",
                    "direction": "EGRESS",
                    "status": str(getattr(_tts_result, "status", "OK")),
                    "latency_ms": int(getattr(_tts_result, "latency_ms", 0)),
                    "payload": {
                        "text_len": len(narrative),
                        "emotion_tag": etag,
                        "speed": float(tts_route.get("speed") or 1.0),
                        "pitch": float(tts_route.get("pitch") or 0.0),
                        "lang_hint": str(tts_route.get("lang_hint") or render_target_lang),
                    },
                    "ts_ms": int(time.time() * 1000),
                })
            except Exception:
                pass  # 审计不影响 TTS 流程
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process tts failed: %s", request_id, exc)
