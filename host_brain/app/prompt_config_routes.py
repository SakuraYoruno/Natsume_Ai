from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from host_brain.model_runtime.prompt_specs import (
    PromptSpec,
    get_prompt_spec,
    list_prompt_specs,
    prompt_spec_metadata,
    resolve_prompt_spec_json,
)

router = APIRouter(prefix="/prompt-config", tags=["prompt-config"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _catalog_item(spec: PromptSpec, *, include_content: bool) -> dict[str, Any]:
    content = resolve_prompt_spec_json(spec.spec_id) if include_content else None
    return {
        **prompt_spec_metadata(spec),
        "locale": "*",
        "content_type": "text" if spec.output_type == "text" else "json",
        "description": spec.purpose,
        "default_content": content,
        "resolved_content": content,
        "enabled": True,
    }


@router.get("/templates")
def list_prompt_templates(
    keyword: str | None = Query(None),
    scope: str | None = Query(None),
    category: str | None = Query(None),
    source: str | None = Query(None),
) -> dict[str, Any]:
    filters = {
        "keyword": str(keyword or "").strip().lower(),
        "scope": str(scope or "").strip().lower(),
        "category": str(category or "").strip().lower(),
        "source": str(source or "").strip().lower(),
    }
    items: list[dict[str, Any]] = []
    for spec in list_prompt_specs():
        item = _catalog_item(spec, include_content=False)
        haystack = " ".join(str(value or "") for value in item.values()).lower()
        if filters["keyword"] and filters["keyword"] not in haystack:
            continue
        if filters["scope"] and filters["scope"] not in str(item["scope"]).lower():
            continue
        if filters["category"] and filters["category"] not in str(item["category"]).lower():
            continue
        if filters["source"] and filters["source"] not in (
            f"{item['source']} {item['source_path']}"
        ).lower():
            continue
        items.append(item)
    return {
        "items": items,
        "total": len(items),
        "filters": {
            "keyword": keyword,
            "scope": scope,
            "category": category,
            "source": source,
        },
        "timestamp": _now_ms(),
    }


@router.get("/templates/{template_id:path}")
def get_prompt_template(template_id: str) -> dict[str, Any]:
    try:
        item = _catalog_item(get_prompt_spec(template_id), include_content=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown prompt spec") from exc
    return {
        "item": item,
        "timestamp": _now_ms(),
    }


@router.get("/catalog/persona-first-batch")
def get_persona_first_batch_catalog() -> dict[str, Any]:
    return {
        "bundles": [],
        "bundle_count": 0,
        "template_count": 0,
        "timestamp": _now_ms(),
    }
