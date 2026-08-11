"""Agent 注册表：定义 agent allowlist 与元数据。

所有合法的 agent_id 必须在此注册，Supervisor handler 在校验
分派提案时会对照此 allowlist，拒绝未注册的 agent_id。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Agent ID 常量
# ---------------------------------------------------------------------------

AGENT_ID_CHAT = "surface_reply"
AGENT_ID_TRANSLATE = "translate_agent"
AGENT_ID_INTENT = "intent_agent"
AGENT_ID_SAFETY = "safety_agent"
AGENT_ID_JUDGE = "judge_agent"
AGENT_ID_RENDERER = "renderer_agent"
AGENT_ID_RETRIEVAL = "retrieval_agent"  # P11 新增
AGENT_ID_REPAIR = "repair_agent"
AGENT_ID_PENTEST = "pentest_agent"  # 无副作用规划与 canonical SecureRun 交接

# ---------------------------------------------------------------------------
# Agent Allowlist（Supervisor 分派时的合法目标集合）
# ---------------------------------------------------------------------------

AGENT_ALLOWLIST: frozenset[str] = frozenset({
    AGENT_ID_CHAT,
    AGENT_ID_TRANSLATE,
    AGENT_ID_INTENT,
    # AGENT_ID_SAFETY 和 AGENT_ID_RENDERER 暂不加入 allowlist：
    # 无对应 handler 文件，LLM 提案选到会触发 AGENT_DISPATCH_NO_HANDLER 降级。
    # 待 P17+ 补充 handler 后再加入。
    AGENT_ID_JUDGE,
    AGENT_ID_RETRIEVAL,  # P11 新增
    AGENT_ID_REPAIR,
    AGENT_ID_PENTEST,
})

# ---------------------------------------------------------------------------
# Agent 元数据（供 Supervisor 提案校验和 trace 使用）
# ---------------------------------------------------------------------------

_AGENT_META: dict[str, dict[str, Any]] = {
    AGENT_ID_CHAT: {
        "agent_id": AGENT_ID_CHAT,
        "display_name": "SurfaceReply",
        "node_id": "chat_direct_ja",          # 复用现有节点
        "fast_path": True,                     # 快路径，不经过 Supervisor
        "allow_parallel": False,
        "has_side_effects": False,
        "risk_level": "low",
        "description": "普通聊天快路径，轻治理 + 强边界",
    },
    AGENT_ID_TRANSLATE: {
        "agent_id": AGENT_ID_TRANSLATE,
        "display_name": "TranslateAgent",
        "node_id": "workbench_translate",      # 复用现有节点
        "fast_path": False,
        "allow_parallel": True,                # 只读，允许并行
        "has_side_effects": False,
        "risk_level": "low",
        "description": "翻译草案生成，只产出工件",
    },
    AGENT_ID_INTENT: {
        "agent_id": AGENT_ID_INTENT,
        "display_name": "IntentAgent",
        "node_id": "core_reasoner_primary",    # 多节点链入口
        "fast_path": False,
        "allow_parallel": False,
        "has_side_effects": True,              # 涉及工具调用
        "risk_level": "high",
        "description": "执行意图，涉及工具调用，第一阶段保留重链",
    },
    AGENT_ID_SAFETY: {
        "agent_id": AGENT_ID_SAFETY,
        "display_name": "SafetyAgent",
        "node_id": "safety_verifier",          # 复用现有节点
        "fast_path": False,
        "allow_parallel": False,
        "has_side_effects": False,
        "risk_level": "low",
        "description": "安全校验，仅中高风险 / 执行场景条件触发",
    },
    AGENT_ID_JUDGE: {
        "agent_id": AGENT_ID_JUDGE,
        "display_name": "JudgeAgent",
        "node_id": "consistency_verifier",     # 复用现有节点
        "fast_path": False,
        "allow_parallel": False,
        "has_side_effects": False,
        "risk_level": "low",
        "description": "统一收口，裁判汇总子 agent 输出",
    },
    AGENT_ID_RENDERER: {
        "agent_id": AGENT_ID_RENDERER,
        "display_name": "RendererAgent",
        "node_id": "renderer_ja_c_style",      # 复用现有节点
        "fast_path": False,
        "allow_parallel": False,
        "has_side_effects": False,
        "risk_level": "low",
        "description": "最终成文输出",
    },
    AGENT_ID_RETRIEVAL: {  # P11 新增
        "agent_id": AGENT_ID_RETRIEVAL,
        "display_name": "RetrievalAgent",
        "node_id": "workbench_retrieval",
        "fast_path": False,
        "allow_parallel": True,                # 只读检索，允许并行
        "has_side_effects": False,
        "risk_level": "low",
        "description": "知识库检索，只读，产出检索工件",
    },
    AGENT_ID_REPAIR: {
        "agent_id": AGENT_ID_REPAIR,
        "display_name": "RepairAgent",
        "node_id": "environment_repair",
        "fast_path": False,
        "allow_parallel": False,
        "has_side_effects": True,
        "risk_level": "high",
        "description": "环境修复与依赖安装，负责补齐运行所需的浏览器/工具后端",
    },
    AGENT_ID_PENTEST: {
        "agent_id": AGENT_ID_PENTEST,
        "display_name": "PentestAgent",
        "node_id": "pentest_agent_node",
        "fast_path": False,
        "allow_parallel": True,
        "has_side_effects": False,
        "risk_level": "high",
        "description": (
            "Pentest 需求澄清、只读规划与 Domain Pack/SecureRun 交接；"
            "Agent 本身不执行网络、工具或攻击内核"
        ),
    },
}


def is_agent_allowed(agent_id: str) -> bool:
    """检查 agent_id 是否在 allowlist 内。"""
    return str(agent_id or "").strip() in AGENT_ALLOWLIST


def get_agent_meta(agent_id: str) -> dict[str, Any] | None:
    """获取 agent 元数据，不存在时返回 None。"""
    return _AGENT_META.get(str(agent_id or "").strip())
