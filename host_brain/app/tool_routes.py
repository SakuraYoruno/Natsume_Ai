"""ToolBroker REST API 路由（M12-A TASK-0156）。

DevSpec §24：ToolBroker REST API。
端点：
  GET  /tools/meta-pack                — 获取当前 Top-K 工具元数据包（含 tool_visibility_hash）
  GET  /tools/{tool_id}                — 查询单个工具元数据
  POST /tools/execute                  — 执行工具（经 ToolBroker 单入口 + WRITE token gating）
  GET  /tools/health                   — ToolBroker 健康状态
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    TOOL_VISIBILITY_POLICY_VIOLATION,
)
from host_brain.core.ids import new_request_id
from host_brain.tools.broker import (
    RESULT_REJECTED,
    RESULT_SUCCESS,
    TOOL_NOT_FOUND,
    ToolBroker,
    ToolExecutionResult,
)
from host_brain.tools.catalog import ToolCatalog, ToolMeta
from host_brain.tools.selector import ToolSelection, select_tool_meta_pack
from host_brain.skills.custom_provider_installer import (
    build_provider_module_name,
    invalidate_provider_module,
    list_custom_provider_infos,
    load_provider_template,
    write_provider_source,
)
from host_brain.skills.hub.hub_installer import (
    install_skill as install_hub_skill,
    list_installed_skills as list_installed_hub_skills,
    remove_skill as remove_hub_skill,
    search_remote_skills,
)
from host_brain.skills.install_repair import repair_skill_loading_failure
from host_brain.app.http_hooks_runtime_helpers import run_long_term_distillation_handler

_logger = logging.getLogger("Natsume.tool_routes")

# ---------------------------------------------------------------------------
# 路由实例
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/tools", tags=["tools"])

# 别名路由：兼容请求 /tool_kits 的前端（如 UAT 场景），返回与 /tools/meta-pack 相同结构
tool_kits_alias_router = APIRouter(prefix="", tags=["tools"])

# ---------------------------------------------------------------------------
# 依赖注入（外部注入 broker/catalog/ledger provider，消除循环导入风险）
# ---------------------------------------------------------------------------
_broker_provider: Optional[Callable[[], ToolBroker]] = None
_catalog_provider: Optional[Callable[[], ToolCatalog]] = None
_ledger_provider: Optional[Callable[[], SQLiteLedger]] = None


def set_tool_providers(
    *,
    broker_provider: Callable[[], ToolBroker],
    catalog_provider: Callable[[], ToolCatalog],
    ledger_provider: Callable[[], SQLiteLedger],
) -> None:
    """由 router registry 在注册路由后调用，注入依赖获取回调。"""
    global _broker_provider, _catalog_provider, _ledger_provider
    _broker_provider = broker_provider
    _catalog_provider = catalog_provider
    _ledger_provider = ledger_provider


def _get_broker() -> ToolBroker:
    if _broker_provider is not None:
        return _broker_provider()
    raise RuntimeError("ToolBroker provider not configured")


def _get_catalog() -> ToolCatalog:
    if _catalog_provider is not None:
        return _catalog_provider()
    raise RuntimeError("ToolCatalog provider not configured")


def _empty_schema_hash() -> str:
    import hashlib
    import json

    return "sha256:" + hashlib.sha256(
        json.dumps({"type": "object", "properties": {}}, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _register_catalog_tool(
    *,
    catalog: ToolCatalog,
    broker: ToolBroker,
    tool_id: str,
    side_effect_level: str,
    auth_level: str,
    domain_tags: tuple[str, ...],
    desc: str,
    handler: Callable[[dict[str, Any], dict[str, Any] | None], Any],
) -> None:
    catalog.register(ToolMeta(
        id=tool_id,
        name=tool_id,
        desc=desc,
        schema_hash=_empty_schema_hash(),
        side_effect_level=side_effect_level,
        auth_level=auth_level,
        domain_tags=domain_tags,
        version="1.0",
    ))
    broker.register_handler(tool_id, handler)


def _ensure_default_tool_catalog_seeded() -> int:
    catalog = _get_catalog()
    existing_tools = catalog.list_all()
    if existing_tools:
        return len(existing_tools)

    broker = _get_broker()

    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
    from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
    from host_brain.skills.scheduler_registry import build_scheduler_skill_registry  # noqa: PLC0415

    skill_registry = get_skill_registry()
    builtin_tool_defs = [
        ("file_list", "READ", "NONE", ("file",), "列出目录内容（只读）"),
        ("file_read", "READ", "NONE", ("file",), "读取文件内容（只读）"),
        ("memory_search", "READ", "NONE", ("memory",), "语义检索记忆库"),
        ("system_info", "READ", "NONE", ("system",), "查询系统信息"),
        ("network_status", "READ", "NONE", ("system",), "查询网络状态"),
        ("web_search", "READ", "NONE", ("web",), "执行网络搜索"),
    ]
    for tool_id, side_effect_level, auth_level, domain_tags, desc in builtin_tool_defs:
        skill_meta = skill_registry.get(tool_id)
        if skill_meta is None:
            continue
        _register_catalog_tool(
            catalog=catalog,
            broker=broker,
            tool_id=tool_id,
            side_effect_level=side_effect_level,
            auth_level=auth_level,
            domain_tags=domain_tags,
            desc=desc,
            handler=lambda args, ctx, _h=skill_meta.handler: _h(args or {}, ctx or {}),
        )

    personality_registry = build_personality_skill_registry()
    scheduler_registry = build_scheduler_skill_registry()
    personality_tool_defs = [
        ("diary_writer", "WRITE", "LOW", ("memory", "file"), "每日日记生成：汇总当日对话→LLM→双写文件+Episodic"),
        ("dream_generator", "WRITE", "LOW", ("memory",), "梦境生成：读取Episodic→LLM生成梦境叙事→写回Episodic"),
        ("desktop_note", "WRITE", "LOW", ("system", "file"), "桌面留言：创建/更新/删除文本文件（路径穿越防护）"),
        ("focus_mode", "READ", "NONE", ("system",), "专注模式切换：进入/退出专注模式"),
        ("proactive_talk", "WRITE", "LOW", ("chat",), "主动对话：空闲超阈值时触发LLM生成问候并推送到Edge"),
    ]
    scheduler_tool_defs = [
        ("scheduler_add", "WRITE", "MEDIUM", ("system",), "向调度器添加定时任务"),
        ("scheduler_cancel", "WRITE", "MEDIUM", ("system",), "取消/删除调度任务（不可逆）"),
        ("scheduler_list", "READ", "NONE", ("system",), "列出当前所有调度任务"),
    ]
    for registry, definitions in ((personality_registry, personality_tool_defs), (scheduler_registry, scheduler_tool_defs)):
        for tool_id, side_effect_level, auth_level, domain_tags, desc in definitions:
            skill_meta = registry.get(tool_id)
            if skill_meta is None:
                continue
            _register_catalog_tool(
                catalog=catalog,
                broker=broker,
                tool_id=tool_id,
                side_effect_level=side_effect_level,
                auth_level=auth_level,
                domain_tags=domain_tags,
                desc=desc,
                handler=lambda args, ctx, _h=skill_meta.handler: _h(args or {}, ctx or {}),
            )

    _register_catalog_tool(
        catalog=catalog,
        broker=broker,
        tool_id="long_term_memory_distillation",
        side_effect_level="WRITE",
        auth_level="LOW",
        domain_tags=("memory",),
        desc="长期记忆蒸馏：从EpisodicStore召回高频记忆→LLM摘要→生成World Canon提案",
        handler=lambda args, ctx: run_long_term_distillation_handler(args or {}, ctx or {}),
    )
    return len(catalog.list_all())


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------

class ToolMetaResponse(BaseModel):
    """单个工具元数据响应。"""
    id: str = Field(..., description="工具唯一 ID")
    name: str = Field(..., description="工具名称")
    desc: str = Field(..., description="工具描述")
    schema_hash: str = Field(..., description="输入 Schema SHA-256 哈希")
    side_effect_level: str = Field(..., description="副作用级别: READ/WRITE")
    auth_level: str = Field(..., description="授权级别: NONE/APPROVAL/CONFIRM")
    domain_tags: List[str] = Field(..., description="领域标签")
    version: str = Field(..., description="工具版本")


class ToolMetaPackResponse(BaseModel):
    """GET /tools/meta-pack 响应体。"""
    tool_meta_pack: List[ToolMetaResponse] = Field(..., description="工具元数据列表")
    selected_ids: List[str] = Field(..., description="已选工具 ID 列表")
    tool_visibility_hash: str = Field(..., description="工具可见性哈希（SHA-256）")
    total: int = Field(..., description="返回工具数量")
    include_write: bool = Field(..., description="是否包含 WRITE 工具")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class ToolExecuteRequest(BaseModel):
    """POST /tools/execute 请求体。"""
    tool_id: str = Field(..., description="工具 ID")
    intent_id: str = Field(..., description="关联 Intent ID")
    session_id: str = Field(..., description="会话 ID")
    seq: int = Field(default=1, description="序列号")
    args: Optional[Dict[str, Any]] = Field(default=None, description="工具调用参数")
    context: Optional[Dict[str, Any]] = Field(default=None, description="执行上下文（可选）")
    approval_token_ref: Optional[str] = Field(default=None, description="WRITE 工具审批票据引用")
    confirm_token_ref: Optional[str] = Field(default=None, description="WRITE 工具确认票据引用")
    thread_id: Optional[str] = Field(default=None, description="线程 ID（可选）")
    turn_id: Optional[str] = Field(default=None, description="轮次 ID（可选）")


class ToolExecuteResponse(BaseModel):
    """POST /tools/execute 响应体。"""
    status: str = Field(..., description="执行状态: SUCCESS/REJECTED/FAILED")
    result_code: str = Field(..., description="结果代码")
    tool_id: str = Field(..., description="工具 ID")
    intent_id: str = Field(..., description="关联 Intent ID")
    evidence_ref: Optional[str] = Field(default=None, description="证据引用（成功时）")
    output: Optional[Any] = Field(default=None, description="工具输出（成功时）")
    reason_code: Optional[str] = Field(default=None, description="拒绝/失败原因代码")
    detail: Optional[str] = Field(default=None, description="详细说明")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class ToolHealthResponse(BaseModel):
    """GET /tools/health 响应体。"""
    status: str = Field(..., description="ToolBroker 状态: OK/DEGRADED")
    catalog_size: int = Field(..., description="已注册工具数量")
    write_tools: int = Field(..., description="WRITE 工具数量")
    read_tools: int = Field(..., description="READ 工具数量")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class CustomSkillProviderInfoResponse(BaseModel):
    filename: str = Field(..., description="provider 文件名")
    module_name: str = Field(..., description="provider 模块名")
    class_name: str | None = Field(default=None, description="provider 类名")
    path: str = Field(..., description="provider 文件路径")
    size_bytes: int = Field(..., description="文件大小")
    updated_at_ms: int = Field(..., description="更新时间戳（毫秒）")
    declared_skill_names: List[str] = Field(default_factory=list, description="provider 声明的 skill 名")
    active_skill_names: List[str] = Field(default_factory=list, description="当前注册表中已激活的 skill 名")
    status: str = Field(..., description="状态：ACTIVE/STAGED/ERROR")
    error: str | None = Field(default=None, description="错误详情")


class CustomSkillProviderListResponse(BaseModel):
    items: List[CustomSkillProviderInfoResponse] = Field(default_factory=list, description="provider 列表")
    total: int = Field(..., description="provider 数量")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class CustomSkillProviderTemplateResponse(BaseModel):
    filename_hint: str = Field(..., description="建议文件名")
    source_code: str = Field(..., description="provider 模板源码")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")


class CustomSkillProviderInstallRequest(BaseModel):
    filename: str = Field(..., description="provider 文件名")
    source_code: str = Field(..., description="provider 源码")
    activate_now: bool = Field(default=True, description="是否立即刷新 SkillRegistry")


class CustomSkillProviderInstallResponse(BaseModel):
    status: str = Field(..., description="安装状态：INSTALLED/ERROR")
    filename: str = Field(..., description="provider 文件名")
    module_name: str = Field(..., description="provider 模块名")
    path: str = Field(..., description="provider 文件路径")
    activated: bool = Field(..., description="是否已刷新到当前注册表")
    declared_skill_names: List[str] = Field(default_factory=list, description="provider 声明的 skill")
    active_skill_names: List[str] = Field(default_factory=list, description="当前已激活的 skill")
    message: str = Field(..., description="结果说明")
    request_id: str = Field(..., description="请求 ID")
    timestamp: int = Field(..., description="响应时间戳（毫秒）")




class HubSkillInstallRequest(BaseModel):
    source: str = Field(..., description="Hub skill source: github:owner/repo/slug or local directory path")
    force: bool = Field(default=False, description="Whether to overwrite existing installation")
    activate_now: bool = Field(default=True, description="Whether to refresh SkillRegistry immediately")


class HubSkillInstallResponse(BaseModel):
    status: str = Field(..., description="Install status: INSTALLED/ERROR")
    name: str = Field(..., description="Installed skill name")
    version: str = Field(..., description="Installed skill version")
    source: str = Field(..., description="Original source")
    install_dir: str = Field(..., description="Installed directory")
    activated: bool = Field(..., description="Whether the skill is active after refresh")
    repair_attempted: bool = Field(default=False, description="Whether auto-repair was attempted after install")
    repair_succeeded: bool = Field(default=False, description="Whether auto-repair succeeded")
    repair_actions: int = Field(default=0, description="How many repair actions were attempted")
    active_skill_names: List[str] = Field(default_factory=list, description="Currently active skill names")
    message: str = Field(..., description="Result summary")
    request_id: str = Field(..., description="Request ID")
    timestamp: int = Field(..., description="Response timestamp")


class HubSkillEntryResponse(BaseModel):
    name: str = Field(..., description="Skill name")
    version: str = Field(default="", description="Skill version")
    description: str = Field(default="", description="Skill description")
    author: str = Field(default="", description="Skill author")
    source: str = Field(default="", description="Install source")
    installed_at: str = Field(default="", description="Installed time")
    dir: str = Field(default="", description="Installed directory")


class HubSkillListResponse(BaseModel):
    total: int = Field(..., description="Installed skill count")
    skills: List[HubSkillEntryResponse] = Field(default_factory=list, description="Installed hub skills")
    request_id: str = Field(..., description="Request ID")
    timestamp: int = Field(..., description="Response timestamp")


class HubSkillRemoveResponse(BaseModel):
    status: str = Field(..., description="Remove status: REMOVED/ERROR")
    name: str = Field(..., description="Removed skill name")
    message: str = Field(..., description="Result summary")
    request_id: str = Field(..., description="Request ID")
    timestamp: int = Field(..., description="Response timestamp")


class HubSkillSearchResponse(BaseModel):
    total: int = Field(..., description="Matched remote skill count")
    skills: List[HubSkillEntryResponse] = Field(default_factory=list, description="Remote matching skills")
    request_id: str = Field(..., description="Request ID")
    timestamp: int = Field(..., description="Response timestamp")

# ---------------------------------------------------------------------------
# GET /tools/health — ToolBroker 健康状态
# ---------------------------------------------------------------------------

@router.get("/health", response_model=ToolHealthResponse)
async def tool_health() -> ToolHealthResponse:
    """ToolBroker 健康状态检查。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        catalog = _get_catalog()
        _ensure_default_tool_catalog_seeded()
        all_tools = catalog.list_all()
        write_count = sum(1 for t in all_tools if t.side_effect_level == "WRITE")
        read_count = len(all_tools) - write_count

        return ToolHealthResponse(
            status="OK",
            catalog_size=len(all_tools),
            write_tools=write_count,
            read_tools=read_count,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
        _logger.warning("tool_health: provider not configured: %s", exc)
        return ToolHealthResponse(
            status="DEGRADED",
            catalog_size=0,
            write_tools=0,
            read_tools=0,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("tool_health failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /tools/meta-pack — 获取 Top-K 工具元数据包
# ---------------------------------------------------------------------------

@router.get("/meta-pack", response_model=ToolMetaPackResponse)
async def get_tool_meta_pack(
    query: str = "",
    k: int = 6,
    include_write: bool = False,
    tags: Optional[str] = None,
) -> ToolMetaPackResponse:
    """获取当前 Top-K 工具元数据包（含 tool_visibility_hash）。

    - `query`: 语义查询词，用于 Top-K 排序
    - `k`: 最多返回工具数量（默认 6，最大 20）
    - `include_write`: 是否包含 WRITE 工具（默认 False，需显式开启）
    - `tags`: 逗号分隔的领域标签过滤（可选）
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    k = min(max(1, k), 20)
    required_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    try:
        catalog = _get_catalog()
        _ensure_default_tool_catalog_seeded()
        selection: ToolSelection = select_tool_meta_pack(
            catalog,
            query=query,
            k=k,
            include_write=include_write,
            required_tags=required_tags,
        )

        tool_responses = [
            ToolMetaResponse(
                id=meta["id"],
                name=meta["name"],
                desc=meta["desc"],
                schema_hash=meta["schema_hash"],
                side_effect_level=meta["side_effect_level"],
                auth_level=meta["auth_level"],
                domain_tags=meta["domain_tags"],
                version=meta["version"],
            )
            for meta in selection.tool_meta_pack
        ]

        return ToolMetaPackResponse(
            tool_meta_pack=tool_responses,
            selected_ids=selection.selected_ids,
            tool_visibility_hash=selection.tool_visibility_hash,
            total=len(tool_responses),
            include_write=include_write,
            request_id=request_id,
            timestamp=ts_ms,
        )

    except RuntimeError as exc:
        _logger.warning("get_tool_meta_pack: provider not configured: %s", exc)
        # 降级：返回空包（ToolBroker 未初始化时不阻断服务）
        return ToolMetaPackResponse(
            tool_meta_pack=[],
            selected_ids=[],
            tool_visibility_hash="sha256:" + "0" * 64,
            total=0,
            include_write=include_write,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("get_tool_meta_pack failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@tool_kits_alias_router.get("/tool_kits", response_model=ToolMetaPackResponse)
async def get_tool_kits_alias() -> ToolMetaPackResponse:
    """兼容别名：GET /tool_kits 与 GET /tools/meta-pack 返回相同结构，便于 UAT/外部前端统一访问。"""
    return await get_tool_meta_pack(query="", k=20, include_write=True)


# ---------------------------------------------------------------------------
# GET /tools/{tool_id} — 查询单个工具元数据
# ---------------------------------------------------------------------------

@router.get("/{tool_id}", response_model=ToolMetaResponse)
async def get_tool_meta(tool_id: str) -> ToolMetaResponse:
    """查询单个工具元数据。"""
    request_id = new_request_id()

    try:
        catalog = _get_catalog()
        _ensure_default_tool_catalog_seeded()
        meta = catalog.get(tool_id)
        return ToolMetaResponse(
            id=meta.id,
            name=meta.name,
            desc=meta.desc,
            schema_hash=meta.schema_hash,
            side_effect_level=meta.side_effect_level,
            auth_level=meta.auth_level,
            domain_tags=list(meta.domain_tags),
            version=meta.version,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"tool not found: {tool_id}",
            headers={"X-Reason-Code": TOOL_NOT_FOUND},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_tool_meta failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /tools/execute — 执行工具（经 ToolBroker 单入口）
# ---------------------------------------------------------------------------

@router.post("/execute", response_model=ToolExecuteResponse, status_code=200)
async def execute_tool(body: ToolExecuteRequest) -> ToolExecuteResponse:
    """执行工具（经 ToolBroker 单入口 + WRITE token gating）。

    - READ 工具：无需 approval_token_ref，直接执行
    - WRITE 工具：必须提供 approval_token_ref，否则返回 403
    - 所有执行均写入审计链（skill_exec_log）
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        _ensure_default_tool_catalog_seeded()
        broker = _get_broker()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        result: ToolExecutionResult = broker.execute(
            request_id=request_id,
            intent_id=body.intent_id,
            session_id=body.session_id,
            seq=body.seq,
            tool_id=body.tool_id,
            args=body.args,
            context=body.context,
            approval_token_ref=body.approval_token_ref,
            confirm_token_ref=body.confirm_token_ref,
            thread_id=body.thread_id,
            turn_id=body.turn_id,
            ts_ms=ts_ms,
        )

        # WRITE 工具无 token → 403
        if result.status == RESULT_REJECTED and result.result_code in (
            "TOOL_TOKEN_MISSING",
            "TOKEN_INVALID",
            "CONFIRM_REQUIRED",
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.detail.get("result_digest", "write token required"),
                headers={"X-Reason-Code": result.result_code},
            )

        # 工具不存在 → 404
        if result.status == RESULT_REJECTED and result.result_code == TOOL_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tool not found: {body.tool_id}",
                headers={"X-Reason-Code": TOOL_NOT_FOUND},
            )

        # 其他拒绝 → 422
        if result.status == RESULT_REJECTED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=result.detail.get("result_digest", "tool execution rejected"),
                headers={"X-Reason-Code": result.result_code},
            )

        return ToolExecuteResponse(
            status=result.status,
            result_code=result.result_code,
            tool_id=result.tool_id,
            intent_id=result.intent_id,
            evidence_ref=result.evidence_ref,
            output=result.output,
            reason_code=None,
            detail=None,
            request_id=request_id,
            timestamp=ts_ms,
        )

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error(
            "execute_tool failed: %s",
            exc,
            extra={"request_id": request_id, "tool_id": body.tool_id},
        )
        raise HTTPException(status_code=500, detail=str(exc))


def _get_active_skill_names() -> set[str]:
    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

    try:
        registry = get_skill_registry()
        return set(registry.list_skill_names())
    except Exception:
        return set()


def _attempt_activate_hub_skill(*, install_dir: str, skill_name: str) -> tuple[bool, list[str], bool, bool, int, str]:
    from host_brain.app.dependencies import refresh_skill_registry  # noqa: PLC0415

    registry = refresh_skill_registry()
    active_skill_names = list(registry.list_skill_names())
    activated = bool(skill_name and skill_name in active_skill_names)
    if activated:
        return True, active_skill_names, False, False, 0, "skill installed and registry refreshed"

    auto_repair_enabled = str(os.environ.get("HOST_SKILL_AUTO_REPAIR_ENABLED") or "true").strip().lower() not in {"0", "false", "no", "off"}
    if not auto_repair_enabled or not install_dir or not Path(install_dir).exists():
        return False, active_skill_names, False, False, 0, "skill installed but not active after refresh"

    repair = repair_skill_loading_failure(skill_dir=Path(install_dir), exc=f"hub skill installed but inactive after refresh: {skill_name}")
    repair_attempted = True
    repair_succeeded = bool(repair.get("success"))
    repair_actions = len(list(repair.get("actions") or []))
    if repair_succeeded:
        registry = refresh_skill_registry()
        active_skill_names = list(registry.list_skill_names())
        activated = bool(skill_name and skill_name in active_skill_names)
        if activated:
            return True, active_skill_names, repair_attempted, True, repair_actions, "skill installed, auto-repaired, and activated"
        return False, active_skill_names, repair_attempted, True, repair_actions, "auto-repair ran but skill still inactive after refresh"
    return False, active_skill_names, repair_attempted, False, repair_actions, "auto-repair attempted but failed to activate skill"


@router.get("/skills/custom-providers", response_model=CustomSkillProviderListResponse)
async def list_custom_skill_providers() -> CustomSkillProviderListResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        items = [
            CustomSkillProviderInfoResponse(
                filename=item.filename,
                module_name=item.module_name,
                class_name=item.class_name,
                path=item.path,
                size_bytes=item.size_bytes,
                updated_at_ms=item.updated_at_ms,
                declared_skill_names=list(item.declared_skill_names),
                active_skill_names=list(item.active_skill_names),
                status=item.status,
                error=item.error,
            )
            for item in list_custom_provider_infos(active_skill_names=_get_active_skill_names())
        ]
        return CustomSkillProviderListResponse(
            items=items,
            total=len(items),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("list_custom_skill_providers failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills/custom-providers/template", response_model=CustomSkillProviderTemplateResponse)
async def get_custom_skill_provider_template() -> CustomSkillProviderTemplateResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        return CustomSkillProviderTemplateResponse(
            filename_hint="my_skill_provider.py",
            source_code=load_provider_template(),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("get_custom_skill_provider_template failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/skills/custom-providers/install", response_model=CustomSkillProviderInstallResponse, status_code=200)
async def install_custom_skill_provider(body: CustomSkillProviderInstallRequest) -> CustomSkillProviderInstallResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)

    try:
        path = write_provider_source(body.filename, body.source_code)
        module_name = invalidate_provider_module(path.name)
        active_skill_names: set[str] = set()
        activated = False
        if body.activate_now:
            from host_brain.app.dependencies import refresh_skill_registry  # noqa: PLC0415

            registry = refresh_skill_registry()
            active_skill_names = set(registry.list_skill_names())
            activated = True
        else:
            active_skill_names = _get_active_skill_names()

        info = next(
            (item for item in list_custom_provider_infos(active_skill_names=active_skill_names) if item.filename == path.name),
            None,
        )
        declared = list(info.declared_skill_names) if info else []
        active = list(info.active_skill_names) if info else []
        status_text = "INSTALLED" if not info or info.status != "ERROR" else "ERROR"
        activated = bool(info and info.status == "ACTIVE")
        message = "provider saved and registry refreshed" if activated else "provider saved; restart or refresh SkillRegistry to activate"
        if info and info.status == "ERROR":
            message = f"provider saved but failed to load: {info.error}"

        return CustomSkillProviderInstallResponse(
            status=status_text,
            filename=path.name,
            module_name=module_name,
            path=str(path),
            activated=activated,
            declared_skill_names=declared,
            active_skill_names=active,
            message=message,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _logger.error("install_custom_skill_provider failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills/hub", response_model=HubSkillListResponse)
async def list_hub_skills() -> HubSkillListResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        rows = list_installed_hub_skills()
        skills = [HubSkillEntryResponse(**dict(row)) for row in rows]
        return HubSkillListResponse(
            total=len(skills),
            skills=skills,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("list_hub_skills failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/skills/hub/search", response_model=HubSkillSearchResponse)
async def search_hub_skills(query: str) -> HubSkillSearchResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        rows = search_remote_skills(query)
        skills = [
            HubSkillEntryResponse(
                name=str(item.name),
                version=str(item.version),
                description=str(item.description),
                author=str(item.author),
                source="remote",
                installed_at="",
                dir="",
            )
            for item in rows
        ]
        return HubSkillSearchResponse(
            total=len(skills),
            skills=skills,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("search_hub_skills failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/skills/hub/install", response_model=HubSkillInstallResponse, status_code=200)
async def install_hub_skill_route(body: HubSkillInstallRequest) -> HubSkillInstallResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        result = install_hub_skill(body.source, force=body.force)
        active_skill_names: list[str] = []
        activated = False
        repair_attempted = False
        repair_succeeded = False
        repair_actions = 0
        if result.success and body.activate_now:
            activated, active_skill_names, repair_attempted, repair_succeeded, repair_actions, activation_message = _attempt_activate_hub_skill(
                install_dir=str(result.install_dir),
                skill_name=str(result.name),
            )
        else:
            active_skill_names = sorted(_get_active_skill_names())
            activated = result.name in active_skill_names if result.name else False
            activation_message = str(result.detail or result.error or "install failed")

        status_text = "INSTALLED" if result.success else "ERROR"
        message = activation_message if result.success else str(result.error or result.detail or "install failed")
        return HubSkillInstallResponse(
            status=status_text,
            name=str(result.name),
            version=str(result.version),
            source=str(result.source),
            install_dir=str(result.install_dir),
            activated=activated,
            repair_attempted=repair_attempted,
            repair_succeeded=repair_succeeded,
            repair_actions=repair_actions,
            active_skill_names=active_skill_names,
            message=message,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("install_hub_skill_route failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/skills/hub/{skill_name}", response_model=HubSkillRemoveResponse)
async def remove_hub_skill_route(skill_name: str) -> HubSkillRemoveResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        result = remove_hub_skill(skill_name)
        if result.success:
            try:
                from host_brain.app.dependencies import refresh_skill_registry  # noqa: PLC0415
                refresh_skill_registry()
            except Exception:
                pass
        return HubSkillRemoveResponse(
            status="REMOVED" if result.success else "ERROR",
            name=str(result.name),
            message=str(result.detail or result.error or "remove failed"),
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("remove_hub_skill_route failed: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))
