"""Local environment readiness check for Python deps and external services."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_FILE = ROOT / ".env"
LOCAL_ENV_FALLBACK_FILE = ROOT / ".env.example"
LOCAL_SECRET_STORE = ROOT / "configs" / "secrets" / "secret_store.runtime.local.json"
LOCAL_SECRET_STORE_FALLBACK = ROOT / "configs" / "secrets" / "secret_store.runtime.example.json"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            values[key] = value
    return values


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError:
            continue
    return {}


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _module_status(name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        return {
            "name": name,
            "installed": False,
            "version": None,
            "error": str(exc),
        }

    version = getattr(module, "__version__", None)
    if not version:
        try:
            version = importlib.metadata.version(name.replace("_", "-"))
        except Exception:
            version = "unknown"
    return {
        "name": name,
        "installed": True,
        "version": str(version),
        "error": None,
    }


def _probe_neo4j(env_values: dict[str, str], secret_store: dict[str, Any]) -> dict[str, Any]:
    enabled = _parse_bool(env_values.get("BOND_NEO4J_ENABLED"), True)
    if not enabled:
        return {
            "service": "neo4j",
            "enabled": False,
            "ok": True,
            "status": "skipped_disabled",
            "detail": "BOND_NEO4J_ENABLED=false",
        }

    uri = env_values.get("BOND_NEO4J_URI", "bolt://127.0.0.1:7687")
    database = env_values.get("BOND_NEO4J_DATABASE", "neo4j")
    auth_ref = env_values.get("BOND_NEO4J_AUTH_REF", "")
    secret_raw = str(secret_store.get(auth_ref, ""))

    if not secret_raw:
        return {
            "service": "neo4j",
            "enabled": True,
            "ok": False,
            "status": "missing_auth",
            "uri": uri,
            "database": database,
            "detail": f"secret missing for auth ref: {auth_ref}",
        }

    if ":" in secret_raw:
        user, password = secret_raw.split(":", 1)
    else:
        user, password = "neo4j", secret_raw

    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session(database=database) as session:
            ok = session.run("RETURN 1 AS ok").single()["ok"]
        driver.close()
        return {
            "service": "neo4j",
            "enabled": True,
            "ok": bool(ok == 1),
            "status": "up",
            "uri": uri,
            "database": database,
            "detail": "query RETURN 1 succeeded",
        }
    except Exception as exc:
        return {
            "service": "neo4j",
            "enabled": True,
            "ok": False,
            "status": "down",
            "uri": uri,
            "database": database,
            "detail": str(exc),
        }


def _probe_chroma(env_values: dict[str, str]) -> dict[str, Any]:
    enabled = _parse_bool(env_values.get("CHROMA_ENABLED"), True)
    if not enabled:
        return {
            "service": "chroma",
            "enabled": False,
            "ok": True,
            "status": "skipped_disabled",
            "detail": "CHROMA_ENABLED=false",
        }

    endpoint = env_values.get("CHROMA_ENDPOINT", "http://127.0.0.1:8000")
    parsed = urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)

    try:
        import chromadb

        client = chromadb.HttpClient(host=host, port=port)
        collections = client.list_collections()
        return {
            "service": "chroma",
            "enabled": True,
            "ok": True,
            "status": "up",
            "endpoint": endpoint,
            "collections": len(collections),
            "detail": "list_collections succeeded",
        }
    except Exception as exc:
        return {
            "service": "chroma",
            "enabled": True,
            "ok": False,
            "status": "down",
            "endpoint": endpoint,
            "detail": str(exc),
        }


def build_report() -> dict[str, Any]:
    env_values = _read_env_file(LOCAL_ENV_FILE)
    if not env_values:
        env_values = _read_env_file(LOCAL_ENV_FALLBACK_FILE)
    if not env_values:
        env_values = dict(os.environ)
    secret_store = _read_json_file(LOCAL_SECRET_STORE)
    if not secret_store:
        secret_store = _read_json_file(LOCAL_SECRET_STORE_FALLBACK)

    package_names = [
        "pytest",
        "neo4j",
        "chromadb",
        "langchain",
        "langchain_openai",
        "langgraph",
        "sentence_transformers",
        "transformers",
        "torch",
    ]
    package_rows = [_module_status(name) for name in package_names]
    required_missing = [row["name"] for row in package_rows if not row["installed"]]

    neo4j = _probe_neo4j(env_values, secret_store)
    chroma = _probe_chroma(env_values)

    ready = not required_missing and neo4j.get("ok", False) and chroma.get("ok", False)
    return {
        "python": {
            "executable": os.sys.executable,
            "version": os.sys.version.split()[0],
        },
        "packages": package_rows,
        "services": [neo4j, chroma],
        "required_missing": required_missing,
        "ready": bool(ready),
    }


def main() -> None:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
