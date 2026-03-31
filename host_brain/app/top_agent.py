"""top_agent.py — 顶级 Agent SSE 生成器（Phase XIV v4 核心）。

夏目蓝本人，唯一入口。

闲聊路径（80%）：Airlock → LLM 流式 → Airlock → 异步后处理
执行路径（20%）：Airlock → LLM 判断 → 调度子 Agent → 组织回复 → Airlock → 异步后处理

治理原则：
  - 闲聊不需要 Governor/审计（说话不是执行动作）
  - 执行治理下沉到子 Agent 内部（白皮书合规）
  - 只保留极轻量 Governor（速率限制 + sys_mode）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict

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
    """出口净化：null byte + 安全词 + 长度。"""
    text = text.replace("\x00", "").strip()
    # 长度限制
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        # 在句子边界截断
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


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
        # reply_template = 标记之前 + {agent_result} + 标记之后
        before = full_text[: m.start()].strip()
        after = full_text[m.end():].strip()
        template = f"{before}\n{{agent_result}}\n{after}".strip()
        if agent_id in ALL_AGENT_IDS:
            return agent_id, task, template

    # 方式 2：JSON delegate（整个输出是 JSON）
    try:
        # 尝试提取 JSON（可能被 ```json 包裹）
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
      Airlock → 轻量 Governor → 检索 → LLM 流式
      → [解析委托] → 子 Agent 执行（可选）→ 最终回复
      → Airlock → 异步后处理
    """
    user_message = str(request_body.message or "").strip()

    # ── 1. Airlock 入口净化 ──────────────────────────────────────────
    sanitized_message, airlock_passed = _airlock_sanitize_input(user_message)
    if not airlock_passed:
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "AIRLOCK_EMPTY_MESSAGE", "request_id": request_id,
        })
        return

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

    try:
        from host_brain.app.chat_stream_handler import _run_retrieval  # noqa: PLC0415
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
        render_target_lang = "ja"

    # ── 4. 等检索结果（最多 3 秒）───────────────────────────────────
    if retrieval_task is not None:
        try:
            retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                retrieval_task, timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception) as _retr_exc:
            retrieval_summary = {"status": "DEGRADED", "error": str(_retr_exc)[:100]}
            _logger.debug("[%s] top_agent retrieval failed/timeout: %s", request_id, _retr_exc)

    # ── 5. 构建 prompt + 流式 LLM 调用 ──────────────────────────────
    from host_brain.app.top_agent_prompt import build_top_agent_prompt  # noqa: PLC0415

    prompt_text, system_prompt, gen_params = build_top_agent_prompt(
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        retrieval_evidence_items=retrieval_evidence_items,
        session_id=session_id,
        request_id=request_id,
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

    # ── 6. 流式生成 ──────────────────────────────────────────────────
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake  # noqa: PLC0415

    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    llm_started_ms = int(time.time() * 1000)

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

            # 提取可见文本（跳过 JSON 包装的 narrative.text）
            visible = _extract_visible_text(full_text)
            if not visible:
                continue

            # 检查是否包含委托标记——如果是，不流式推送（等 LLM 完成后统一处理）
            if "[DELEGATE:" in full_text or '"delegate"' in full_text:
                continue

            # 流式推送
            if visible.startswith(rendered_text):
                new_chunk = visible[len(rendered_text):]
            else:
                new_chunk = visible
            if new_chunk:
                rendered_text = visible
                yield _sse({"phase": "RENDER", "chunk": new_chunk})

    except (TimeoutError, Exception) as _llm_exc:
        _logger.warning("[%s] top_agent LLM failed: %s", request_id, _llm_exc)
        if not full_text.strip():
            full_text = "ちょっと待ってね…"
            yield _sse({"phase": "RENDER", "chunk": full_text})

    # ── 7. 解析委托标记 ──────────────────────────────────────────────
    narrative = repair_utf8_latin1_mojibake(full_text) or full_text
    narrative = _extract_visible_text(narrative) or narrative

    delegate_agent, delegate_task, reply_template = _extract_delegate_from_llm_output(full_text)

    delegated_agents: list[dict[str, Any]] = []

    if delegate_agent and delegate_task:
        _logger.info(
            "[%s] top_agent delegate → %s task=%s",
            request_id, delegate_agent, delegate_task[:60],
        )

        # 通知前端正在执行
        yield _sse({
            "phase": "PRESENCE", "event": "DELEGATING",
            "agent": delegate_agent, "request_id": request_id,
        })

        # 调度子 Agent
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
            # 方式 A：模板填充
            if reply_template and "{agent_result}" in reply_template:
                narrative = reply_template.replace("{agent_result}", sub_resp.result)
            else:
                # 方式 A fallback：直接拼接
                narrative = sub_resp.result

            # 推送最终结果（之前没流式推送过）
            yield _sse({"phase": "RENDER", "chunk": narrative})
        else:
            # 子 Agent 失败，使用降级回复
            narrative = f"啊啦，{delegate_agent} 这边好像出了点状况呢…要不过会儿再试试？"
            yield _sse({"phase": "RENDER", "chunk": narrative})

    # ── 8. 出口净化 ──────────────────────────────────────────────────
    narrative = _airlock_sanitize_output(narrative)

    # ── 9. COMMIT 事件（精简版 audit_refs）───────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms

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
            "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            "delegated_agents": delegated_agents,
        },
    })

    # ── 10. 异步后处理（不阻塞回复）─────────────────────────────────
    asyncio.create_task(
        _async_post_process(
            request_id=request_id,
            session_id=session_id,
            user_message=sanitized_message,
            narrative=narrative,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
            render_target_lang=render_target_lang,
        )
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _extract_visible_text(raw: str) -> str:
    """从 LLM 原始输出中提取可见文本。

    处理两种格式：
    1. 纯文本（直接返回）
    2. JSON: {"narrative": {"text": "..."}} （提取 text）
    """
    text = raw.strip()
    if not text:
        return ""

    # 尝试 JSON 解析
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


async def _async_post_process(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    ts_ms: int,
    render_target_lang: str,
) -> None:
    """异步后处理：记忆写入 + Bond 更新 + VTS + TTS。

    fire-and-forget，不阻塞主回复流。
    """
    try:
        # 记忆写入
        from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

        store = get_episodic_store()
        if store is not None:
            await asyncio.to_thread(
                persist_chat_episodic_memory,
                request_id=request_id,
                intent_id=request_id,
                session_id=session_id,
                user_message=user_message,
                assistant_reply=narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=store,
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process episodic failed: %s", request_id, exc)

    try:
        # Bond 更新
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

    try:
        # VTS 情感驱动
        from host_brain.app.chat_stream_handler import _drive_stream_vts_emotion  # noqa: PLC0415
        await _drive_stream_vts_emotion(
            request_id=request_id, emotion_context=emotion_context, ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process vts failed: %s", request_id, exc)

    try:
        # TTS 语音播报
        tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
        if tts_enabled in ("1", "true", "yes", "on") and narrative.strip():
            from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
            from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

            etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
            tts_route = resolve_tts_route(
                text=narrative, emotion_context=emotion_context, bond_summary=None,
            )
            await post_speak(
                text=narrative,
                request_id=request_id,
                session_id=session_id,
                speed=float(tts_route.get("speed") or 1.0),
                pitch=float(tts_route.get("pitch") or 0.0),
                emotion_tag=str(tts_route.get("emotion_tag") or etag),
                lang_hint=str(tts_route.get("lang_hint") or render_target_lang),
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process tts failed: %s", request_id, exc)
