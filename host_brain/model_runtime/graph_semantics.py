"""Server-owned semantic bindings for executable graph model nodes."""

from __future__ import annotations

from dataclasses import dataclass

from host_brain.model_runtime.live_prompts import GRAPH_NODE_PROMPT_IDS


class UnknownGraphModelSemanticError(KeyError):
    """Raised when a graph model node has no centrally registered semantic."""


@dataclass(frozen=True, slots=True)
class GraphModelSemantic:
    model_node_id: str
    prompt_id: str
    scene_type: str
    cognitive_tier: str


_GRAPH_MODEL_SEMANTICS = {
    "chat_direct_ja": GraphModelSemantic(
        "chat_direct_ja", GRAPH_NODE_PROMPT_IDS["chat_direct_ja"], "CHAT", "C"
    ),
    "core_reasoner_primary": GraphModelSemantic(
        "core_reasoner_primary",
        GRAPH_NODE_PROMPT_IDS["core_reasoner_primary"],
        "GRAPH_CORE_REASONER",
        "A",
    ),
    "core_reasoner_secondary": GraphModelSemantic(
        "core_reasoner_secondary",
        GRAPH_NODE_PROMPT_IDS["core_reasoner_secondary"],
        "GRAPH_CORE_REASONER",
        "B",
    ),
    "planner_constraints": GraphModelSemantic(
        "planner_constraints",
        GRAPH_NODE_PROMPT_IDS["planner_constraints"],
        "GRAPH_CONSTRAINT_PLANNER",
        "A",
    ),
    "tool_planner": GraphModelSemantic(
        "tool_planner", GRAPH_NODE_PROMPT_IDS["tool_planner"], "GRAPH_TOOL_PLANNER", "B"
    ),
    "safety_verifier": GraphModelSemantic(
        "safety_verifier",
        GRAPH_NODE_PROMPT_IDS["safety_verifier"],
        "GRAPH_SAFETY_VERIFIER",
        "B",
    ),
    "consistency_verifier": GraphModelSemantic(
        "consistency_verifier",
        GRAPH_NODE_PROMPT_IDS["consistency_verifier"],
        "GRAPH_CONSISTENCY_VERIFIER",
        "B",
    ),
    "renderer_ja_c_style": GraphModelSemantic(
        "renderer_ja_c_style",
        GRAPH_NODE_PROMPT_IDS["renderer_ja_c_style"],
        "GRAPH_RENDERER",
        "C",
    ),
    "workbench_translate": GraphModelSemantic(
        "workbench_translate",
        GRAPH_NODE_PROMPT_IDS["workbench_translate"],
        "GRAPH_TRANSLATE",
        # Translation is a bounded transformation, not a deep-reasoning task.
        # Tier A selects a reasoning model whose hidden reasoning can exhaust
        # the small JSON response budget before any public content is emitted.
        "B",
    ),
    "workbench_code_assist": GraphModelSemantic(
        "workbench_code_assist",
        GRAPH_NODE_PROMPT_IDS["workbench_code_assist"],
        "GRAPH_CODE_ASSIST",
        "A",
    ),
}


def graph_model_semantic(model_node_id: str) -> GraphModelSemantic:
    normalized = str(model_node_id or "").strip()
    semantic = _GRAPH_MODEL_SEMANTICS.get(normalized)
    if semantic is None:
        raise UnknownGraphModelSemanticError(
            f"GRAPH_MODEL_SEMANTIC_UNKNOWN:{normalized or '<empty>'}"
        )
    return semantic


def graph_model_semantic_ids() -> tuple[str, ...]:
    return tuple(sorted(_GRAPH_MODEL_SEMANTICS))


__all__ = [
    "GraphModelSemantic",
    "UnknownGraphModelSemanticError",
    "graph_model_semantic",
    "graph_model_semantic_ids",
]
