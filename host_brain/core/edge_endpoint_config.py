"""Pure Edge endpoint resolution shared by app and composition adapters."""

from __future__ import annotations

import os


def normalize_edge_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def resolve_edge_base_url() -> str:
    """Resolve the primary Edge RPC endpoint from immutable process input."""

    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return normalize_edge_base_url(override)

    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    return f"http://{host}:{port}"


def resolve_edge_base_urls() -> list[str]:
    """Resolve the ordered, de-duplicated Edge probe endpoints."""

    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    primary = resolve_edge_base_url()
    host = (
        str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
        or "127.0.0.1"
    )
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800

    candidates = [primary]
    if not override:
        candidates.append(f"http://{host}:{port}")
    allow_local_fallback = str(
        os.environ.get("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if (
        allow_local_fallback
        and not override
        and host not in {"127.0.0.1", "localhost"}
    ):
        candidates.extend(
            [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
        )

    seen: set[str] = set()
    urls: list[str] = []
    for item in candidates:
        url = normalize_edge_base_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


__all__ = [
    "normalize_edge_base_url",
    "resolve_edge_base_url",
    "resolve_edge_base_urls",
]
