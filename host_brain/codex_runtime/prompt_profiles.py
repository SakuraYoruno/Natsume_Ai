"""Prompt profile generation for WorkOrder-first Codex execution."""

from __future__ import annotations

import json
from pathlib import Path

from host_brain.codex_runtime.work_order import CodexWorkOrder


def build_codex_prompt(work_order: CodexWorkOrder, *, workspace: Path) -> str:
    input_pack = dict(work_order.input_pack or {})
    metadata = dict(work_order.metadata or {})
    is_production = bool(metadata.get("codex_production"))
    parts = [
        "You are executing a Codex WorkOrder for the Natsume host runtime.",
        f"WorkOrder ID: {work_order.work_order_id}",
        f"Domain: {work_order.domain}",
        f"Run mode: {work_order.run_mode}",
        f"Workspace: {workspace}",
    ]
    if is_production:
        parts.append(
            "Production background role. You are a silent worker, not Natsume's persona. "
            "Return structured worker findings only; the Host surface/main chain owns final user-facing expression."
        )
    else:
        parts.append("Return only the final user-facing result.")
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
    if is_production:
        boundary = dict(metadata.get("persona_boundary") or {})
        result_contract = dict(metadata.get("result_contract") or {})
        must_not = [str(item) for item in list(boundary.get("must_not") or []) if str(item).strip()]
        must = [str(item) for item in list(boundary.get("must") or []) if str(item).strip()]
        if must:
            parts.append("Production persona boundary - must:\n" + "\n".join(f"- {item}" for item in must))
        if must_not:
            parts.append("Production persona boundary - must not:\n" + "\n".join(f"- {item}" for item in must_not))
        allowed_sections = [str(item) for item in list(result_contract.get("allowed_sections") or []) if str(item).strip()]
        forbidden_sections = [str(item) for item in list(result_contract.get("forbidden_sections") or []) if str(item).strip()]
        if allowed_sections or forbidden_sections:
            parts.append(
                "Production result contract:\n"
                + ("Allowed sections: " + ", ".join(allowed_sections) + "\n" if allowed_sections else "")
                + ("Forbidden sections: " + ", ".join(forbidden_sections) if forbidden_sections else "")
            )
        for title, key in (
            ("Source policy", "source_policy"),
            ("Account policy", "account_policy"),
            ("Purchase policy", "purchase_policy"),
            ("Artifact contract", "artifact_contract"),
        ):
            values = [str(item) for item in list(input_pack.get(key) or []) if str(item).strip()]
            if values:
                parts.append(title + ":\n" + "\n".join(f"- {item}" for item in values))
        if input_pack.get("purchase_requested"):
            parts.append(
                "Purchase gate: account points/coins may be spent only when approval_policy.confirmed=true. "
                "If confirmation is missing, produce a purchase_plan and stop before purchase/unlock controls."
            )
        workflow_keys = (
            "output_kind",
            "site_adapter",
            "urls",
            "browser_profile",
            "cadence",
            "driver_kind",
            "workflow_owner",
            "account_action_kind",
        )
        workflow_input = {key: input_pack.get(key) for key in workflow_keys if input_pack.get(key) not in (None, "", [], {})}
        if workflow_input:
            parts.append(
                "Production workflow input:\n"
                + json.dumps(workflow_input, ensure_ascii=False, indent=2, sort_keys=True)
            )
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
