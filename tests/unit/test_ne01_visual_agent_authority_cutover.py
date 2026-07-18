from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROUTE_PATH = ROOT / "host_brain/app/agent_routes.py"
CHAT_ROUTE_PATH = ROOT / "host_brain/app/chat_routes.py"
CHAT_VISION_PATH = ROOT / "host_brain/app/chat_handler_vision.py"
ACTION_PLANNER_PATH = ROOT / "host_brain/app/vision/action_planner.py"
RAW_LOOP_PATH = ROOT / "host_brain/app/vision/agent_loop.py"
RAW_EDGE_CLIENT_PATH = ROOT / "host_brain/app/vision/edge_rpc_client.py"
PRODUCTION_ROOT = ROOT / "host_brain"
FORBIDDEN_ROUTE_MARKERS = (
    "host_brain.composition.process_services",
    "host_brain.app.graph_config_builder",
    "host_brain.app.vision.agent_loop",
    "host_brain.app.vision.edge_rpc_client",
    "host_brain.channels.edge_transport",
    "get_ledger",
    "resolve_host_llm_runtime_async",
    "ClawdbotClient",
    "MockClawdbotClient",
    "EdgeRpcClient",
    "resolve_host_edge_rpc_shared_key",
    "CLAWDBOT_BASE_URL",
    "EDGE_RPC_BASE_URL",
    "run_agent_loop",
    "execute_plan",
)


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_visual_agent_route_has_no_nested_execution_dependencies() -> None:
    source = ROUTE_PATH.read_text(encoding="utf-8-sig")
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source


def test_visual_agent_route_cannot_call_legacy_loop_or_effect_executor() -> None:
    called_names = _called_names(ROUTE_PATH)
    assert "run_agent_loop" not in called_names
    assert "execute_plan" not in called_names
    assert "resolve_host_llm_runtime_async" not in called_names


def test_raw_visual_loop_and_effect_clients_are_physically_removed() -> None:
    assert not RAW_LOOP_PATH.exists()
    assert not RAW_EDGE_CLIENT_PATH.exists()


def test_legacy_visual_execution_symbols_have_zero_production_references() -> None:
    references: list[str] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module
                in {
                    "host_brain.app.vision.agent_loop",
                    "host_brain.app.vision.edge_rpc_client",
                }
                or any(
                    alias.name
                    in {
                        "run_agent_loop",
                        "ClawdbotClient",
                        "MockClawdbotClient",
                        "EdgeRpcClient",
                    }
                    for alias in node.names
                )
            ):
                references.append(path.relative_to(ROOT).as_posix())
            elif isinstance(node, ast.Import) and any(
                alias.name
                in {
                    "host_brain.app.vision.agent_loop",
                    "host_brain.app.vision.edge_rpc_client",
                }
                for alias in node.names
            ):
                references.append(path.relative_to(ROOT).as_posix())
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in {
                "run_agent_loop",
                "ClawdbotClient",
                "MockClawdbotClient",
                "EdgeRpcClient",
            }:
                references.append(path.relative_to(ROOT).as_posix())
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {
                "run_agent_loop",
                "ClawdbotClient",
                "MockClawdbotClient",
                "EdgeRpcClient",
            }:
                references.append(path.relative_to(ROOT).as_posix())
    assert references == []


def test_legacy_action_planner_is_sealed_and_has_no_process_model_locator() -> None:
    source = ACTION_PLANNER_PATH.read_text(encoding="utf-8-sig")
    for marker in (
        "host_brain.composition.process_services",
        "get_model_attempt_service",
        "get_prompt_registry",
        "ModelAttemptRequest",
        "VISION_ACTION_PLANNER_PROMPT_ID",
    ):
        assert marker not in source
    assert "raise RuntimeError(VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED)" in source


def test_vision_chat_actions_path_cannot_generate_or_commit_action_plan() -> None:
    source = CHAT_VISION_PATH.read_text(encoding="utf-8-sig")
    assert "generate_action_plan" not in source
    assert 'if response_format != "text"' in source
    assert '"decision": "REJECT"' in source
    assert "VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED" in source
    assert 'commit_payload["action_plan"]' not in source


def test_real_chat_route_rejects_non_text_before_chat_services() -> None:
    source = CHAT_ROUTE_PATH.read_text(encoding="utf-8-sig")
    gate_index = source.index('if response_format != "text"')
    assert gate_index < source.index("notify_user_interaction_fn()")
    assert gate_index < source.index("build_chat_bio_ops_preflight_trace(request_body)")
    assert gate_index < source.index("evaluate_immune_gate_fn(request_body)")
    assert gate_index < source.index("handle_chat_fn(")
    assert "VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED" in source


def test_historical_vision_helper_has_zero_production_callers() -> None:
    callers: list[str] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        if path == CHAT_VISION_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name == "_vision_ui_sse_generator":
                callers.append(path.relative_to(ROOT).as_posix())
    assert callers == []


def test_visual_agent_compatibility_routes_are_registered_once() -> None:
    registry = (ROOT / "host_brain/app/router_registry.py").read_text(
        encoding="utf-8-sig"
    )
    assert registry.count("app.include_router(agent_router)") == 1

    route_source = ROUTE_PATH.read_text(encoding="utf-8-sig")
    assert route_source.count('@router.post("/run")') == 1
    assert route_source.count('@router.get("/info"') == 1
