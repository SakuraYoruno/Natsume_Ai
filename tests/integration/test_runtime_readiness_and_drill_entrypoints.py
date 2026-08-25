import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENV_TOOL_PATH = ROOT / "scripts" / "runtime" / "generate_env_readiness_report.py"
DRILL_TOOL_PATH = ROOT / "tools" / "replay" / "production_drill.py"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_readiness_main_writes_report_and_summary(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module("generate_env_readiness_report_entry", ENV_TOOL_PATH)
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "\n".join(
            [
                "Natsume_ENV=dev",
                "HOST_LLM_PROVIDER=mock",
                "EDGE_RPC_GATEWAY_ENABLED=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    node_inventory_file = ROOT / "tests" / "fixtures" / "services" / "good" / "node_inventory_9.json"
    key_profiles_file = ROOT / "configs" / "secrets" / "key_profiles.example.json"
    secret_store_file = ROOT / "configs" / "secrets" / "secret_store.runtime.example.json"
    graph_file = ROOT / "tests" / "fixtures" / "orchestration" / "good" / "graph_main.json"
    output_file = tmp_path / "runtime" / "reports" / "env_readiness_report.json"
    captured: dict[str, object] = {}

    def _fake_build_runtime_env_readiness_report(**kwargs):
        captured.update(kwargs)
        return {
            "request_id": kwargs["request_id"],
            "stage": "S_ENV_READY",
            "env": kwargs["env_values"].get("Natsume_ENV"),
            "readiness_status": "READY",
            "reason_code": None,
            "ts_ms": kwargs["ts_ms"],
        }

    monkeypatch.setattr(module, "build_runtime_env_readiness_report", _fake_build_runtime_env_readiness_report)
    monkeypatch.setattr(module, "now_ms", lambda: 1_800_400_000_000)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_env_readiness_report.py",
            "--env-file",
            str(env_file),
            "--node-inventory",
            str(node_inventory_file),
            "--key-profiles",
            str(key_profiles_file),
            "--secret-store",
            str(secret_store_file),
            "--graph-config",
            str(graph_file),
            "--output",
            str(output_file),
            "--request-id",
            "req_env_entry_001",
        ],
    )

    assert module.main() == 0

    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    written_report = json.loads(output_file.read_text(encoding="utf-8"))

    assert summary == {
        "env": "dev",
        "output": str(output_file),
        "readiness_status": "READY",
        "reason_code": None,
        "request_id": "req_env_entry_001",
        "stage": "S_ENV_READY",
    }
    assert written_report["request_id"] == "req_env_entry_001"
    assert written_report["ts_ms"] == 1_800_400_000_000
    assert captured["env_values"]["Natsume_ENV"] == "dev"
    assert captured["node_inventory_payload"]["expected_node_count"] == 8
    assert captured["key_profiles_payload"]["profiles"][0]["provider"] == "gemini"
    assert captured["secret_store"]["sec://gemini/natsu-node-01"].startswith("sk-")
    assert captured["graph_payload"]["graph"]["graph_id"] == "graph.main.v1"


def test_env_readiness_main_uses_fallback_files_and_default_graph(tmp_path, monkeypatch, capsys) -> None:
    module = _load_module("generate_env_readiness_report_fallbacks", ENV_TOOL_PATH)
    env_fallback = tmp_path / ".env.example"
    env_fallback.write_text("Natsume_ENV=staging\nOPENAI_API_KEY_REF=sec://openai/demo\n", encoding="utf-8")
    key_profiles_fallback = tmp_path / "key_profiles.example.json"
    key_profiles_fallback.write_text(
        json.dumps(
            {
                "host_node_id": "natsu-node-01",
                "edge_node_id": "natsu-node-06",
                "current_epoch": 20260401,
                "profiles": [
                    {
                        "provider": "openai",
                        "node_id": "natsu-node-01",
                        "scope": "host.llm.gateway",
                        "secret_ref": "sec://openai/demo",
                        "rotation_epoch": 20270401,
                        "status": "active",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    secret_store_fallback = tmp_path / "secret_store.runtime.example.json"
    secret_store_fallback.write_text(
        json.dumps({"sec://openai/demo": "sk-demo-value"}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_file = tmp_path / "env_readiness_report.json"
    captured: dict[str, object] = {}

    def _fake_build_runtime_env_readiness_report(**kwargs):
        captured.update(kwargs)
        return {
            "request_id": "req_env_entry_002",
            "stage": "S_ENV_READY",
            "env": kwargs["env_values"].get("Natsume_ENV"),
            "readiness_status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
            "ts_ms": kwargs["ts_ms"],
        }

    monkeypatch.setattr(module, "build_runtime_env_readiness_report", _fake_build_runtime_env_readiness_report)
    monkeypatch.setattr(module, "DEFAULT_ENV_FALLBACK_FILE", env_fallback)
    monkeypatch.setattr(module, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_fallback)
    monkeypatch.setattr(module, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_fallback)
    monkeypatch.setattr(module, "now_ms", lambda: 1_800_400_000_001)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_env_readiness_report.py",
            "--env-file",
            str(tmp_path / "missing.env"),
            "--node-inventory",
            str(tmp_path / "missing_node_inventory.json"),
            "--key-profiles",
            str(tmp_path / "missing_key_profiles.json"),
            "--secret-store",
            str(tmp_path / "missing_secret_store.json"),
            "--graph-config",
            str(tmp_path / "missing_graph.json"),
            "--output",
            str(output_file),
        ],
    )

    assert module.main() == 0

    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    written_report = json.loads(output_file.read_text(encoding="utf-8"))

    assert summary["env"] == "staging"
    assert summary["readiness_status"] == "DEGRADED"
    assert summary["reason_code"] == "SERVICE_UNAVAILABLE_DEPENDENCY"
    assert written_report["request_id"] == "req_env_entry_002"
    assert captured["env_values"]["Natsume_ENV"] == "staging"
    assert captured["key_profiles_payload"]["profiles"][0]["provider"] == "openai"
    assert captured["secret_store"]["sec://openai/demo"] == "sk-demo-value"
    assert captured["node_inventory_payload"] == {"nodes": [], "expected_node_count": 0}
    assert captured["graph_payload"]["graph"]["graph_id"] == "env.readiness.default"
    assert captured["request_id"] is None


def test_production_drill_main_requires_repo_root_and_fixture(capsys) -> None:
    module = _load_module("production_drill_usage", DRILL_TOOL_PATH)

    assert module.main([]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == module.S_DRILL_SUITE
    assert payload["status"] == module.STATUS_FAILED
    assert payload["reason_code"] == module.DRILL_SCENARIO_INVALID


def test_production_drill_main_emits_suite_summary_for_real_fixtures(capsys) -> None:
    module = _load_module("production_drill_entry", DRILL_TOOL_PATH)

    exit_code = module.main(
        [
            str(ROOT),
            "tests/fixtures/drill/good/provider_down.json",
            "tests/fixtures/drill/bad/orch_param_drift.json",
        ]
    )

    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    scenario_ids = {row["scenario_id"] for row in payload["scenarios"]}

    assert payload["stage"] == module.S_DRILL_SUITE
    assert payload["status"] == module.STATUS_DEGRADED
    assert payload["reason_code"] == "SERVICE_UNAVAILABLE_DEPENDENCY"
    assert payload["summary"] == {
        "scenario_total": 2,
        "ok_count": 0,
        "degraded_count": 2,
        "failed_count": 0,
    }
    assert scenario_ids == {
        "drill.dependency.provider_down",
        "drill.orch.param_drift",
    }
