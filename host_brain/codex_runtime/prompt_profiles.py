"""Prompt profile generation for WorkOrder-first Codex execution."""

from __future__ import annotations

from pathlib import Path

from host_brain.codex_runtime.work_order import CodexWorkOrder


def build_codex_prompt(work_order: CodexWorkOrder, *, workspace: Path) -> str:
    input_pack = dict(work_order.input_pack or {})
    parts = [
        "You are executing a Codex WorkOrder for the Natsume host runtime.",
        f"WorkOrder ID: {work_order.work_order_id}",
        f"Domain: {work_order.domain}",
        f"Run mode: {work_order.run_mode}",
        f"Workspace: {workspace}",
        "Return only the final user-facing result.",
    ]
    if work_order.domain == "bio_ops":
        parts.append(
            "BioOps proposal-only execution. Read evidence and produce audit reports, candidates, or recommendations only. "
            "Do not modify files, personality core, Bond, Canon, secrets, .env, production config, or long-term memory facts."
        )
    elif work_order.run_mode == "inspect":
        parts.append("Read-only execution. Do not modify files.")
    elif work_order.run_mode == "self_heal":
        parts.append("Diagnose the runtime fault, make the smallest bounded repair if authorized, and explain verification.")
    elif work_order.run_mode == "self_evolve":
        parts.append("Operate only in an experiment branch/workspace. Produce auditable changes and promotion notes.")
    elif work_order.run_mode == "audit":
        parts.append("Produce structured audit findings and do not provide trading advice.")
    elif work_order.run_mode == "translate":
        parts.append("Protect variables, control codes, speaker labels, and script structure.")
    else:
        parts.append("Keep file changes minimal, explicit, and auditable.")

    if work_order.allowed_actions:
        parts.append("Allowed actions: " + ", ".join(work_order.allowed_actions))
    if work_order.forbidden_actions:
        parts.append("Forbidden actions: " + ", ".join(work_order.forbidden_actions))
    if work_order.acceptance_criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(f"- {item}" for item in work_order.acceptance_criteria))
    if input_pack:
        context = str(input_pack.get("context") or input_pack.get("user_request") or "").strip()
        if context:
            parts.append("Context:\n" + context[:14_000])
    parts.append("Objective:\n" + work_order.objective)
    return "\n\n".join(part for part in parts if part).strip()


def prompt_profile_name(work_order: CodexWorkOrder) -> str:
    return f"{work_order.domain}_{work_order.run_mode}"


__all__ = ["build_codex_prompt", "prompt_profile_name"]
