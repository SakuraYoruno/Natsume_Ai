from __future__ import annotations

import json
import time
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from host_brain.app.dependencies import get_prompt_template_store
from host_brain.app.prompt_template_catalog import build_persona_first_batch_catalog
from host_brain.core.ids import new_request_id

router = APIRouter(prefix="/prompt-config", tags=["prompt-config"])


class PromptTemplateItem(BaseModel):
    template_id: str
    category: str
    scope: str
    locale: str
    content_type: str
    description: str
    default_content: str
    override_content: Optional[str] = None
    resolved_content: str
    source: str
    enabled: bool
    has_override: bool
    version: int
    created_at_ts_ms: Optional[int] = None
    updated_at_ts_ms: Optional[int] = None


class PromptTemplateListResponse(BaseModel):
    items: List[PromptTemplateItem]
    total: int
    request_id: str
    timestamp: int


class PromptTemplateResponse(BaseModel):
    item: PromptTemplateItem
    request_id: str
    timestamp: int


class PromptTemplateUpdateRequest(BaseModel):
    content: str
    enabled: bool = True
    description: Optional[str] = None


class PersonaPromptTemplateEntry(BaseModel):
    template_id: str
    title: str
    description: str
    focus_keys: List[str] = Field(default_factory=list)
    operator_notes: List[str] = Field(default_factory=list)
    item: PromptTemplateItem


class PersonaPromptTemplateBundle(BaseModel):
    bundle_id: str
    title: str
    description: str
    template_count: int
    entries: List[PersonaPromptTemplateEntry]


class PersonaPromptTemplateCatalogResponse(BaseModel):
    bundles: List[PersonaPromptTemplateBundle]
    total_templates: int
    request_id: str
    timestamp: int


def _to_item(payload: dict[str, Any]) -> PromptTemplateItem:
    return PromptTemplateItem(**payload)


@router.get("/templates", response_model=PromptTemplateListResponse)
async def list_prompt_templates(
    scope: Optional[str] = None,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
) -> PromptTemplateListResponse:
    request_id = new_request_id()
    timestamp = int(time.time() * 1000)
    store = get_prompt_template_store()
    items = [
        _to_item(item)
        for item in store.list_templates(
            scope=scope,
            category=category,
            keyword=keyword,
            source=source,
        )
    ]
    return PromptTemplateListResponse(
        items=items,
        total=len(items),
        request_id=request_id,
        timestamp=timestamp,
    )


@router.get("/templates/{template_id}", response_model=PromptTemplateResponse)
async def get_prompt_template(template_id: str) -> PromptTemplateResponse:
    request_id = new_request_id()
    timestamp = int(time.time() * 1000)
    store = get_prompt_template_store()
    try:
        item = _to_item(store.get_template(template_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PromptTemplateResponse(item=item, request_id=request_id, timestamp=timestamp)


@router.put("/templates/{template_id}", response_model=PromptTemplateResponse)
async def update_prompt_template(
    template_id: str,
    payload: PromptTemplateUpdateRequest,
) -> PromptTemplateResponse:
    request_id = new_request_id()
    timestamp = int(time.time() * 1000)
    store = get_prompt_template_store()
    try:
        current = store.get_template(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if str(current.get("content_type") or "").strip().lower() == "json":
        try:
            json.loads(str(payload.content or "").strip())
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid json override: {exc}") from exc

    store.upsert_template(
        template_id,
        content=str(payload.content or ""),
        enabled=bool(payload.enabled),
        description=payload.description,
    )
    item = _to_item(store.get_template(template_id))
    return PromptTemplateResponse(item=item, request_id=request_id, timestamp=timestamp)


@router.post("/templates/{template_id}/reset", response_model=PromptTemplateResponse)
async def reset_prompt_template(template_id: str) -> PromptTemplateResponse:
    request_id = new_request_id()
    timestamp = int(time.time() * 1000)
    store = get_prompt_template_store()
    try:
        item = _to_item(store.reset_template(template_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PromptTemplateResponse(item=item, request_id=request_id, timestamp=timestamp)


@router.get("/catalog/persona-first-batch", response_model=PersonaPromptTemplateCatalogResponse)
async def get_persona_first_batch_catalog() -> PersonaPromptTemplateCatalogResponse:
    request_id = new_request_id()
    timestamp = int(time.time() * 1000)
    store = get_prompt_template_store()
    bundles = [
        PersonaPromptTemplateBundle(
            bundle_id=bundle["bundle_id"],
            title=bundle["title"],
            description=bundle["description"],
            template_count=int(bundle["template_count"]),
            entries=[
                PersonaPromptTemplateEntry(
                    template_id=entry["template_id"],
                    title=entry["title"],
                    description=entry["description"],
                    focus_keys=list(entry.get("focus_keys") or []),
                    operator_notes=list(entry.get("operator_notes") or []),
                    item=_to_item(entry["item"]),
                )
                for entry in bundle["entries"]
            ],
        )
        for bundle in build_persona_first_batch_catalog(store)
    ]
    return PersonaPromptTemplateCatalogResponse(
        bundles=bundles,
        total_templates=sum(len(bundle.entries) for bundle in bundles),
        request_id=request_id,
        timestamp=timestamp,
    )
