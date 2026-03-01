"""Generate external dependency readiness report from local runtime configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edge_client.config import build_edge_runtime_statuses, load_edge_config
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.main import build_env_readiness_report
from host_brain.bond.neo4j_client import (
    build_neo4j_dependency_status,
    build_neo4j_probe_report,
    load_neo4j_runtime_config,
)
from host_brain.core.ids import new_request_id, now_ms
from host_brain.integrations.graph_runtime import GraphRuntimeContract, validate_graph_config
from host_brain.memory.chroma_client import (
    build_chroma_dependency_status,
    build_chroma_probe_report,
    load_chroma_runtime_config,
)
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    build_embedding_provider_registry,
    load_embedding_runtime_config,
)
from host_brain.retrieval.reranker import (
    RerankerServiceContract,
    build_reranker_provider_registry,
    load_reranker_runtime_config,
)
from tools.replay.production_drill import run_drill_suite


ROOT = REPO_ROOT
DEFAULT_ENV_FILE = ROOT / "configs" / "runtime" / "local_services.env"
DEFAULT_ENV_FALLBACK_FILE = ROOT / "configs" / "runtime" / "local_services.env.example"
DEFAULT_NODE_INVENTORY = ROOT / "configs" / "deploy" / "node_inventory_9.json"
DEFAULT_KEY_PROFILES = ROOT / "configs" / "secrets" / "key_profiles.runtime.json"
DEFAULT_KEY_PROFILES_FALLBACK = ROOT / "configs" / "secrets" / "key_profiles.example.json"
DEFAULT_SECRET_STORE = ROOT / "configs" / "secrets" / "secret_store.runtime.local.json"
DEFAULT_SECRET_STORE_FALLBACK = ROOT / "configs" / "secrets" / "secret_store.runtime.example.json"
DEFAULT_GRAPH_CONFIG = ROOT / "tests" / "fixtures" / "orchestration" / "good" / "graph_main.json"
DEFAULT_OUTPUT = ROOT / "runtime" / "reports" / "env_readiness_report.json"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            values[key] = value
    return values


def _load_json(path: Path) -> dict[str, Any]:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"unable to decode json file: {path}")


def _normalize_runtime_env(*, env_file_values: Mapping[str, str]) -> dict[str, str]:
    merged = dict(env_file_values)
    merged.update({key: str(value) for key, value in os.environ.items()})
    return merged


def _resolve_ref_secret(env_values: dict[str, str], *, ref_key: str, value_key: str, secret_store: Mapping[str, Any]) -> None:
    if env_values.get(value_key):
        return
    secret_ref = str(env_values.get(ref_key) or "").strip()
    if not secret_ref:
        return
    secret_value = str(secret_store.get(secret_ref) or "").strip()
    if secret_value:
        env_values[value_key] = secret_value


def _probe_neo4j_live(*, env_values: Mapping[str, Any], secret_store: Mapping[str, Any]) -> dict[str, Any]:
    config_result = load_neo4j_runtime_config(env=env_values)
    if not config_result.success or config_result.config is None:
        detail = config_result.issues[0].detail if config_result.issues else "neo4j config invalid"
        return {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": detail,
        }

    config = config_result.config
    probe = {
        "reachable": False,
        "auth_ok": False,
        "schema_ok": True,
        "leader_ok": False,
        "detail": "neo4j probe not executed",
    }
    if not config.enabled:
        report = build_neo4j_probe_report(config=config, request_id=None, probe=probe, ts_ms=now_ms())
        return build_neo4j_dependency_status(report)

    auth_ref = str(config.auth_ref or "").strip()
    secret_raw = str(secret_store.get(auth_ref) or "").strip()
    if not secret_raw:
        probe["detail"] = f"secret missing for auth ref: {auth_ref}"
        report = build_neo4j_probe_report(config=config, request_id=None, probe=probe, ts_ms=now_ms())
        return build_neo4j_dependency_status(report)

    if ":" in secret_raw:
        username, password = secret_raw.split(":", 1)
    else:
        username, password = "neo4j", secret_raw

    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(config.uri, auth=(username, password))
        with driver.session(database=config.database) as session:
            session.run("RETURN 1 AS ok").single()
        driver.close()
        probe = {
            "reachable": True,
            "auth_ok": True,
            "schema_ok": True,
            "leader_ok": True,
            "detail": "neo4j query RETURN 1 succeeded",
        }
    except Exception as exc:
        probe["detail"] = str(exc)
    report = build_neo4j_probe_report(config=config, request_id=None, probe=probe, ts_ms=now_ms())
    return build_neo4j_dependency_status(report)


def _probe_chroma_live(*, env_values: Mapping[str, Any]) -> dict[str, Any]:
    config_result = load_chroma_runtime_config(env=env_values)
    if not config_result.success or config_result.config is None:
        detail = config_result.issues[0].detail if config_result.issues else "chroma config invalid"
        return {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": detail,
        }

    config = config_result.config
    probe = {
        "reachable": False,
        "detail": "chroma probe not executed",
    }
    if not config.enabled:
        report = build_chroma_probe_report(config=config, request_id=None, probe=probe, ts_ms=now_ms())
        return build_chroma_dependency_status(report)

    endpoint = str(config.endpoint or "http://127.0.0.1:8000").strip()
    parsed = urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = (parsed.scheme.lower() == "https")
    try:
        import chromadb

        client = chromadb.HttpClient(host=host, port=port, ssl=ssl)
        client.list_collections()
        probe["reachable"] = True
        probe["detail"] = "chroma list_collections succeeded"
    except Exception as exc:
        probe["detail"] = str(exc)

    report = build_chroma_probe_report(config=config, request_id=None, probe=probe, ts_ms=now_ms())
    return build_chroma_dependency_status(report)


def _probe_embedding_live(*, env_values: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    config_result = load_embedding_runtime_config(env=env_values)
    if not config_result.success or config_result.config is None:
        detail = config_result.issues[0].detail if config_result.issues else "embedding config invalid"
        return {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": detail,
        }
    registry = build_embedding_provider_registry(env=env_values)
    service = EmbeddingServiceContract(
        runtime_config=config_result.config,
        provider_registry=registry,
        node_id=node_id,
        available=True,
    )
    result = service.embed(request_id="req_probe_embed", text="readiness probe text", intent_id="probe.intent")
    if result.status == "OK":
        return {"available": True, "detail": "embedding dependency healthy"}
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": f"{result.reason_code}: {result.detail}",
    }


def _probe_reranker_live(*, env_values: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    config_result = load_reranker_runtime_config(env=env_values)
    if not config_result.success or config_result.config is None:
        detail = config_result.issues[0].detail if config_result.issues else "reranker config invalid"
        return {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": detail,
        }
    registry = build_reranker_provider_registry(env=env_values)
    service = RerankerServiceContract(
        runtime_config=config_result.config,
        provider_registry=registry,
        node_id=node_id,
        available=True,
    )
    result = service.rerank(
        request_id="req_probe_rerank",
        query="readiness probe",
        candidates=[
            {
                "doc_id": "probe_doc_001",
                "source_ref": "source://probe/001",
                "snippet": "readiness probe snippet",
                "metadata": {},
            }
        ],
        intent_id="probe.intent",
    )
    if result.status == "OK":
        return {"available": True, "detail": "reranker dependency healthy"}
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": f"{result.reason_code}: {result.detail}",
    }


def _build_graph_runtime_status(
    *,
    graph_config: Mapping[str, Any],
    llm_available: bool,
    retrieval_available: bool,
    edge_gateway_available: bool,
) -> dict[str, Any]:
    validation = validate_graph_config(graph_config)
    if validation.get("status") == "FAILED":
        return {
            "status": "FAILED",
            "reason_code": str(validation.get("reason_code") or ""),
            "detail": str(validation.get("detail") or "graph config invalid"),
        }

    runtime = GraphRuntimeContract()
    node_bindings = graph_config.get("node_bindings") or []
    for node in node_bindings:
        handler_id = str(node.get("handler_id") or "").strip()
        if not handler_id:
            continue
        runtime.register_node_handler(handler_id, lambda payload, _ctx=None: dict(payload))

    dependency_statuses = {
        "plan": {
            "available": bool(llm_available),
            "detail": "llm gateway unavailable" if not llm_available else "llm gateway ok",
        },
        "retrieve": {
            "available": bool(retrieval_available),
            "detail": "retrieval dependency unavailable" if not retrieval_available else "retrieval dependency ok",
        },
        "respond": {
            "available": bool(edge_gateway_available),
            "detail": "edge gateway unavailable" if not edge_gateway_available else "edge gateway ok",
        },
    }
    result = runtime.run_graph(
        request_id="req_graph_readiness_probe",
        graph_config=graph_config,
        payload={"probe": True},
        dependency_statuses=dependency_statuses,
    )
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "detail": result.detail,
    }


def _build_drill_summary_rows() -> list[dict[str, Any]]:
    fixture_paths = [
        Path("tests/fixtures/drill/good/cold_start_no_key.json"),
        Path("tests/fixtures/drill/good/provider_down.json"),
        Path("tests/fixtures/drill/bad/rollback_conflict.json"),
    ]
    suite = run_drill_suite(root=ROOT, scenario_fixtures=fixture_paths, ts_ms=now_ms())
    rows: list[dict[str, Any]] = []
    for row in suite.get("scenarios") or []:
        rows.append(
            {
                "scenario_id": str(row.get("scenario_id") or ""),
                "status": str(row.get("status") or ""),
                "reason_code_chain": list(row.get("reason_code_chain") or []),
                "replay_ref": str(row.get("replay_ref") or ""),
            }
        )
    return rows


def _build_dependency_probe_matrix(
    *,
    env_values: Mapping[str, Any],
    secret_store: Mapping[str, Any],
    host_node_id: str,
) -> dict[str, dict[str, Any]]:
    probes: dict[str, dict[str, Any]] = {}
    probes["host.bond.neo4j"] = _probe_neo4j_live(env_values=env_values, secret_store=secret_store)
    probes["host.memory.chroma"] = _probe_chroma_live(env_values=env_values)
    probes["host.retrieval.embedding"] = _probe_embedding_live(env_values=env_values, node_id=host_node_id)
    probes["host.retrieval.reranker"] = _probe_reranker_live(env_values=env_values, node_id=host_node_id)
    return probes


def build_runtime_env_readiness_report(
    *,
    env_values: Mapping[str, Any],
    node_inventory_payload: Mapping[str, Any],
    key_profiles_payload: Mapping[str, Any],
    secret_store: Mapping[str, Any],
    graph_payload: Mapping[str, Any],
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    runtime_env = dict(env_values)
    profiles = list(key_profiles_payload.get("profiles") or [])
    host_node_id = str(key_profiles_payload.get("host_node_id") or "natsu-node-01").strip() or "natsu-node-01"
    edge_node_id = str(key_profiles_payload.get("edge_node_id") or "natsu-node-06").strip() or "natsu-node-06"
    current_epoch = int(key_profiles_payload.get("current_epoch") or 0)

    _resolve_ref_secret(runtime_env, ref_key="OPENAI_API_KEY_REF", value_key="OPENAI_API_KEY", secret_store=secret_store)
    _resolve_ref_secret(runtime_env, ref_key="GEMINI_API_KEY_REF", value_key="GEMINI_API_KEY", secret_store=secret_store)
    _resolve_ref_secret(runtime_env, ref_key="DEEPSEEK_API_KEY_REF", value_key="DEEPSEEK_API_KEY", secret_store=secret_store)
    _resolve_ref_secret(runtime_env, ref_key="EDGE_HMAC_SHARED_KEY_REF", value_key="EDGE_HMAC_SHARED_KEY", secret_store=secret_store)

    host_result = load_host_config(
        env=runtime_env,
        key_profiles=profiles,
        secret_store=secret_store,
        node_id=host_node_id,
        current_epoch=current_epoch,
    )
    edge_result = load_edge_config(
        env=runtime_env,
        key_profiles=profiles,
        secret_store=secret_store,
        node_id=edge_node_id,
        current_epoch=current_epoch,
    )
    startup_runtime_statuses: dict[str, Any] = {}
    if host_result.success and host_result.config is not None:
        startup_runtime_statuses.update(build_host_runtime_statuses(host_result.config))
    if edge_result.success and edge_result.config is not None:
        startup_runtime_statuses.update(build_edge_runtime_statuses(edge_result.config))

    dependency_probes = _build_dependency_probe_matrix(
        env_values=runtime_env,
        secret_store=secret_store,
        host_node_id=host_node_id,
    )

    llm_available = bool(startup_runtime_statuses.get("host.integrations.llm_gateway", {}).get("available", True))
    edge_gateway_available = bool(startup_runtime_statuses.get("edge.rpc.gateway", {}).get("available", True))
    retrieval_available = bool(dependency_probes.get("host.retrieval.embedding", {}).get("available", False)) and bool(
        dependency_probes.get("host.retrieval.reranker", {}).get("available", False)
    )
    graph_config = dict(graph_payload.get("graph") or graph_payload)
    graph_runtime = _build_graph_runtime_status(
        graph_config=graph_config,
        llm_available=llm_available,
        retrieval_available=retrieval_available,
        edge_gateway_available=edge_gateway_available,
    )
    drill_summaries = _build_drill_summary_rows()

    nodes = list(node_inventory_payload.get("nodes") or [])
    expected_node_count = int(node_inventory_payload.get("expected_node_count") or len(nodes))
    return build_env_readiness_report(
        env=str(runtime_env.get("NATSUME_ENV") or "dev"),
        node_inventory=nodes,
        expected_node_count=expected_node_count,
        key_profiles=profiles,
        graph_runtime=graph_runtime,
        drill_summaries=drill_summaries,
        host_env=runtime_env,
        edge_env=runtime_env,
        runtime_statuses=startup_runtime_statuses,
        dependency_probes=dependency_probes,
        request_id=request_id or new_request_id(),
        ts_ms=ts_ms,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate env readiness integration report.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Runtime env file path.")
    parser.add_argument("--node-inventory", default=str(DEFAULT_NODE_INVENTORY), help="Node inventory JSON path.")
    parser.add_argument("--key-profiles", default=str(DEFAULT_KEY_PROFILES), help="Key profiles JSON path.")
    parser.add_argument("--secret-store", default=str(DEFAULT_SECRET_STORE), help="Secret store JSON path.")
    parser.add_argument("--graph-config", default=str(DEFAULT_GRAPH_CONFIG), help="Graph config JSON path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output report JSON path.")
    parser.add_argument("--request-id", default="", help="Optional request id.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = Path(args.env_file)
    node_inventory_file = Path(args.node_inventory)
    key_profiles_file = Path(args.key_profiles)
    secret_store_file = Path(args.secret_store)
    graph_file = Path(args.graph_config)
    output_file = Path(args.output)

    if not env_file.exists() and DEFAULT_ENV_FALLBACK_FILE.exists():
        env_file = DEFAULT_ENV_FALLBACK_FILE
    if not key_profiles_file.exists() and DEFAULT_KEY_PROFILES_FALLBACK.exists():
        key_profiles_file = DEFAULT_KEY_PROFILES_FALLBACK
    if not secret_store_file.exists() and DEFAULT_SECRET_STORE_FALLBACK.exists():
        secret_store_file = DEFAULT_SECRET_STORE_FALLBACK

    env_values = _normalize_runtime_env(env_file_values=_read_env_file(env_file))
    node_inventory_payload = _load_json(node_inventory_file)
    key_profiles_payload = _load_json(key_profiles_file)
    secret_store_payload = _load_json(secret_store_file)
    graph_payload = _load_json(graph_file)

    report = build_runtime_env_readiness_report(
        env_values=env_values,
        node_inventory_payload=node_inventory_payload,
        key_profiles_payload=key_profiles_payload,
        secret_store=secret_store_payload,
        graph_payload=graph_payload,
        request_id=(str(args.request_id).strip() or None),
        ts_ms=now_ms(),
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_file),
                "request_id": report.get("request_id"),
                "stage": report.get("stage"),
                "env": report.get("env"),
                "readiness_status": report.get("readiness_status"),
                "reason_code": report.get("reason_code"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
