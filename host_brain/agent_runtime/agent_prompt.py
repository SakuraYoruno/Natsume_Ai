"""从 AgentWorkOrder 构建 Agent system prompt。

改编自 prompt_profiles.py，输出格式适配 ReAct 循环的 system_prompt_extra。
包含工具列表、域约束、角色边界合约，引导 LLM 按 JSON 格式输出 thought + action。
"""

from __future__ import annotations

import json
from pathlib import Path

from host_brain.agent_runtime.work_order import AgentWorkOrder


def build_agent_system_prompt(
    work_order: AgentWorkOrder,
    *,
    workspace: Path,
    tools_description: str,
) -> str:
    """构建 Agent system prompt 的 extra 部分（不含通用 ReAct 指令）。

    Args:
        work_order: 当前执行的 AgentWorkOrder
        workspace: 工作区路径
        tools_description: 工具列表的可读描述（由 React loop 注入）

    Returns:
        追加到 system prompt 的指令文本
    """
    input_pack = dict(work_order.input_pack or {})
    metadata = dict(work_order.metadata or {})
    is_production = bool(metadata.get("agent_production"))

    parts: list[str] = [
        f"## WorkOrder Context",
        f"- WorkOrder ID: {work_order.work_order_id}",
        f"- Domain: {work_order.domain}",
        f"- Run mode: {work_order.run_mode}",
        f"- Workspace: {workspace}",
    ]

    # 角色声明
    if is_production:
        parts.append(
            "## Role\n"
            "You are a silent background worker for the Natsume host runtime. "
            "You are NOT Natsume's persona — return structured worker findings only. "
            "The host surface/main chain owns final user-facing expression."
        )
    else:
        parts.append(
            "## Role\n"
            "You are an autonomous code execution agent for the Natsume host runtime. "
            "Execute the WorkOrder using the available tools below."
        )

    # 域安全规则
    domain_rules = _build_domain_rules(work_order.domain, work_order.run_mode)
    if domain_rules:
        parts.append(domain_rules)

    # 允许/禁止操作（从 WorkOrder 的 allowed_actions / forbidden_actions）
    action_rules = _build_action_rules(work_order)
    if action_rules:
        parts.append(action_rules)

    # 生产合约（角色边界 + 结果合约）
    if is_production:
        contract = _build_production_contract(metadata, input_pack)
        if contract:
            parts.append(contract)

    # 验收标准
    if work_order.acceptance_criteria:
        parts.append(
            "## Acceptance Criteria\n"
            + "\n".join(f"- {item}" for item in work_order.acceptance_criteria)
        )

    # 上下文
    if input_pack:
        context = str(input_pack.get("context") or input_pack.get("user_request") or "").strip()
        if context:
            parts.append("## Context\n" + context[:14000])

    # 目标（核心）
    parts.append(
        "## Objective\n" + work_order.objective + "\n\n"
        "Follow these steps:\n"
        "1. Think about what needs to be done to complete the objective.\n"
        "2. Call tools as needed (one at a time for write tools, parallel OK for reads).\n"
        "3. When complete, call `agent_finish` with your summary and results."
    )

    # 可用工具说明
    if tools_description:
        parts.append("## Available Tools\n" + tools_description)

    return "\n\n".join(part for part in parts if part).strip()


def _build_domain_rules(domain: str, run_mode: str) -> str:
    domain = str(domain or "").strip().lower()
    run_mode = str(run_mode or "").strip().lower()

    rules: dict[str, str] = {
        "bio_ops": (
            "## BioOps Rules\n"
            "Read evidence and produce audit reports, candidates, or recommendations only. "
            "Do NOT modify files, personality core, Bond, Canon, secrets, .env, production config, or long-term memory facts."
        ),
        "immune": (
            "## Immune Rules\n"
            "Diagnose the issue from the health report. Propose minimal, bounded repairs. "
            "Do NOT modify files unless explicitly authorized by the work order approval policy."
        ),
        "evolution": (
            "## Evolution Rules\n"
            "Operate only in an experiment branch/workspace. Produce auditable changes and promotion notes. "
            "Do NOT modify main/master branch directly."
        ),
        "finance": (
            "## Audit Rules\n"
            "Produce structured audit findings. Do NOT provide trading advice or financial recommendations. "
            "Report anomalies, data discrepancies, and compliance issues only."
        ),
    }

    # run_mode-based rules
    mode_rules: dict[str, str] = {
        "inspect": "## Inspection Mode\nRead-only. Do NOT modify any files. Only read, analyze, and report findings.",
        "audit": "## Audit Mode\nProduce structured audit findings. No trading advice, no file modifications beyond reports.",
        "translate": "## Translation Mode\nProtect variables, control codes, speaker labels, and script structure. Only modify target language text.",
        "worker": "## Worker Mode\nFile modifications are allowed within the workspace. Keep changes minimal, explicit, and auditable. Use agent_git_diff to review changes before finishing.",
        "self_heal": "## Self-Heal Mode\nDiagnose the runtime fault. Make the smallest bounded repair if authorized. Write verification notes.",
        "self_evolve": "## Self-Evolve Mode\nOperate in an experiment branch. Produce auditable changes and promotion notes. Do NOT commit to main/master.",
    }

    result_parts: list[str] = []
    if domain in rules:
        result_parts.append(rules[domain])
    if run_mode in mode_rules:
        result_parts.append(mode_rules[run_mode])
    return "\n\n".join(result_parts) if result_parts else ""


def _build_action_rules(work_order: AgentWorkOrder) -> str:
    parts: list[str] = []
    if work_order.allowed_actions:
        parts.append("Allowed actions: " + ", ".join(work_order.allowed_actions))
    if work_order.forbidden_actions:
        parts.append("Forbidden actions: " + ", ".join(work_order.forbidden_actions))
    return "## Action Policy\n" + "\n".join(parts) if parts else ""


def _build_production_contract(metadata: dict, input_pack: dict) -> str:
    parts: list[str] = ["## Production Contract"]

    boundary = dict(metadata.get("persona_boundary") or {})
    must_not = [str(item) for item in list(boundary.get("must_not") or []) if str(item).strip()]
    must = [str(item) for item in list(boundary.get("must") or []) if str(item).strip()]
    if must:
        parts.append("**Must:**\n" + "\n".join(f"- {item}" for item in must))
    if must_not:
        parts.append("**Must NOT:**\n" + "\n".join(f"- {item}" for item in must_not))

    result_contract = dict(metadata.get("result_contract") or {})
    allowed_sections = [str(s) for s in list(result_contract.get("allowed_sections") or []) if str(s).strip()]
    forbidden_sections = [str(s) for s in list(result_contract.get("forbidden_sections") or []) if str(s).strip()]
    if allowed_sections:
        parts.append("**Allowed result sections:** " + ", ".join(allowed_sections))
    if forbidden_sections:
        parts.append("**Forbidden result sections:** " + ", ".join(forbidden_sections))

    for title, key in (
        ("Source Policy", "source_policy"),
        ("Account Policy", "account_policy"),
        ("Purchase Policy", "purchase_policy"),
    ):
        values = [str(item) for item in list(input_pack.get(key) or []) if str(item).strip()]
        if values:
            parts.append(f"**{title}:**\n" + "\n".join(f"- {item}" for item in values))

    if input_pack.get("purchase_requested"):
        parts.append(
            "**Purchase Gate:** account points/coins may be spent only when approval_policy.confirmed=true. "
            "If confirmation is missing, produce a purchase_plan and stop before purchase/unlock controls."
        )

    return "\n".join(parts) if len(parts) > 1 else ""


__all__ = ["build_agent_system_prompt"]
