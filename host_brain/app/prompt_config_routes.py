from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query

from host_brain.app.dependencies import get_prompt_template_store

router = APIRouter(prefix="/prompt-config", tags=["prompt-config"])


def _now_ms() -> int:
    return int(time.time() * 1000)


@router.get("/templates")
def list_prompt_templates(
    keyword: str | None = Query(None),
    scope: str | None = Query(None),
    category: str | None = Query(None),
    source: str | None = Query(None),
) -> dict[str, Any]:
    store = get_prompt_template_store()
    items = store.list_templates(
        keyword=keyword,
        scope=scope,
        category=category,
        source=source,
    )
    return {
        "items": items,
        "total": len(items),
        "filters": {
            "keyword": keyword,
            "scope": scope,
            "category": category,
            "source": source,
        },
        "retired": True,
        "timestamp": _now_ms(),
    }


@router.get("/templates/{template_id:path}")
def get_prompt_template(template_id: str) -> dict[str, Any]:
    return {
        "item": get_prompt_template_store().get_template(template_id),
        "retired": True,
        "timestamp": _now_ms(),
    }


@router.get("/catalog/persona-first-batch")
def get_persona_first_batch_catalog() -> dict[str, Any]:
    return {
        "bundles": [],
        "bundle_count": 0,
        "template_count": 0,
        "retired": True,
        "timestamp": _now_ms(),
    }
