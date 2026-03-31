"""top_agent_prompt.py — 顶级 Agent prompt 构建（Phase XIV v4）。

复用现有 prompt_builder 的核心组件：
  - character_profile（角色人设）
  - style_canon v5（说话风格）
  - persona_examples（台词参考）
  - Bond / Emotion 上下文
  - 检索证据

新增：
  - 子 Agent 能力描述 + 委托协议说明
  - 简化的输出格式（不再强制 JSON 封装）
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from host_brain.app.sub_agent_protocol import AGENT_CAPABILITIES


def build_top_agent_prompt(
    *,
    user_message: str,
    render_target_lang: str = "ja",
    emotion_context: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    session_id: str = "",
    request_id: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """构建顶级 Agent 的 prompt。

    Returns:
        (prompt_text, system_prompt, gen_params)
    """
    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}

    # ── 角色人设（常驻最高优先级）─────────────────────────────────────
    character_profile = _load_character_profile(render_target_lang)

    # ── 说话风格 ──────────────────────────────────────────────────────
    style_instruction = _load_style_instruction()

    # ── 台词参考 ──────────────────────────────────────────────────────
    persona_examples = _load_persona_examples(emotion_context)

    # ── Bond 感知 ─────────────────────────────────────────────────────
    bond_block = _build_bond_block(emotion_context)

    # ── 情感绑定 ──────────────────────────────────────────────────────
    emotion_block = _build_emotion_block(emotion_context, emotion_snapshot)

    # ── 检索证据 ──────────────────────────────────────────────────────
    evidence_block = _build_evidence_block(retrieval_evidence_items)

    # ── 子 Agent 能力描述（仅对执行路径有用）──────────────────────────
    agent_block = _build_agent_capabilities_block(render_target_lang)

    # ── system_prompt（简洁版）────────────────────────────────────────
    system_prompt = (
        f"{character_profile}\n\n"
        f"{style_instruction}\n\n"
        f"{persona_examples}\n\n"
        f"{bond_block}\n\n"
        f"{emotion_block}\n\n"
        f"{agent_block}\n\n"
        "输出规则：\n"
        "1) 直接用自然语言回复，不需要 JSON 封装。\n"
        "2) 如果需要调用子 Agent，在回复中嵌入 [DELEGATE:agent_id:任务描述] 标记。\n"
        "3) 代码块用 ``` 包裹。\n"
        "4) 不要说自己是 AI/助手/机器人。\n"
        "5) 遵循【说话风格】和台词参考的语气、节奏和句式。\n"
    ).strip()

    # ── prompt_text（用户消息 + 证据）────────────────────────────────
    prompt_text = (
        f"{evidence_block}\n\n"
        f"【用户消息】\n{user_message}\n"
    ).strip()

    # ── gen_params ────────────────────────────────────────────────────
    gen_params: dict[str, Any] = {
        "temperature": 0.75,
        "max_output_tokens": 2048,
    }
    # 情感调制
    etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
    if etag in ("EXCITED", "HAPPY", "LOVING"):
        gen_params["temperature"] = 0.85
    elif etag in ("SAD", "ANXIOUS", "ANGRY"):
        gen_params["temperature"] = 0.65

    return prompt_text, system_prompt, gen_params


# ---------------------------------------------------------------------------
# 内部组件加载
# ---------------------------------------------------------------------------

def _load_character_profile(render_lang: str) -> str:
    """加载常驻角色人设。"""
    try:
        from host_brain.app.prompt_builder import _load_character_profile as _load_cp  # noqa: PLC0415
        text = _load_cp(render_lang)
        if text:
            return f"【角色人设】\n{text}"
    except Exception:
        pass
    return ""


def _load_style_instruction() -> str:
    """加载 style_canon v5 说话风格。"""
    try:
        from host_brain.app.prompt_builder import _load_style_profile  # noqa: PLC0415
        style = _load_style_profile()
        if style:
            return f"【说话风格】\n{style}"
    except (ImportError, AttributeError):
        pass

    # fallback：从环境变量或文件加载
    try:
        from pathlib import Path
        style_path = Path(os.environ.get("NATSUME_STYLE_PROFILE_PATH") or "runtime/style_canon/style_profile_v5.json")
        if style_path.exists():
            import json as _json
            data = _json.loads(style_path.read_text(encoding="utf-8"))
            instruction = str(data.get("style_instruction") or data.get("instruction") or "").strip()
            if instruction:
                return f"【说话风格】\n{instruction}"
    except Exception:
        pass

    return ""


def _load_persona_examples(emotion_context: dict[str, Any]) -> str:
    """加载台词风格参考（EmotionTaggedUtteranceIndex）。"""
    try:
        from host_brain.retrieval.emotion_utterance_index import get_utterance_index  # noqa: PLC0415
        etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
        index = get_utterance_index()
        if index.is_built:
            result = index.retrieve_multi_scene(etag, lang="cn", primary_n=3, daily_n=2, max_chars=400)
            block = index.render_for_prompt(result, lang="cn")
            if block:
                return block
    except Exception:
        pass
    return ""


def _build_bond_block(emotion_context: dict[str, Any]) -> str:
    """构建 Bond 感知风格指导。"""
    try:
        from host_brain.app.dependencies import get_bond_aware_style_engine  # noqa: PLC0415
        engine = get_bond_aware_style_engine()
        if engine is not None:
            style = engine.compute_style(emotion_context=emotion_context)
            if style:
                stage = str(style.get("bond_stage") or "STRANGER")
                prompt = str(style.get("prompt_injection") or "").strip()
                if prompt:
                    return f"BOND_AWARE_STYLE:\n- bond_stage: {stage}\n- prompt: {prompt}"
    except Exception:
        pass
    return ""


def _build_emotion_block(
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
) -> str:
    """构建情感绑定块（精简版）。"""
    etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
    mood = str(emotion_context.get("mood_state") or "steady").strip()
    intensity = emotion_context.get("emotion_intensity")

    lines = [
        "EMOTION_BINDING:",
        f"- emotion_tag: {etag}",
        f"- mood_state: {mood}",
    ]
    if intensity is not None:
        lines.append(f"- emotion_intensity: {intensity}")
    return "\n".join(lines)


def _build_evidence_block(items: list[dict[str, Any]] | None) -> str:
    """构建检索证据块。"""
    if not items:
        return "【检索证据】\n- (none)"

    lines = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("evidence_ref") or item.get("source_ref") or "").strip()[:80]
        snippet = str(item.get("content") or item.get("snippet") or "").strip()[:220]
        if snippet:
            lines.append(f"- [{ref}] {snippet}")

    return "【检索证据】\n" + ("\n".join(lines) if lines else "- (none)")


def _build_agent_capabilities_block(render_lang: str) -> str:
    """构建子 Agent 能力描述块（供 LLM 判断是否需要委托）。"""
    lines = ["【可用能力】"]
    lines.append("如果用户的请求需要以下能力，使用 [DELEGATE:agent_id:任务描述] 委托对应 Agent：")
    for agent_id, desc in AGENT_CAPABILITIES.items():
        lines.append(f"- {agent_id}：{desc}")
    lines.append("如果是日常闲聊，直接回复即可，不需要委托。")
    return "\n".join(lines)
