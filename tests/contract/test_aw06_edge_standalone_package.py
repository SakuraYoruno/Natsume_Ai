from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
EDGE_ROOT = ROOT / "edge_client"
CONTRACT_ROOT = ROOT / "natsume_contracts"

FORMER_HOST_COUPLED_MODULES = (
    "edge_client.config",
    "edge_client.actuators.speech",
    "edge_client.actuators.tts_engine",
    "edge_client.actuators.vts_driver",
    "edge_client.actuators.vts_expression_map",
    "edge_client.app.fastapi_app",
    "edge_client.app.schemas",
    "edge_client.channels.input_reporter",
    "edge_client.gateway.bootstrap",
    "edge_client.proofs.resource_gate",
    "edge_client.rpc.auth",
    "edge_client.rpc.event_reporter",
    "edge_client.rpc.resource_gate",
    "edge_client.rpc.voice",
    "edge_client.runtime.emotion_inference",
    "edge_client.runtime.health",
    "edge_client.runtime.voice_pipeline",
    "edge_client.senses.asr",
    "edge_client.tribe.edge_tribe_bridge",
    "edge_client.tribe.somatic_agent",
)


def test_edge_source_has_zero_host_implementation_imports() -> None:
    findings: list[tuple[str, int, str]] = []
    for path in EDGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("host_brain"):
                findings.append((str(path.relative_to(ROOT)), node.lineno, str(node.module)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("host_brain"):
                        findings.append((str(path.relative_to(ROOT)), node.lineno, alias.name))
    assert findings == []


def test_edge_and_contract_projects_declare_independent_package_units() -> None:
    edge_project = tomllib.loads((EDGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contract_project = tomllib.loads((CONTRACT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert edge_project["project"]["name"] == "edge-client"
    assert any(
        dependency.startswith("natsume-contracts>=")
        for dependency in edge_project["project"]["dependencies"]
    )
    assert "edge_client.gateway" in edge_project["tool"]["setuptools"]["packages"]
    assert edge_project["tool"]["setuptools"]["package-dir"] == {"edge_client": "."}
    assert contract_project["project"]["name"] == "natsume-contracts"
    assert contract_project["project"]["dependencies"] == []
    assert "natsume_contracts.edge" in contract_project["tool"]["setuptools"]["packages"]


def test_formerly_coupled_edge_modules_import_without_host_package(tmp_path: Path) -> None:
    isolated = tmp_path / "site"
    shutil.copytree(
        EDGE_ROOT,
        isolated / "edge_client",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"),
    )
    shutil.copytree(
        CONTRACT_ROOT,
        isolated / "natsume_contracts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"),
    )
    assert not (isolated / "host_brain").exists()
    script = f"""
import importlib, json, pathlib, sys
class RejectHost:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'host_brain' or fullname.startswith('host_brain.'):
            raise AssertionError('Edge attempted Host import: ' + fullname)
        return None
sys.meta_path.insert(0, RejectHost())
modules = json.loads({json.dumps(json.dumps(FORMER_HOST_COUPLED_MODULES))})
for name in modules:
    importlib.import_module(name)
edge = importlib.import_module('edge_client')
contracts = importlib.import_module('natsume_contracts.edge.v1')
root = pathlib.Path({str(isolated)!r}).resolve()
assert pathlib.Path(edge.__file__).resolve().is_relative_to(root)
assert pathlib.Path(contracts.__file__).resolve().is_relative_to(root)
assert not any(name == 'host_brain' or name.startswith('host_brain.') for name in sys.modules)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(isolated)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
