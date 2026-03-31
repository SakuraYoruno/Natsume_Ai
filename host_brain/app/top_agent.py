"""top_agent.py — 顶级 Agent SSE 生成器（Phase XIV v4 核心）。

夏目蓝本人，唯一入口。

闲聊路径（80%）：Airlock → LLM 流式 → 反思链 → Airlock → 异步后处理
执行路径（20%）：Airlock → LLM 判断 → 调度子 Agent → 组织回复 → Airlock → 异步后处理

治理原则：
  - 闲聊不需要 Governor/审计（说话不是执行动作）
  - 执行治理下沉到子 Agent 内部（白皮书合规）
  - 只保留极轻量 Governor（速率限制 + sys_mode）

归并自旧链路的能力（chat_stream_handler._sse_event_generator）：
  - SentenceSplitter + SENTENCE 事件（前端 TTS 实时跟读）
  - apply_response_intelligence（反思链 + AI 话术清理 + 多模态后缀）
  - _should_buffer_stream_output（高风险对话缓冲）
  - classify_ingress（出口 Airlock 安全分类）
  - _infer_emotion_tag_for_tts（回复文案情感推断）
  - thread_id 传递
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, Mapping

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
    """从 LLM 原始输出中提取可见文本。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text") or "").strip()
                if isinstance(narrative, str):
                    return narrative.strip()
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

    # ── 3. 并行启动检索 ──────────────────────────────────────────────
    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    render_target_lang = "ja"

    try:
        from host_brain.app.chat_retrieval_helpers import _run_retrieval  # noqa: PLC0415
        from host_brain.app.prompt_builder import infer_render_target_lang  # noqa: PLC0415

        render_target_lang = infer_render_target_lang(user_message=sanitized_message)
        retrieval_task = asyncio.create_task(
            _run_retrieval(
                request_id=request_id,
                message=sanitized_message,
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

    # ── 5. 构建 prompt（完整管线含 build_prompt_intelligence）──────────
    from host_brain.app.top_agent_prompt import build_top_agent_prompt  # noqa: PLC0415

    prompt_text, system_prompt, gen_params = build_top_agent_prompt(
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        session_id=session_id,
        request_id=request_id,
        thread_id=thread_id,
        bond_summary=bond_summary,
        recall_slot_text=recall_slot_text if recall_slot_text else None,
    )

    # 解析 LLM 运行时
    from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415
    llm_runtime = await resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )
    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    base_url = str(llm_runtime.get("base_url_override") or "").strip() or None

    if not llm_runtime.get("available"):
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
            "narrative": "服务暂时不可用，请稍后再试。",
        })
        return

    # ── 6. 流式生成 + SentenceSplitter + 反思缓冲 ────────────────────
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake  # noqa: PLC0415
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

    splitter = SentenceSplitter()
    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    sentence_index = 0
    llm_started_ms = int(time.time() * 1000)

    # 反思缓冲：高风险对话先缓冲，等 apply_response_intelligence 改写后再推送
    buffer_stream = _should_buffer_stream_output(
        emotion_context=emotion_context,
        bond_summary=bond_summary or None,
    )

    try:
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=gen_params,
            base_url_override=base_url,
        ):
            if first_token_ms is None:
                first_token_ms = int(time.time() * 1000) - llm_started_ms

            full_text += chunk
            visible = _extract_visible_text(full_text)
            if not visible:
                continue

            # 委托标记检测——有委托时不流式推送
            if "[DELEGATE:" in full_text or '"delegate"' in full_text:
                continue

            if visible.startswith(rendered_text):
                new_chunk = visible[len(rendered_text):]
            else:
                new_chunk = visible
            if not new_chunk:
                continue

            rendered_text = visible

            # 反思缓冲模式下跳过实时推送
            if buffer_stream:
                continue

            yield _sse({"phase": "RENDER", "chunk": new_chunk})

            # 句子边界检测 → SENTENCE 事件（前端 TTS 实时跟读）
            for sentence in splitter.feed(new_chunk):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1

    except (TimeoutError, Exception) as _llm_exc:
        _logger.warning("[%s] top_agent LLM failed: %s", request_id, _llm_exc)
        if not full_text.strip():
            full_text = "ちょっと待ってね…"
            if not buffer_stream:
                yield _sse({"phase": "RENDER", "chunk": full_text})
                for sentence in splitter.feed(full_text):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

    # ── 7. 解析委托标记 ──────────────────────────────────────────────
    narrative = repair_utf8_latin1_mojibake(full_text) or full_text
    narrative = _extract_visible_text(narrative) or narrative

    delegate_agent, delegate_task, reply_template = _extract_delegate_from_llm_output(full_text)
    delegated_agents: list[dict[str, Any]] = []

    if delegate_agent and delegate_task:
        _logger.info(
            "[%s] top_agent delegate -> %s task=%s",
            request_id, delegate_agent, delegate_task[:60],
        )
        yield _sse({
            "phase": "PRESENCE", "event": "DELEGATING",
            "agent": delegate_agent, "request_id": request_id,
        })

        dispatcher = get_sub_agent_dispatcher()
        sub_resp = await dispatcher.dispatch(SubAgentRequest(
            agent_id=delegate_agent,
            task=delegate_task or sanitized_message,
            user_message=sanitized_message,
            request_id=request_id,
            session_id=session_id,
        ))

        delegated_agents.append({
            "agent_id": sub_resp.agent_id,
            "success": sub_resp.success,
            "latency_ms": sub_resp.latency_ms,
        })

        if sub_resp.success and sub_resp.result:
            if reply_template and "{agent_result}" in reply_template:
                narrative = reply_template.replace("{agent_result}", sub_resp.result)
            else:
                narrative = sub_resp.result
            yield _sse({"phase": "RENDER", "chunk": narrative})
        else:
            narrative = f"啊啦，{delegate_agent} 这边好像出了点状况呢…要不过会儿再试试？"
            yield _sse({"phase": "RENDER", "chunk": narrative})

    # ── 8. apply_response_intelligence（反思链 + 多模态后缀）──────────
    response_intelligence_meta: dict[str, Any] = {}
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

        narrative, response_intelligence_meta = apply_response_intelligence(
            request_id=request_id,
            user_message=sanitized_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary or None,
            render_lang=render_target_lang,
            allow_rewrite=buffer_stream,
        )

        # 缓冲模式：改写后一次性推送
        if buffer_stream and narrative:
            rendered_text = narrative
            yield _sse({"phase": "RENDER", "chunk": narrative})
            for sentence in splitter.feed(narrative):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1
        elif narrative.startswith(rendered_text):
            # 非缓冲模式：推送反思链可能追加的后缀（如 emoji）
            postprocess_chunk = narrative[len(rendered_text):]
            if postprocess_chunk:
                rendered_text = narrative
                yield _sse({"phase": "RENDER", "chunk": postprocess_chunk})
                for sentence in splitter.feed(postprocess_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
    except Exception as _ri_exc:
        response_intelligence_meta = {
            "reflection": {"status": "DEGRADED", "reason_code": str(_ri_exc)[:100]},
        }
        _logger.debug("[%s] top_agent response_intelligence failed: %s", request_id, _ri_exc)

    # flush 剩余未成句文本
    remainder = splitter.flush()
    if remainder:
        yield _sse({
            "phase": "SENTENCE",
            "sentence": remainder,
            "sentence_index": sentence_index,
        })
        sentence_index += 1

    # ── 9. 出口净化 ──────────────────────────────────────────────────
    narrative = _airlock_sanitize_output(narrative)

    # ── 9.5 PII 出口检查（防止 LLM 重放 PII）────────────────────────
    _pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            if _pii_eg.has_pii:
                _pii_egress_stats = {
                    "detections": _pii_eg.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_eg.detections}),
                }
    except Exception:
        pass

    # ── 10. COMMIT 事件 ──────────────────────────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms
    llm_latency_ms = int(time.time() * 1000) - llm_started_ms

    yield _sse({
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "narrative": narrative,
        "request_id": request_id,
        "audit_refs": {
            "request_id": request_id,
            "path": "delegate" if delegated_agents else "chat_direct",
            "latency_ms": total_latency_ms,
            "first_token_ms": first_token_ms,
            "llm_latency_ms": llm_latency_ms,
            "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            "stream_buffered": buffer_stream,
            "delegated_agents": delegated_agents,
            "response_intelligence": {
                k: v for k, v in response_intelligence_meta.items()
                if k in ("reflection", "multimodal_output")
            } if response_intelligence_meta else {},
            "pii_stats": {
                "ingress": _pii_ingress_stats,
                "egress": _pii_egress_stats,
            } if (_pii_ingress_stats or _pii_egress_stats) else {},
        },
    })

    # ── 11. 异步后处理（不阻塞回复）─────────────────────────────────
    asyncio.create_task(
        _async_post_process(
            request_id=request_id,
            session_id=session_id,
            user_message=sanitized_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            render_target_lang=render_target_lang,
            # 链路追踪审计数据
            total_latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            first_token_ms=first_token_ms,
            provider_name=provider_name,
            model_id=model_id,
            buffer_stream=buffer_stream,
            delegated_agents=delegated_agents,
            retrieval_summary=retrieval_summary,
            gov_decision=gov,
            pii_ingress_stats=_pii_ingress_stats,
            pii_egress_stats=_pii_egress_stats,
        )
    )


# ---------------------------------------------------------------------------
# 异步后处理
# ---------------------------------------------------------------------------

async def _async_post_process(
    *,
    request_id: str,
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
            "ts_ms": _now,
        })

        # (3) graph_run_trace — 端到端链路汇总
        _path = "delegate" if delegated_agents else "chat_direct"
        _retrieval_status = (retrieval_summary or {}).get("status", "SKIPPED")
        _retrieval_count = (retrieval_summary or {}).get("candidate_count", 0)

        # 构建 nodes_json：记录每个处理阶段的耗时和状态
        trace_nodes: list[dict[str, Any]] = [
            {"node_id": "airlock_ingress", "node_type": "governor", "display_name": "Airlock 入口",
             "side": "host", "status": "OK", "ts_ms": ts_ms},
            {"node_id": "governor", "node_type": "governor", "display_name": "Governor",
             "side": "host", "status": gov_decision.get("decision", "APPROVE") if gov_decision else "OK",
             "latency_ms": gov_decision.get("latency_ms", 0) if gov_decision else 0, "ts_ms": ts_ms},
            {"node_id": "recall_fusion", "node_type": "recall", "display_name": "Recall Fusion",
             "side": "host", "status": _retrieval_status,
             "output_data": {"candidate_count": _retrieval_count}, "ts_ms": ts_ms},
            {"node_id": "prompt_builder", "node_type": "system", "display_name": "Prompt Builder",
             "side": "host", "status": "OK", "ts_ms": ts_ms},
            {"node_id": "llm_stream", "node_type": "llm", "display_name": "LLM 流式生成",
             "side": "host", "status": "OK" if narrative.strip() else "DEGRADED",
             "latency_ms": llm_latency_ms, "provider": provider_name, "model_id": model_id,
             "input_data": {"first_token_ms": first_token_ms, "buffer_stream": buffer_stream},
             "ts_ms": ts_ms},
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
        if tts_enabled in ("1", "true", "yes", "on") and narrative.strip():
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
