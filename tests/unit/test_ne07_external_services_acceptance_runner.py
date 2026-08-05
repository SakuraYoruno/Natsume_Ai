"""Contracts for the isolated NE-07 external-service acceptance runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/runtime/run_ne07_external_services_acceptance.py"
SPEC = importlib.util.spec_from_file_location("ne07_external_acceptance", RUNNER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _config(tmp_path: Path, **overrides: object) -> object:
    values: dict[str, object] = {
        "commit_sha": "a" * 40,
        "run_id": "ne07-test",
        "project_name": "natsume-ne07-test",
        "neo4j_uri": "bolt://127.0.0.1:27687",
        "neo4j_user": "neo4j",
        "neo4j_password_env": "NATSUME_NEO4J_PASSWORD",
        "neo4j_container": "natsume-ne07-test-neo4j",
        "chroma_endpoint": "http://127.0.0.1:28000",
        "chroma_container": "natsume-ne07-test-chroma",
        "redis_url": "redis://127.0.0.1:26379/0",
        "redis_container": "natsume-ne07-test-redis",
        "output": tmp_path / "receipt.json",
        "timeout_seconds": 30.0,
    }
    values.update(overrides)
    return MODULE.AcceptanceConfig(**values)


def test_runner_accepts_only_isolated_loopback_resources(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.validate()

    _config(
        tmp_path,
        project_name="natsume-dual-test",
        neo4j_container="natsume-dual-test-neo4j",
        chroma_container="natsume-dual-test-chroma",
        redis_container="natsume-dual-test-redis",
    ).validate()

    with pytest.raises(ValueError, match="natsume-ne07"):
        _config(tmp_path, neo4j_container="natsume-neo4j").validate()
    with pytest.raises(ValueError, match="default service ports"):
        _config(tmp_path, redis_url="redis://127.0.0.1:6379/0").validate()
    with pytest.raises(ValueError, match="loopback"):
        _config(tmp_path, chroma_endpoint="http://chroma.example:28000").validate()


def test_digest_is_canonical_and_never_contains_probe_payload() -> None:
    left = MODULE._digest({"b": 2, "a": 1})
    right = MODULE._digest({"a": 1, "b": 2})

    assert left == right
    assert left.startswith("sha256:")
    assert '"a"' not in left


def test_runner_source_has_no_secret_cli_or_full_docker_inspect() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert 'parser.add_argument("--neo4j-password",' not in source
    assert "--neo4j-password-env" in source
    assert '"docker", "inspect"' not in source
    assert "secret_material" in source
    assert "EXCLUDED_UNTOUCHED" in source
