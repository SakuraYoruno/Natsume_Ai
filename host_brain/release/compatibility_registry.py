"""Single release gate for temporary production compatibility facades.

Compatibility entries are code-owned and intentionally explicit.  A facade
may exist only while it is registered with an owner, replacement and removal
wave.  Core Release requires an empty active registry; retired module/symbol
guards then prevent accidental resurrection.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


COMPATIBILITY_REGISTRY_SCHEMA_VERSION = "natsume.compatibility-registry.v1"


@dataclass(frozen=True, slots=True)
class CompatibilityFacade:
    module: str
    symbols: tuple[str, ...]
    owner: str
    replacement: str
    remove_by_wave: str

    def __post_init__(self) -> None:
        if not self.module or not self.owner or not self.replacement or not self.remove_by_wave:
            raise ValueError("compatibility facade registration is incomplete")


@dataclass(frozen=True, slots=True)
class CompatibilityUsage:
    facade_module: str
    source_path: str
    symbol: str
    line: int


# AW08 owns every remaining entry.  This tuple must be empty at CODE_COMPLETE.
ACTIVE_COMPATIBILITY_FACADES: tuple[CompatibilityFacade, ...] = ()


# Core Release refuses to ship if any retired authority/facade is restored.
# Keep this list code-owned so the same invariant is enforced by contracts,
# manifest generation and the final acceptance runner.
RETIRED_CORE_PATHS: tuple[str, ...] = (
    "edge_client/gateway/router.py",
    "host_brain/agent_runtime/job_registry.py",
    "host_brain/agent_runtime/acceptance_judge.py",
    "host_brain/agent_runtime/budget_policy.py",
    "host_brain/agent_runtime/contract_errors.py",
    "host_brain/agent_runtime/production_executor.py",
    "host_brain/agent_runtime/review_policy.py",
    "host_brain/agent_runtime/review_store.py",
    "host_brain/agent_runtime/review_token_contract.py",
    "host_brain/agent_runtime/task_graph.py",
    "host_brain/agent_runtime/task_store.py",
    "host_brain/agent_runtime/task_scheduler.py",
    "host_brain/agent_runtime/verification_contract.py",
    "host_brain/agent_runtime/work_order.py",
    "host_brain/app/graph_config_llm_runtime.py",
    "host_brain/app/executive_runtime_core.py",
    "host_brain/app/http_hooks_runtime_helpers.py",
    "host_brain/app/handlers/pentest_attack_agent_handler.py",
    "host_brain/app/pentest_event_bus.py",
    "host_brain/app/plan_graph.py",
    "host_brain/app/memory_event_store.py",
    "host_brain/app/obs_resource_guard.py",
    "host_brain/app/prompt_template_defs.py",
    "host_brain/app/prompt_template_store.py",
    "host_brain/app/provider_factory.py",
    "host_brain/app/skill_dag_planner.py",
    "host_brain/channels/edge_rpc_client.py",
    "host_brain/conversation/store.py",
    "host_brain/digital_life/contracts.py",
    "host_brain/eventing/gateway_transport.py",
    "host_brain/integrations/graph_evidence.py",
    "host_brain/observability/command_relocation.py",
    "host_brain/persistence/life_runtime_repository.py",
    "host_brain/persona/inner_world.py",
    "host_brain/pentest/agent/compat.py",
    "host_brain/pentest/agent/vector_store.py",
    "host_brain/pentest/run_store.py",
    "host_brain/pentest/secure_ingress_authority.py",
    "host_brain/release/manifest_generators.py",
    "host_brain/runtime/autonomous_self.py",
    "host_brain/runtime/life_continuity.py",
    "host_brain/runtime/life_organs.py",
    "host_brain/secure_ingress/store.py",
    "host_brain/skills/supply_chain_store.py",
    "host_brain/validation/digital_life_supervisor.py",
    "scripts/pentest/_show_prompt.py",
    "scripts/pentest/chat_attack_domain_takeover.py",
    "scripts/pentest/chat_verify.py",
    "scripts/pentest/chat_verify_bg.py",
    "scripts/pentest/chat_verify_minimal.py",
    "scripts/pentest/final_writeup_gen.py",
    "scripts/pentest/run_autonomous_redteam_lab_probe.py",
    "scripts/pentest/run_docker_vulnerable_lab_e2e_probe.py",
    "scripts/pentest/run_domain_cognitive.py",
    "scripts/pentest/run_domain_lab_probe.py",
    "scripts/pentest/run_domain_takeover_real.py",
    "scripts/pentest/run_phase3_authenticated_workflow_probe.py",
    "scripts/pentest/run_phase3_route_workflow_probe.py",
    "scripts/pentest/run_phase3_state_machine_workflow_probe.py",
    "scripts/pentest/run_readonly_probe.py",
)


def scan_production_usage(
    root: str | Path,
    *,
    facades: Sequence[CompatibilityFacade] = ACTIVE_COMPATIBILITY_FACADES,
    production_roots: Sequence[str] = ("host_brain", "edge_client", "natsume_contracts"),
) -> tuple[CompatibilityUsage, ...]:
    """Return AST import usage for every registered facade.

    Tests, docs and generated release output are deliberately outside the
    production roots.  Syntax errors fail closed and are reported as a usage
    row rather than silently skipped.
    """

    root_path = Path(root).resolve()
    facade_by_module = {entry.module: entry for entry in facades}
    if not facade_by_module:
        return ()
    usages: list[CompatibilityUsage] = []
    for relative_root in production_roots:
        source_root = root_path / relative_root
        if not source_root.is_dir():
            continue
        for source_path in sorted(source_root.rglob("*.py")):
            module_name = _module_name(root_path, source_path)
            if module_name in facade_by_module:
                continue
            try:
                tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), filename=str(source_path))
            except SyntaxError as exc:
                usages.append(
                    CompatibilityUsage(
                        facade_module="<syntax-error>",
                        source_path=source_path.relative_to(root_path).as_posix(),
                        symbol=str(exc.msg),
                        line=int(exc.lineno or 0),
                    )
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        entry = facade_by_module.get(alias.name)
                        if entry is not None:
                            usages.append(_usage(root_path, source_path, entry.module, "*", node.lineno))
                elif isinstance(node, ast.ImportFrom):
                    imported_module = _resolve_import_module(module_name, node)
                    entry = facade_by_module.get(imported_module)
                    if entry is None:
                        continue
                    for alias in node.names:
                        if not entry.symbols or alias.name in entry.symbols or alias.name == "*":
                            usages.append(
                                _usage(root_path, source_path, entry.module, alias.name, node.lineno)
                            )
    return tuple(usages)


def validate_registry(
    root: str | Path,
    *,
    facades: Sequence[CompatibilityFacade] = ACTIVE_COMPATIBILITY_FACADES,
    require_empty: bool = False,
) -> dict[str, object]:
    usages = scan_production_usage(root, facades=facades)
    issues: list[str] = []
    if require_empty and facades:
        issues.append(f"active compatibility registry is not empty: {len(facades)}")
    for usage in usages:
        issues.append(
            f"{usage.source_path}:{usage.line} imports {usage.facade_module}:{usage.symbol}"
        )
    return {
        "schema_version": COMPATIBILITY_REGISTRY_SCHEMA_VERSION,
        "status": "PASS" if not issues else "FAIL",
        "active_count": len(facades),
        "usage_count": len(usages),
        "issues": issues,
    }


def validate_retired_paths_absent(
    root: str | Path,
    retired_paths: Iterable[str],
) -> dict[str, object]:
    root_path = Path(root).resolve()
    remaining = [
        str(path).replace("\\", "/")
        for path in retired_paths
        if (root_path / str(path)).exists()
    ]
    return {
        "schema_version": COMPATIBILITY_REGISTRY_SCHEMA_VERSION,
        "status": "PASS" if not remaining else "FAIL",
        "remaining_paths": sorted(remaining),
    }


def _module_name(root: Path, source_path: Path) -> str:
    relative = source_path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import_module(current_module: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return str(node.module or "")
    package = current_module.split(".")[:-1]
    keep = max(0, len(package) - node.level + 1)
    prefix = package[:keep]
    suffix = str(node.module or "").split(".") if node.module else []
    return ".".join([*prefix, *suffix])


def _usage(root: Path, source_path: Path, module: str, symbol: str, line: int) -> CompatibilityUsage:
    return CompatibilityUsage(
        facade_module=module,
        source_path=source_path.relative_to(root).as_posix(),
        symbol=symbol,
        line=int(line),
    )


__all__ = [
    "ACTIVE_COMPATIBILITY_FACADES",
    "COMPATIBILITY_REGISTRY_SCHEMA_VERSION",
    "RETIRED_CORE_PATHS",
    "CompatibilityFacade",
    "CompatibilityUsage",
    "scan_production_usage",
    "validate_registry",
    "validate_retired_paths_absent",
]
