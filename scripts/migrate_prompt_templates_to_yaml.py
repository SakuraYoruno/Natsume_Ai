"""migrate_prompt_templates_to_yaml.py

将 prompt_template_defs.py 中的所有模板定义按功能分组导出为 YAML 文件。
输出目录：prompts/templates/
运行方式：python scripts/migrate_prompt_templates_to_yaml.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # type: ignore[import]

from host_brain.app.prompt_template_defs import list_prompt_template_definitions

# scope → 分组文件名映射
_SCOPE_GROUP: dict[str, str] = {}

_AGENT_SCOPES = {
    "intent_agent_handler", "intent_agent_entry_handler",
    "translate_agent_handler", "repair_agent_handler", "emotion_agent_handler",
    "chat_agent_handler", "retrieval_agent_handler", "judge_agent_handler",
    "supervisor_dispatch_handler", "sub_agent_protocol", "react_loop",
    "vision.action_planner", "chat_handler_vision",
}
_CHAT_SCOPES = {
    "chat_agent", "chat_handler", "chat_context_builder", "prompt_builder",
    "cortex_prompt_builder", "prompt_context", "reflection_chain",
    "expression_adapter", "interaction_pattern_analyzer", "bond_aware_style",
    "style_distiller", "proactive_voice", "intelligent_runtime",
}
_EXECUTIVE_SCOPES = {
    "executive_router", "cortex_orchestrator", "http_hooks_runtime_helpers",
    "input_routes", "provider_factory", "cortex_route_decider",
}
_PERSONA_SCOPES = {
    "persona", "inner_world", "body_sense", "self_model", "self_reflection",
    "emotional_silence", "emotion_context", "emotion_llm_inference",
    "emotion_utterance_index", "trait_drift_guard", "drift_detector",
    "evolution_orchestrator", "evolution_desire_engine",
}
_GOVERNANCE_SCOPES = {
    "immune_agent", "core.qa",
}

def _group(scope: str) -> str:
    if scope in _AGENT_SCOPES:
        return "agents"
    if scope in _CHAT_SCOPES:
        return "chat"
    if scope in _EXECUTIVE_SCOPES:
        return "executive"
    if scope in _PERSONA_SCOPES:
        return "persona"
    if scope in _GOVERNANCE_SCOPES:
        return "governance"
    return "misc"

def main() -> None:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", "templates")
    os.makedirs(out_dir, exist_ok=True)

    groups: dict[str, list[dict]] = {}
    for t in list_prompt_template_definitions():
        g = _group(t.scope)
        groups.setdefault(g, [])
        # 尝试解析 JSON content，保持结构化
        try:
            if t.content_type == "json":
                content = json.loads(t.default_content)
            else:
                content = t.default_content
        except Exception:
            content = t.default_content

        groups[g].append({
            "template_id": t.template_id,
            "category": t.category,
            "scope": t.scope,
            "locale": t.locale,
            "content_type": t.content_type,
            "description": t.description,
            "default_content": content,
        })

    for group_name, templates in sorted(groups.items()):
        out_path = os.path.join(out_dir, f"{group_name}.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"templates": templates},
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )
        print(f"  写入 {out_path}（{len(templates)} 个模板）")

    print(f"\n迁移完成，共 {sum(len(v) for v in groups.values())} 个模板，{len(groups)} 个分组文件。")

if __name__ == "__main__":
    main()
