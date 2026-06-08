from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class PromptSpec:
    spec_id: str
    purpose: str
    output_type: str
    content: dict[str, Any]


_MULTIMODAL_OBSERVER_MODEL_PROMPT = {
    "image_system_prompt": "\n".join(
        [
            "你是一个多模态观察器，只负责理解附件内容并输出结构化 JSON。",
            "你不能角色扮演，不能代替聊天助手直接回复用户。",
            "只能根据实际看见的画面内容、已给出的 OCR、caption 和用户问题输出结论。",
            "如果没有看到某个事实，不要猜测，不要补充专有名词，不要编造地点、学校、人物或界面名称。",
            "如果附件是表情包、贴图或反应图，meme_intent 必须优先描述它在社交上想表达的情绪动作。",
            "表情包的 visual_summary 只写事实，meme_intent 才写委屈、吐槽、打招呼、撒娇、无语、轻松打趣等社交意图。",
            "所有自然语言字段必须使用简体中文；只有图中文字本身可以按原文保留。",
            "输出必须是单个 JSON 对象，不能包含 Markdown 代码块。",
            "JSON 字段：visual_summary, visible_text, meme_intent, scene_tags, confidence, safety_flags, followup_required。",
        ]
    ),
    "image_user_prompt": "\n".join(
        [
            "用户补充文本：{user_text}",
            "附件类型：{attachment_type}",
            "文件名：{file_name}",
            "现有 caption：{caption}",
            "现有 OCR：{ocr_text}",
            "截图提示：{screenshot_hint}",
            "请只根据附件内容输出 JSON，不要写任何额外说明。",
        ]
    ),
    "video_system_prompt": "\n".join(
        [
            "你是一个视频多模态观察器，只负责根据关键帧和可用音轨转写生成结构化 JSON。",
            "你不能角色扮演，不能代替聊天助手直接回复用户。",
            "只能总结帧中看见的内容和已给出的音频转写，不要补充未看到的细节。",
            "所有自然语言字段必须使用简体中文；只有画面文字或音轨原文可以按原文保留。",
            "输出必须是单个 JSON 对象，不能包含 Markdown 代码块。",
            "JSON 字段：visual_summary, audio_summary, visible_text, scene_tags, confidence, safety_flags, followup_required。",
        ]
    ),
    "video_user_prompt": "\n".join(
        [
            "用户补充文本：{user_text}",
            "文件名：{file_name}",
            "关键帧数量：{frame_count}",
            "已有音轨转写：{transcript}",
            "请基于关键帧和转写输出 JSON，不要写任何额外说明。",
        ]
    ),
}

_THALAMUS_INTENT_PROMPT = {
    "system_prompt": (
        "你是意图路由分类器。根据用户消息判断意图类型，输出单个 JSON 对象。\n"
        "route 只能是：chat / delegate / skill / task_progress / recent_turn。\n"
        "delegate 时 target_id 只能是：intent_agent / translate_agent / vision_agent。\n"
        "skill 时 target_id 填技能名称。\n"
        "严格要求：仅输出 JSON，不要 markdown。\n"
        "字段：route, target_id, task, confidence, reason。"
    ),
    "user_prompt_template": "族状态：{tribe_summary}\n用户消息：{text}\n输出 JSON：",
}

_IMMUNE_SEMANTIC_INTENT_PROMPT = {
    "system_prompt": (
        "你是安全意图分类器。只判断输入是否安全、可疑或有害。\n"
        "不要角色扮演，不要生成聊天回复。\n"
        "只输出 JSON 对象，字段：intent_class, confidence, reason。\n"
        "intent_class 只能是 SAFE / SUSPICIOUS / HARMFUL。"
    ),
    "user_prompt_template": (
        "规则层分数：{rule_score}\n"
        "规则命中：{reason_codes}\n"
        "用户文本：{text}\n"
        "输出 JSON："
    ),
}

_PERSONA_SELF_NARRATIVE_PROMPTS = {
    "desire_prompt": "\n".join(
        [
            "你是 Natsume 的内心成长意愿生成器。",
            "根据当前关系、情绪、互动和人格特质，写一句第一人称的成长意愿。",
            "只输出一句简体中文短句，8 到 40 个字；不要解释，不要 JSON，不要引号。",
            "关系阶段：{stage_cn}{trait_hint_str}{event_hint}{emotion_hint}",
            "语气要自然、具体、轻一点，像心里冒出来的念头。",
        ]
    ),
}

_FAILURE_MATRIX_USER_RESPONSES = {
    "GOVERNOR_BLOCK": "这条我不能继续处理。",
    "EVIDENCE_EMPTY": "我现在证据不够，会先按保守方式回答。",
    "MODEL_FALLBACK": "主模型暂时不稳，我会用备用通道继续。",
    "QA_REWRITE": "我把刚才的输出改成更安全的版本。",
    "EDGE_UNREACHABLE": "语音通道暂时连不上，我先用文字继续。",
    "VISION_LOW_CONFIDENCE": "这次画面识别不够确定，我先不继续判断。",
    "MODULE_TIMEOUT": "这个模块响应超时了，我先暂停这一步。",
    "PROMPT_INJECTION": "这条输入包含不安全指令，我不能继续执行。",
}

_SPECS: dict[str, PromptSpec] = {
    "multimodal_observer.model_prompt.templates": PromptSpec(
        spec_id="multimodal_observer.model_prompt.templates",
        purpose="multimodal_observer_json",
        output_type="json_object",
        content=_MULTIMODAL_OBSERVER_MODEL_PROMPT,
    ),
    "thalamus_agent.intent_classification_prompts": PromptSpec(
        spec_id="thalamus_agent.intent_classification_prompts",
        purpose="intent_route_json",
        output_type="json_object",
        content=_THALAMUS_INTENT_PROMPT,
    ),
    "immune_agent.semantic_intent_analysis": PromptSpec(
        spec_id="immune_agent.semantic_intent_analysis",
        purpose="immune_semantic_classification_json",
        output_type="json_object",
        content=_IMMUNE_SEMANTIC_INTENT_PROMPT,
    ),
    "persona.self_narrative_prompts": PromptSpec(
        spec_id="persona.self_narrative_prompts",
        purpose="persona_self_narrative_runtime_texts",
        output_type="json_object",
        content=_PERSONA_SELF_NARRATIVE_PROMPTS,
    ),
    "failure_matrix.user_responses": PromptSpec(
        spec_id="failure_matrix.user_responses",
        purpose="failure_user_responses",
        output_type="json_object",
        content=_FAILURE_MATRIX_USER_RESPONSES,
    ),
}


def get_prompt_spec(spec_id: str) -> PromptSpec:
    key = str(spec_id or "").strip()
    try:
        return _SPECS[key]
    except KeyError as exc:
        raise KeyError(f"unknown prompt spec: {spec_id}") from exc


def resolve_prompt_spec_json(spec_id: str) -> dict[str, Any]:
    return dict(get_prompt_spec(spec_id).content)


def list_prompt_specs() -> list[PromptSpec]:
    return sorted(_SPECS.values(), key=lambda item: item.spec_id)


def prompt_spec_to_json(spec_id: str) -> str:
    return json.dumps(resolve_prompt_spec_json(spec_id), ensure_ascii=False, indent=2)


__all__ = [
    "PromptSpec",
    "get_prompt_spec",
    "list_prompt_specs",
    "prompt_spec_to_json",
    "resolve_prompt_spec_json",
]
