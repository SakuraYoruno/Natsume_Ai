"""Run the NE-07 isolated Neo4j, Chroma, and Redis persistence acceptance.

The runner is intentionally acceptance-only.  It refuses the repository's
default service identities and ports, checks Docker Compose project labels,
reads the Neo4j password only from the process environment, and writes a
redacted receipt containing digests instead of probe payloads.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


SCHEMA_VERSION = "natsume.ne07-external-services-acceptance.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTITY_RE = re.compile(r"^natsume-(?:ne07|a15|dual)-[a-z0-9][a-z0-9_.-]*$")
_DEFAULT_PORTS = {6379, 7474, 17687, 18000}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _run_docker(*args: str, timeout: float = 60.0) -> str:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"docker command failed: {args[0]}")
    return completed.stdout.strip()


def _container_field(container: str, template: str) -> str:
    return _run_docker("inspect", "--format", template, container)


def _container_snapshot(container: str, expected_project: str) -> dict[str, str]:
    project = _container_field(
        container,
        '{{index .Config.Labels "com.docker.compose.project"}}',
    )
    if project != expected_project:
        raise RuntimeError(f"container project mismatch: {container}")
    return {
        "container_id": _container_field(container, "{{.Id}}"),
        "image_id": _container_field(container, "{{.Image}}"),
        "image_ref": _container_field(container, "{{.Config.Image}}"),
        "started_at": _container_field(container, "{{.State.StartedAt}}"),
        "health": _container_field(
            container,
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        ),
        "compose_project": project,
    }


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    description: str,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except BaseException as exc:  # noqa: BLE001 - bounded readiness polling
            last_error = exc
        time.sleep(1.0)
    suffix = f" ({type(last_error).__name__})" if last_error else ""
    raise TimeoutError(f"timed out waiting for {description}{suffix}")


def _wait_container_healthy(container: str, timeout: float) -> None:
    _wait_until(
        lambda: _container_field(
            container,
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        )
        == "healthy",
        timeout=timeout,
        description=f"healthy container {container}",
    )


def _restart_container(container: str, timeout: float) -> dict[str, str]:
    before = _container_field(container, "{{.State.StartedAt}}")
    _run_docker("restart", "--timeout", "30", container, timeout=timeout)
    _wait_container_healthy(container, timeout)
    after = _container_field(container, "{{.State.StartedAt}}")
    if before == after:
        raise RuntimeError(f"container did not restart: {container}")
    return {"before_started_at": before, "after_started_at": after}


@dataclass(frozen=True)
class AcceptanceConfig:
    commit_sha: str
    run_id: str
    project_name: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password_env: str
    neo4j_container: str
    chroma_endpoint: str
    chroma_container: str
    redis_url: str
    redis_container: str
    output: Path
    timeout_seconds: float

    def validate(self) -> None:
        if not _SHA_RE.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be a full lowercase Git SHA")
        identities = (
            self.project_name,
            self.neo4j_container,
            self.chroma_container,
            self.redis_container,
        )
        if any(not _IDENTITY_RE.fullmatch(value) for value in identities):
            raise ValueError(
                "all Docker identities must use an isolated natsume-ne07-* "
                "natsume-a15-* or natsume-dual-* prefix"
            )
        ports = {
            _endpoint_port(self.neo4j_uri, 7687),
            _endpoint_port(self.chroma_endpoint, 8000),
            _endpoint_port(self.redis_url, 6379),
        }
        if ports & _DEFAULT_PORTS:
            raise ValueError("acceptance runner refuses repository default service ports")
        for endpoint in (self.neo4j_uri, self.chroma_endpoint, self.redis_url):
            host = urlparse(endpoint).hostname
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("acceptance endpoints must be loopback-only")
        if self.timeout_seconds < 10 or self.timeout_seconds > 600:
            raise ValueError("timeout_seconds must be between 10 and 600")


def _endpoint_port(endpoint: str, default: int) -> int:
    parsed = urlparse(endpoint)
    return int(parsed.port or default)


def _neo4j_connectivity(config: AcceptanceConfig, password: str) -> bool:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, password),
        connection_timeout=5.0,
    )
    try:
        driver.verify_connectivity()
        return True
    finally:
        driver.close()


def _neo4j_write(config: AcceptanceConfig, password: str, probe_id: str) -> str:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, password),
    )
    try:
        with driver.session(database="neo4j") as session:
            row = session.run(
                """
                MERGE (probe:NE07AcceptanceProbe {
                    workspace_id: $workspace_id,
                    probe_id: $probe_id
                })
                SET probe.version = 1,
                    probe.value_digest = $value_digest
                RETURN probe.workspace_id AS workspace_id,
                       probe.probe_id AS probe_id,
                       probe.version AS version,
                       probe.value_digest AS value_digest
                """,
                workspace_id=config.project_name,
                probe_id=probe_id,
                value_digest=_digest({"service": "neo4j", "probe_id": probe_id}),
            ).single(strict=True)
            return _digest(dict(row))
    finally:
        driver.close()


def _neo4j_read_update_cleanup(
    config: AcceptanceConfig,
    password: str,
    probe_id: str,
) -> dict[str, str]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, password),
    )
    try:
        with driver.session(database="neo4j") as session:
            persisted = session.run(
                """
                MATCH (probe:NE07AcceptanceProbe {
                    workspace_id: $workspace_id,
                    probe_id: $probe_id
                })
                RETURN probe.version AS version,
                       probe.value_digest AS value_digest
                """,
                workspace_id=config.project_name,
                probe_id=probe_id,
            ).single(strict=True)
            if int(persisted["version"]) != 1:
                raise RuntimeError("Neo4j persisted version mismatch")
            updated = session.run(
                """
                MATCH (probe:NE07AcceptanceProbe {
                    workspace_id: $workspace_id,
                    probe_id: $probe_id
                })
                SET probe.version = 2
                RETURN probe.version AS version,
                       probe.value_digest AS value_digest
                """,
                workspace_id=config.project_name,
                probe_id=probe_id,
            ).single(strict=True)
            if int(updated["version"]) != 2:
                raise RuntimeError("Neo4j update was not visible")
            session.run(
                """
                MATCH (probe:NE07AcceptanceProbe {
                    workspace_id: $workspace_id,
                    probe_id: $probe_id
                })
                DELETE probe
                """,
                workspace_id=config.project_name,
                probe_id=probe_id,
            ).consume()
            remaining = session.run(
                """
                MATCH (probe:NE07AcceptanceProbe {
                    workspace_id: $workspace_id,
                    probe_id: $probe_id
                })
                RETURN count(probe) AS count
                """,
                workspace_id=config.project_name,
                probe_id=probe_id,
            ).single(strict=True)
            if int(remaining["count"]) != 0:
                raise RuntimeError("Neo4j acceptance namespace cleanup failed")
            return {
                "restart_read_digest": _digest(dict(persisted)),
                "update_digest": _digest(dict(updated)),
                "cleanup": "PASS",
            }
    finally:
        driver.close()


def _chroma_client(endpoint: str) -> Any:
    import chromadb
    from chromadb.config import Settings

    parsed = urlparse(endpoint)
    return chromadb.HttpClient(
        host=parsed.hostname or "127.0.0.1",
        port=_endpoint_port(endpoint, 8000),
        ssl=parsed.scheme == "https",
        settings=Settings(anonymized_telemetry=False),
    )


def _chroma_connectivity(config: AcceptanceConfig) -> bool:
    return bool(_chroma_client(config.chroma_endpoint).heartbeat())


def _chroma_write(config: AcceptanceConfig, collection_name: str, probe_id: str) -> str:
    client = _chroma_client(config.chroma_endpoint)
    collection = client.get_or_create_collection(name=collection_name)
    metadata = {"workspace_id": config.project_name, "version": 1}
    collection.upsert(
        ids=[probe_id],
        embeddings=[[0.125, 0.25, 0.5, 1.0]],
        documents=["NE-07 isolated Chroma acceptance probe"],
        metadatas=[metadata],
    )
    fetched = collection.get(ids=[probe_id], include=["metadatas"])
    if fetched.get("ids") != [probe_id]:
        raise RuntimeError("Chroma upsert was not readable")
    return _digest({"ids": fetched["ids"], "metadatas": fetched["metadatas"]})


def _chroma_read_query_cleanup(
    config: AcceptanceConfig,
    collection_name: str,
    probe_id: str,
) -> dict[str, str]:
    client = _chroma_client(config.chroma_endpoint)
    collection = client.get_collection(name=collection_name)
    fetched = collection.get(ids=[probe_id], include=["metadatas"])
    if fetched.get("ids") != [probe_id]:
        raise RuntimeError("Chroma restart read did not return the probe")
    queried = collection.query(
        query_embeddings=[[0.125, 0.25, 0.5, 1.0]],
        n_results=1,
        include=["metadatas", "distances"],
    )
    ids = queried.get("ids") or []
    if not ids or ids[0] != [probe_id]:
        raise RuntimeError("Chroma vector query did not return the probe")
    collection.update(ids=[probe_id], metadatas=[{"workspace_id": config.project_name, "version": 2}])
    updated = collection.get(ids=[probe_id], include=["metadatas"])
    metadata = (updated.get("metadatas") or [{}])[0] or {}
    if int(metadata.get("version", 0)) != 2:
        raise RuntimeError("Chroma update was not visible")
    client.delete_collection(name=collection_name)
    remaining = {item.name for item in client.list_collections()}
    if collection_name in remaining:
        raise RuntimeError("Chroma acceptance collection cleanup failed")
    return {
        "restart_read_digest": _digest(
            {"ids": fetched["ids"], "metadatas": fetched["metadatas"]}
        ),
        "query_digest": _digest({"ids": ids, "distances": queried.get("distances")}),
        "update_digest": _digest(
            {"ids": updated["ids"], "metadatas": updated["metadatas"]}
        ),
        "cleanup": "PASS",
    }


def _redis_client(url: str) -> Any:
    import redis

    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def _redis_connectivity(config: AcceptanceConfig) -> bool:
    return bool(_redis_client(config.redis_url).ping())


def _redis_write(config: AcceptanceConfig, key: str, lock_key: str, value: str) -> str:
    client = _redis_client(config.redis_url)
    if not client.set(key, value):
        raise RuntimeError("Redis SET failed")
    if not client.set(lock_key, "owner-a", nx=True, ex=300):
        raise RuntimeError("Redis lock acquisition failed")
    if client.set(lock_key, "owner-b", nx=True, ex=300):
        raise RuntimeError("Redis competing lock was not rejected")
    if client.get(key) != value:
        raise RuntimeError("Redis GET mismatch before restart")
    return _digest({"key": key, "value": value, "lock": "owner-a"})


def _redis_read_cleanup(
    config: AcceptanceConfig,
    key: str,
    lock_key: str,
    value: str,
) -> dict[str, str]:
    client = _redis_client(config.redis_url)
    persisted = client.get(key)
    lock_owner = client.get(lock_key)
    if persisted != value or lock_owner != "owner-a":
        raise RuntimeError("Redis restart persistence mismatch")
    client.delete(key, lock_key)
    if client.exists(key, lock_key):
        raise RuntimeError("Redis acceptance key cleanup failed")
    return {
        "restart_read_digest": _digest(
            {"key": key, "value": persisted, "lock": lock_owner}
        ),
        "cleanup": "PASS",
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(config: AcceptanceConfig) -> dict[str, Any]:
    config.validate()
    password = os.environ.get(config.neo4j_password_env, "")
    if not password:
        raise RuntimeError("Neo4j acceptance password environment variable is missing")

    started = time.monotonic()
    started_at = _utc_now()
    probe_token = uuid.uuid4().hex
    probe_id = f"probe-{probe_token}"
    collection_name = f"ne07-{probe_token}"
    redis_key = f"{config.project_name}:probe:{probe_token}"
    redis_lock_key = f"{redis_key}:lock"
    redis_value = uuid.uuid4().hex

    containers = {
        "neo4j": _container_snapshot(config.neo4j_container, config.project_name),
        "chroma": _container_snapshot(config.chroma_container, config.project_name),
        "redis": _container_snapshot(config.redis_container, config.project_name),
    }
    for name, container in (
        ("neo4j", config.neo4j_container),
        ("chroma", config.chroma_container),
        ("redis", config.redis_container),
    ):
        _wait_container_healthy(container, config.timeout_seconds)
        containers[name]["health"] = "healthy"

    _wait_until(
        lambda: _neo4j_connectivity(config, password),
        timeout=config.timeout_seconds,
        description="Neo4j connectivity",
    )
    _wait_until(
        lambda: _chroma_connectivity(config),
        timeout=config.timeout_seconds,
        description="Chroma heartbeat",
    )
    _wait_until(
        lambda: _redis_connectivity(config),
        timeout=config.timeout_seconds,
        description="Redis PING",
    )

    neo4j_write_digest = _neo4j_write(config, password, probe_id)
    chroma_write_digest = _chroma_write(config, collection_name, probe_id)
    redis_write_digest = _redis_write(config, redis_key, redis_lock_key, redis_value)

    neo4j_restart = _restart_container(config.neo4j_container, config.timeout_seconds)
    _wait_until(
        lambda: _neo4j_connectivity(config, password),
        timeout=config.timeout_seconds,
        description="Neo4j connectivity after restart",
    )
    neo4j_result = _neo4j_read_update_cleanup(config, password, probe_id)

    chroma_restart = _restart_container(config.chroma_container, config.timeout_seconds)
    _wait_until(
        lambda: _chroma_connectivity(config),
        timeout=config.timeout_seconds,
        description="Chroma heartbeat after restart",
    )
    chroma_result = _chroma_read_query_cleanup(config, collection_name, probe_id)

    redis_restart = _restart_container(config.redis_container, config.timeout_seconds)
    _wait_until(
        lambda: _redis_connectivity(config),
        timeout=config.timeout_seconds,
        description="Redis PING after restart",
    )
    redis_result = _redis_read_cleanup(
        config,
        redis_key,
        redis_lock_key,
        redis_value,
    )

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "commit_sha": config.commit_sha,
        "run_id": config.run_id,
        "project_name": config.project_name,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "docker_version": _run_docker(
            "version",
            "--format",
            "{{.Client.Version}}/{{.Server.Version}}",
        ),
        "client_versions": {
            package: importlib.metadata.version(package)
            for package in ("neo4j", "chromadb", "redis")
        },
        "probe_identity_digest": _digest(
            {
                "probe_id": probe_id,
                "collection_name": collection_name,
                "redis_key": redis_key,
            }
        ),
        "services": {
            "neo4j": {
                **containers["neo4j"],
                "port": _endpoint_port(config.neo4j_uri, 7687),
                "initial_write_digest": neo4j_write_digest,
                "restart": neo4j_restart,
                **neo4j_result,
                "status": "PASS",
            },
            "chroma": {
                **containers["chroma"],
                "port": _endpoint_port(config.chroma_endpoint, 8000),
                "initial_write_digest": chroma_write_digest,
                "restart": chroma_restart,
                **chroma_result,
                "status": "PASS",
            },
            "redis": {
                **containers["redis"],
                "port": _endpoint_port(config.redis_url, 6379),
                "initial_write_digest": redis_write_digest,
                "restart": redis_restart,
                **redis_result,
                "status": "PASS",
            },
        },
        "secret_material": "ABSENT",
        "user_default_stack": "EXCLUDED_UNTOUCHED",
    }
    receipt["receipt_digest"] = _digest(receipt)
    _write_receipt(config.output, receipt)
    return receipt


def _parse_args(argv: list[str]) -> AcceptanceConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--neo4j-uri", required=True)
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument(
        "--neo4j-password-env",
        default="NATSUME_NEO4J_PASSWORD",
    )
    parser.add_argument("--neo4j-container", required=True)
    parser.add_argument("--chroma-endpoint", required=True)
    parser.add_argument("--chroma-container", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--redis-container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args(argv)
    return AcceptanceConfig(
        commit_sha=args.commit_sha,
        run_id=args.run_id,
        project_name=args.project_name,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password_env=args.neo4j_password_env,
        neo4j_container=args.neo4j_container,
        chroma_endpoint=args.chroma_endpoint,
        chroma_container=args.chroma_container,
        redis_url=args.redis_url,
        redis_container=args.redis_container,
        output=args.output,
        timeout_seconds=args.timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    config = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        receipt = run(config)
    except BaseException as exc:  # noqa: BLE001 - CLI must emit a bounded failure
        failure = {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "commit_sha": config.commit_sha,
            "run_id": config.run_id,
            "project_name": config.project_name,
            "finished_at": _utc_now(),
            "error_type": type(exc).__name__,
            "secret_material": "ABSENT",
        }
        failure["receipt_digest"] = _digest(failure)
        _write_receipt(config.output, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 1
    summary = {
        "schema_version": receipt["schema_version"],
        "status": receipt["status"],
        "run_id": receipt["run_id"],
        "receipt_digest": receipt["receipt_digest"],
        "output": str(config.output),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
