"""Tool metadata facade and app-local governed execution boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import inspect
import json
from threading import RLock
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIRM_REQUIRED,
    GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
    TOKEN_INVALID,
)
from host_brain.core.ids import new_event_id, now_ms
from host_brain.governor.tokens import TokenService
from host_brain.tools.catalog import ToolCatalog, ToolMeta


ToolHandler = Callable[[dict[str, Any], dict[str, Any] | None], Any]
ToolTaskAuthorityVerifier = Callable[..., bool]

RESULT_SUCCESS = "SUCCESS"
RESULT_REJECTED = "REJECTED"
RESULT_FAILED = "FAILED"

TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
TOOL_HANDLER_MISSING = "TOOL_HANDLER_MISSING"
TOOL_EXECUTED = "TOOL_EXECUTED"
TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"


@dataclass(frozen=True)
class ToolExecutionResult:
    status: str
    result_code: str
    tool_id: str
    intent_id: str
    evidence_ref: str | None
    detail: dict[str, Any]
    output: Any = None


@dataclass(frozen=True)
class _RegisteredTool:
    """Executable and metadata frozen together under the Broker lock."""

    handler: ToolHandler
    meta: ToolMeta
    execution_safety: str
    adapter_identity: str
    binding_json: str


class _ToolHandlerVault:
    """Private executable registry shared only by one app-local boundary."""

    __slots__ = ("catalog", "lock", "registrations")

    def __init__(self, catalog: ToolCatalog) -> None:
        self.catalog = catalog
        self.lock = RLock()
        self.registrations: dict[str, _RegisteredTool] = {}


class _GovernedToolExecutionPort:
    """App-local port sealed to one authority verifier and private vault."""

    __slots__ = (
        "__vault",
        "__token_service",
        "__ledger",
        "__authority_verifier",
    )

    def __init__(
        self,
        *,
        vault: _ToolHandlerVault,
        token_service: TokenService,
        ledger: SQLiteLedger,
        authority_verifier: ToolTaskAuthorityVerifier,
    ) -> None:
        self.__vault = vault
        self.__token_service = token_service
        self.__ledger = ledger
        self.__authority_verifier = authority_verifier

    def execute_bound(
        self,
        *,
        task_authority: Any,
        expected_binding: Mapping[str, Any],
        request_id: str | None,
        intent_id: str,
        session_id: str,
        seq: int,
        tool_id: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        confirm_token_ref: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        ts_ms: int | None = None,
        before_handler: Callable[[], None] | None = None,
    ) -> ToolExecutionResult:
        try:
            # 任务权威在读取 handler 前先验证，并绑定请求、意图、会话和工具 ID；
            # 这样持有一个普通绑定投影的调用方不能把它变成任意工具的执行许可。
            authorized = self.__authority_verifier(
                task_authority=task_authority,
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                tool_id=tool_id,
            )
        except Exception as exc:
            raise PermissionError(GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED) from exc
        if authorized is not True:
            raise PermissionError(GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED)
        target = str(tool_id or "").strip()
        if not isinstance(expected_binding, Mapping):
            raise ValueError("expected_binding must be a mapping")
        with self.__vault.lock:
            registration = self.__vault.registrations.get(target)
            if registration is None:
                raise KeyError(f"tool handler missing: {target}")
            if _canonical_json(expected_binding) != registration.binding_json:
                # 节点准入时冻结的是完整目录/适配器/安全级别投影；执行前逐字节比对，
                # 防止重启或热更新后同一 tool_id 悄然指向了不同 schema 或写入 handler。
                raise ValueError(f"tool execution binding changed: {target}")
            if registration.execution_safety != "SAFE_LOCAL_REPLAY":
                # 可能存在外部副作用的适配器不允许本地直接重放，必须移交带持久证据
                # 的对账路径，避免一次响应丢失造成重复调用。
                raise ValueError(f"tool requires reconciliation: {target}")
            return _execute_registered(
                token_service=self.__token_service,
                ledger=self.__ledger,
                registration=registration,
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                tool_id=target,
                args=args,
                context=context,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                thread_id=thread_id,
                turn_id=turn_id,
                ts_ms=ts_ms,
                before_handler=before_handler,
            )


class ToolRuntimeBoundary:
    """One app-local Broker facade plus its non-exported execution vault."""

    __slots__ = ("broker", "__vault", "__token_service", "__ledger")

    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        token_service: TokenService,
        ledger: SQLiteLedger,
    ) -> None:
        vault = _ToolHandlerVault(catalog)
        self.__vault = vault
        self.__token_service = token_service
        self.__ledger = ledger
        self.broker = ToolBroker(
            catalog=catalog,
            token_service=token_service,
            ledger=ledger,
            _vault=vault,
        )

    def bind_execution_port(
        self,
        *,
        authority_verifier: ToolTaskAuthorityVerifier,
    ) -> _GovernedToolExecutionPort:
        """Bind the sole handler port inside one ASGI application object graph."""

        if not callable(authority_verifier):
            raise TypeError("authority_verifier must be callable")
        # 每次绑定显式注入验证器，端口不进入全局服务定位器；不同应用实例因此不会
        # 共享 handler vault 或错误复用另一实例的 Task authority。
        return _GovernedToolExecutionPort(
            vault=self.__vault,
            token_service=self.__token_service,
            ledger=self.__ledger,
            authority_verifier=authority_verifier,
        )


def build_tool_runtime_boundary(
    *,
    catalog: ToolCatalog,
    token_service: TokenService,
    ledger: SQLiteLedger,
) -> ToolRuntimeBoundary:
    """Construct an app-local Tool boundary; never store it in a process locator."""

    return ToolRuntimeBoundary(
        catalog=catalog,
        token_service=token_service,
        ledger=ledger,
    )


class ToolBroker:
    """Registration/readiness facade whose raw execution APIs are retired."""

    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        token_service: TokenService,
        ledger: SQLiteLedger,
        _vault: _ToolHandlerVault | None = None,
    ) -> None:
        self.catalog = catalog
        self.token_service = token_service
        self.ledger = ledger
        self.__vault = _vault or _ToolHandlerVault(catalog)
        if self.__vault.catalog is not catalog:
            raise ValueError("Tool handler vault must bind the exact ToolCatalog")

    def register_handler(
        self,
        tool_id: str,
        handler: ToolHandler,
        *,
        replay_safe_local: bool = False,
        adapter_version_id: str | None = None,
    ) -> None:
        if not callable(handler):
            raise ValueError("handler must be callable")
        target = str(tool_id or "").strip()
        if not target:
            raise ValueError("tool_id must be non-empty")
        explicit_version = str(adapter_version_id or "").strip() or None
        with self.__vault.lock:
            # 注册时把可执行对象与当时目录元数据一起密封；目录日后独立变更会令
            # readiness/执行失败关闭，而不是继续运行一个含义已经漂移的旧 handler。
            meta = self.catalog.get(target)
            execution_safety = (
                "SAFE_LOCAL_REPLAY" if replay_safe_local else "RECONCILIATION_REQUIRED"
            )
            adapter_identity = _handler_adapter_identity(
                target,
                handler,
                adapter_version_id=explicit_version,
            )
            binding = _execution_binding_payload(
                meta=meta,
                adapter_identity=adapter_identity,
                execution_safety=execution_safety,
            )
            registration = _RegisteredTool(
                handler=handler,
                meta=meta,
                execution_safety=execution_safety,
                adapter_identity=adapter_identity,
                binding_json=_canonical_json(binding),
            )
            self.__vault.registrations[target] = registration

    def resolve_execution_binding(self, tool_id: str) -> dict[str, Any]:
        """Return the immutable catalog/adapter identity frozen at admission.

        The binding contains no executable object.  A worker must reopen this
        projection immediately before execution and compare it byte-for-byte,
        preventing an admitted READ grant from executing a replaced WRITE
        handler or a changed schema/version after restart.
        """

        target = str(tool_id or "").strip()
        with self.__vault.lock:
            registration = self.__vault.registrations.get(target)
            if registration is None:
                raise KeyError(f"tool handler missing: {target}")
            # Metadata replacement outside the Broker must be followed by an
            # explicit handler re-registration.  Admission otherwise fails
            # closed instead of combining a new catalog row with an old handler.
            live_meta = self.catalog.get(target)
            if live_meta != registration.meta:
                raise ValueError(f"tool catalog binding changed: {target}")
            return json.loads(registration.binding_json)

    def readiness_snapshot(self) -> dict[str, Any]:
        """Return an exact Catalog↔handler readiness projection.

        Catalog presence alone is not execution readiness: a failed composition
        seed can leave metadata without a handler, and declarative payload
        normalizers are deliberately non-executable until a real governed
        adapter is wired.  This projection is read-only and secret-free.
        """

        with self.__vault.lock:
            catalog = {meta.id: meta for meta in self.catalog.list_all()}
            registrations = dict(self.__vault.registrations)
            catalog_ids = tuple(sorted(catalog))
            registered_ids = tuple(sorted(registrations))
            missing_handler_ids = tuple(sorted(set(catalog) - set(registrations)))
            orphan_handler_ids = tuple(sorted(set(registrations) - set(catalog)))
            drifted_ids = tuple(
                sorted(
                    tool_id
                    for tool_id in set(catalog).intersection(registrations)
                    if catalog[tool_id] != registrations[tool_id].meta
                )
            )
            executable_ids = tuple(
                sorted(
                    tool_id
                    for tool_id, registration in registrations.items()
                    if registration.execution_safety == "SAFE_LOCAL_REPLAY"
                    and tool_id in catalog
                    and catalog[tool_id] == registration.meta
                )
            )
            if missing_handler_ids or orphan_handler_ids or drifted_ids:
                # 不完整种子优先暴露为 PARTIAL_SEED，部署层据此拒绝接流；不能因为
                # 某一个工具可用就把整套目录视为完整，从而掩盖安全关键缺失。
                status = "PARTIAL_SEED"
            elif not catalog_ids:
                status = "UNSEEDED"
            elif not executable_ids:
                status = "FAIL_CLOSED_NO_GOVERNED_EXECUTOR"
            else:
                status = "READY"
            return {
                "status": status,
                "catalog_ids": catalog_ids,
                "registered_ids": registered_ids,
                "governed_executable_ids": executable_ids,
                "missing_handler_ids": missing_handler_ids,
                "orphan_handler_ids": orphan_handler_ids,
                "drifted_ids": drifted_ids,
            }

    def execute_bound(
        self,
        *,
        expected_binding: Mapping[str, Any],
        request_id: str | None,
        intent_id: str,
        session_id: str,
        seq: int,
        tool_id: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        confirm_token_ref: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        ts_ms: int | None = None,
    ) -> ToolExecutionResult:
        """Reject raw bound execution without an app-local Task authority port.

        Resolving a live binding is metadata admission, not execution
        authority. Only ``_GovernedToolExecutionPort`` can cross the private
        capability boundary after it reopens the exact frozen node authority.
        """

        target = str(tool_id or "").strip()
        del expected_binding, session_id, seq, args, context
        del approval_token_ref, confirm_token_ref
        return _log_and_return(
            ledger=self.ledger,
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=target,
            approval_token_ref=None,
            confirm_token_ref=None,
            status=RESULT_REJECTED,
            result_code=GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
            result_digest=(
                "raw bound ToolBroker execution is retired; an app-local "
                "verified Tool Task execution port is required"
            ),
            evidence_ref=None,
            ts_ms=int(ts_ms if ts_ms is not None else now_ms()),
            output=None,
        )

    def execute(
        self,
        *,
        request_id: str | None,
        intent_id: str,
        session_id: str,
        seq: int,
        tool_id: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        approval_token_ref: str | None = None,
        confirm_token_ref: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        ts_ms: int | None = None,
    ) -> ToolExecutionResult:
        """Reject the retired unbound execution API before handler lookup.

        Production execution must enter through HostCommand/TaskGraph and the
        exact immutable binding consumed by the app-local execution port.
        Keeping this compatibility signature fail-closed prevents old callers
        from using a catalog id plus Governor token as a second execution authority.
        """

        target = str(tool_id or "").strip()
        # Retired callers may still provide stale bearer material. It is not
        # authority and must not be copied into the durable audit ledger.
        del session_id, seq, args, context, approval_token_ref, confirm_token_ref
        return _log_and_return(
            ledger=self.ledger,
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=target,
            approval_token_ref=None,
            confirm_token_ref=None,
            status=RESULT_REJECTED,
            result_code=GOVERNED_TOOL_TASK_AUTHORITY_REQUIRED,
            result_digest=(
                "direct ToolBroker execution is retired; immutable TaskGraph "
                "authority and exact execution binding are required"
            ),
            evidence_ref=None,
            ts_ms=int(ts_ms if ts_ms is not None else now_ms()),
            output=None,
        )


def _execute_registered(
    *,
    token_service: TokenService,
    ledger: SQLiteLedger,
    registration: _RegisteredTool,
    request_id: str | None,
    intent_id: str,
    session_id: str,
    seq: int,
    tool_id: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
    approval_token_ref: str | None,
    confirm_token_ref: str | None,
    thread_id: str | None,
    turn_id: str | None,
    ts_ms: int | None,
    before_handler: Callable[[], None] | None,
) -> ToolExecutionResult:
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    safe_args = args if isinstance(args, dict) else {}
    meta = registration.meta
    handler = registration.handler

    if meta.side_effect_level == "WRITE":
        require_confirm = meta.auth_level == "CONFIRM"
        validation = token_service.validate_execution_tokens(
            intent_id=intent_id,
            skill_name=tool_id,
            session_id=session_id,
            seq=int(seq),
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            require_confirm=require_confirm,
            now_ts_ms=now_value,
        )
        if not validation.valid:
            code = validation.reason_code or TOKEN_INVALID
            detail = validation.detail or "write token validation failed"
            if code == CONFIRM_REQUIRED and not confirm_token_ref:
                detail = "confirm token required for write tool"
            return _log_and_return(
                ledger=ledger,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                intent_id=intent_id,
                tool_id=tool_id,
                approval_token_ref=approval_token_ref,
                confirm_token_ref=confirm_token_ref,
                status=RESULT_REJECTED,
                result_code=code,
                result_digest=detail,
                evidence_ref=None,
                ts_ms=now_value,
                output=None,
            )

    if before_handler is not None:
        if not callable(before_handler):
            raise TypeError("before_handler must be callable")
        # PENDING means the live Task authority, exact binding, replay-safety
        # and any WRITE token check all passed; the handler is next.
        before_handler()

    try:
        exec_context = dict(context or {})
        exec_context.setdefault("emotion_tag", "NEUTRAL")
        exec_context.setdefault("mood_state", "steady")
        output = handler(safe_args, exec_context)
    except Exception as exc:  # pragma: no cover - defensive guard
        return _log_and_return(
            ledger=ledger,
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            status=RESULT_FAILED,
            result_code=TOOL_EXECUTION_ERROR,
            result_digest=str(exc),
            evidence_ref=None,
            ts_ms=now_value,
            output=None,
        )

    evidence_ref = f"evidence::{new_event_id()}"
    return _log_and_return(
        ledger=ledger,
        request_id=request_id,
        thread_id=thread_id,
        turn_id=turn_id,
        intent_id=intent_id,
        tool_id=tool_id,
        approval_token_ref=approval_token_ref,
        confirm_token_ref=confirm_token_ref,
        status=RESULT_SUCCESS,
        result_code=TOOL_EXECUTED,
        result_digest=_result_digest(output),
        evidence_ref=evidence_ref,
        ts_ms=now_value,
        output=output,
    )


def _log_and_return(
    *,
    ledger: SQLiteLedger,
    request_id: str | None,
    thread_id: str | None,
    turn_id: str | None,
    intent_id: str,
    tool_id: str,
    approval_token_ref: str | None,
    confirm_token_ref: str | None,
    status: str,
    result_code: str,
    result_digest: str,
    evidence_ref: str | None,
    ts_ms: int,
    output: Any,
) -> ToolExecutionResult:
    row = {
        "request_id": request_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "intent_id": intent_id,
        "skill_name": tool_id,
        "approval_token_ref": approval_token_ref,
        "confirm_token_ref": confirm_token_ref,
        "result_status": status,
        "result_code": result_code,
        "result_digest": result_digest,
        "evidence_ref": evidence_ref,
        "manifest_ref": None,
        "undo_token_ref": None,
        "ts_ms": ts_ms,
    }
    ledger.log_skill_exec(row)
    return ToolExecutionResult(
        status=status,
        result_code=result_code,
        tool_id=tool_id,
        intent_id=intent_id,
        evidence_ref=evidence_ref,
        detail=row,
        output=output,
    )


def _result_digest(output: Any) -> str:
    canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _execution_binding_payload(
    *,
    meta: ToolMeta,
    adapter_identity: str,
    execution_safety: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "Natsume.tool.execution_binding.v1",
        "tool_id": str(meta.id),
        "tool_version": str(meta.version),
        "schema_hash": str(meta.schema_hash),
        "side_effect_level": str(meta.side_effect_level),
        "auth_level": str(meta.auth_level),
        "handler_adapter_id": str(adapter_identity),
        "execution_safety": str(execution_safety),
    }
    payload["binding_digest"] = "sha256:" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _handler_adapter_identity(
    tool_id: str,
    handler: Any,
    *,
    adapter_version_id: str | None = None,
) -> str:
    """Fingerprint executable code plus stable closure/configuration state.

    Dataclass-backed declarative handlers are expanded field by field, bound
    methods bind their ``__self__`` configuration, and callable objects must
    expose stable dataclass state or an explicit adapter version.  Raw values
    never enter the durable binding; only canonical hashes do.
    """

    seen_callables: set[int] = set()
    seen_values: set[int] = set()
    opaque_types: set[str] = set()

    def value_type(value: Any) -> str:
        return f"{type(value).__module__}.{type(value).__qualname__}"

    def json_digest(value: Any) -> dict[str, Any]:
        encoded = _canonical_json(value).encode("utf-8")
        return {
            "value_type": value_type(value),
            "value_sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def describe_dataclass(value: Any) -> dict[str, Any]:
        identity = id(value)
        if identity in seen_values:
            return {"value_type": value_type(value), "recursive": True}
        seen_values.add(identity)
        return {
            "value_type": value_type(value),
            "dataclass_fields": {
                item.name: describe_value(getattr(value, item.name))
                for item in fields(value)
            },
        }

    def describe_value(value: Any) -> dict[str, Any]:
        if value is None or isinstance(value, (str, int, float, bool)):
            return json_digest(value)
        if isinstance(value, bytes):
            return {
                "value_type": value_type(value),
                "value_sha256": hashlib.sha256(value).hexdigest(),
            }
        if callable(value):
            return {"callable": describe_callable(value)}
        if is_dataclass(value) and not isinstance(value, type):
            return describe_dataclass(value)
        identity = id(value)
        if identity in seen_values:
            return {"value_type": value_type(value), "recursive": True}
        seen_values.add(identity)
        if isinstance(value, Mapping):
            pairs = [
                {
                    "key": describe_value(key),
                    "value": describe_value(child),
                }
                for key, child in value.items()
            ]
            pairs.sort(key=_canonical_json)
            return {"value_type": value_type(value), "mapping": pairs}
        if isinstance(value, (list, tuple)):
            return {
                "value_type": value_type(value),
                "items": [describe_value(child) for child in value],
            }
        if isinstance(value, (set, frozenset)):
            items = [describe_value(child) for child in value]
            items.sort(key=_canonical_json)
            return {"value_type": value_type(value), "items": items}
        stable_id = str(getattr(value, "__natsume_adapter_version__", "") or "").strip()
        if stable_id:
            return {
                "value_type": value_type(value),
                "adapter_version_sha256": hashlib.sha256(
                    stable_id.encode("utf-8")
                ).hexdigest(),
            }
        opaque_types.add(value_type(value))
        return {"value_type": value_type(value), "opaque": True}

    def describe_callable(value: Any) -> dict[str, Any]:
        identity = id(value)
        if identity in seen_callables:
            return {"callable_type": value_type(value), "recursive": True}
        seen_callables.add(identity)
        projection: dict[str, Any] = {
            "module": str(getattr(value, "__module__", value.__class__.__module__)),
            "qualname": str(
                getattr(value, "__qualname__", value.__class__.__qualname__)
            ),
            "callable_class": value_type(value),
        }
        code = getattr(value, "__code__", None)
        if code is not None:
            projection["code_sha256"] = hashlib.sha256(code.co_code).hexdigest()
            projection["code_names"] = list(code.co_names)
            projection["code_constants"] = [
                describe_value(item) for item in code.co_consts
            ]
        if inspect.ismethod(value):
            projection["bound_function"] = describe_callable(value.__func__)
            projection["bound_self"] = describe_value(value.__self__)
        elif not inspect.isfunction(value) and not inspect.isbuiltin(value):
            call_impl = getattr(type(value), "__call__", None)
            if callable(call_impl):
                projection["call_implementation"] = describe_callable(call_impl)
            if is_dataclass(value) and not isinstance(value, type):
                projection["callable_state"] = describe_dataclass(value)
            else:
                stable_id = str(
                    getattr(value, "__natsume_adapter_version__", "") or ""
                ).strip()
                if stable_id:
                    projection["adapter_version_sha256"] = hashlib.sha256(
                        stable_id.encode("utf-8")
                    ).hexdigest()
                else:
                    opaque_types.add(value_type(value))
                    projection["callable_state"] = {
                        "value_type": value_type(value),
                        "opaque": True,
                    }
        closure_projection: list[dict[str, Any]] = []
        for cell in getattr(value, "__closure__", None) or ():
            try:
                child = cell.cell_contents
            except ValueError:
                closure_projection.append({"empty": True})
                continue
            closure_projection.append(describe_value(child))
        if closure_projection:
            projection["closure"] = closure_projection
        defaults = getattr(value, "__defaults__", None) or ()
        if defaults:
            projection["defaults"] = [describe_value(child) for child in defaults]
        kwdefaults = getattr(value, "__kwdefaults__", None) or {}
        if isinstance(kwdefaults, Mapping) and kwdefaults:
            projection["kwdefaults"] = {
                str(key): describe_value(child)
                for key, child in sorted(kwdefaults.items(), key=lambda item: str(item[0]))
            }
        wrapped = inspect.unwrap(value)
        if wrapped is not value and callable(wrapped):
            projection["wrapped"] = describe_callable(wrapped)
        return projection

    explicit_version = str(adapter_version_id or "").strip()
    projection: dict[str, Any] = {
        "tool_id": str(tool_id),
        "handler": describe_callable(handler),
    }
    if explicit_version:
        projection["adapter_version_sha256"] = hashlib.sha256(
            explicit_version.encode("utf-8")
        ).hexdigest()
    if opaque_types and not explicit_version:
        raise ValueError(
            "opaque Tool handler state requires adapter_version_id: "
            + ", ".join(sorted(opaque_types))
        )
    serialized = _canonical_json(projection)
    return "tool-handler://sha256/" + hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()
