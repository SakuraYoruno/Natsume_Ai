"""Tool execution single-entry broker with WRITE token gating."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import inspect
import json
from threading import RLock
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CONFIRM_REQUIRED, TOKEN_INVALID
from host_brain.core.ids import new_event_id, now_ms
from host_brain.governor.tokens import TokenService
from host_brain.tools.catalog import ToolCatalog, ToolMeta


ToolHandler = Callable[[dict[str, Any], dict[str, Any] | None], Any]

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


class ToolBroker:
    """Single legal entrypoint for managed tool execution."""

    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        token_service: TokenService,
        ledger: SQLiteLedger,
    ) -> None:
        self.catalog = catalog
        self.token_service = token_service
        self.ledger = ledger
        self._lock = RLock()
        self._registrations: dict[str, _RegisteredTool] = {}

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
        with self._lock:
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
            self._registrations[target] = registration

    def resolve_execution_binding(self, tool_id: str) -> dict[str, Any]:
        """Return the immutable catalog/adapter identity frozen at admission.

        The binding contains no executable object.  A worker must reopen this
        projection immediately before execution and compare it byte-for-byte,
        preventing an admitted READ grant from executing a replaced WRITE
        handler or a changed schema/version after restart.
        """

        target = str(tool_id or "").strip()
        with self._lock:
            registration = self._registrations.get(target)
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

        with self._lock:
            catalog = {meta.id: meta for meta in self.catalog.list_all()}
            registrations = dict(self._registrations)
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
        """Validate the frozen binding and execute its handler atomically.

        The same lock protects handler replacement, the exact binding compare,
        handler selection and invocation.  The bound path never reopens the
        mutable catalog after comparison and therefore has no compare/dispatch
        TOCTOU window.
        """

        target = str(tool_id or "").strip()
        if not isinstance(expected_binding, Mapping):
            raise ValueError("expected_binding must be a mapping")
        with self._lock:
            registration = self._registrations.get(target)
            if registration is None:
                raise KeyError(f"tool handler missing: {target}")
            if _canonical_json(expected_binding) != registration.binding_json:
                raise ValueError(f"tool execution binding changed: {target}")
            if registration.execution_safety != "SAFE_LOCAL_REPLAY":
                raise ValueError(f"tool requires reconciliation: {target}")
            return self._execute_registered(
                registration,
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
        target = str(tool_id or "").strip()
        with self._lock:
            registration = self._registrations.get(target)
            if registration is None:
                try:
                    self.catalog.get(target)
                except KeyError:
                    return self._missing_result(
                        request_id=request_id,
                        intent_id=intent_id,
                        tool_id=target,
                        approval_token_ref=approval_token_ref,
                        confirm_token_ref=confirm_token_ref,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        ts_ms=ts_ms,
                        not_found=True,
                    )
                return self._missing_result(
                    request_id=request_id,
                    intent_id=intent_id,
                    tool_id=target,
                    approval_token_ref=approval_token_ref,
                    confirm_token_ref=confirm_token_ref,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    ts_ms=ts_ms,
                    not_found=False,
                )
            return self._execute_registered(
                registration,
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
            )

    def _execute_registered(
        self,
        registration: _RegisteredTool,
        *,
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
    ) -> ToolExecutionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        safe_args = args if isinstance(args, dict) else {}
        meta = registration.meta
        handler = registration.handler

        if meta.side_effect_level == "WRITE":
            require_confirm = meta.auth_level == "CONFIRM"
            validation = self.token_service.validate_execution_tokens(
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
                return self._log_and_return(
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

        try:
            # M27-J：context 注入情感字段（OPT-38）
            exec_context = dict(context or {})
            if "emotion_tag" not in exec_context:
                exec_context["emotion_tag"] = "NEUTRAL"
            if "mood_state" not in exec_context:
                exec_context["mood_state"] = "steady"
            output = handler(safe_args, exec_context)
        except Exception as exc:  # pragma: no cover - defensive guard
            return self._log_and_return(
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
        digest = _result_digest(output)
        return self._log_and_return(
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            status=RESULT_SUCCESS,
            result_code=TOOL_EXECUTED,
            result_digest=digest,
            evidence_ref=evidence_ref,
            ts_ms=now_value,
            output=output,
        )

    def _missing_result(
        self,
        *,
        request_id: str | None,
        intent_id: str,
        tool_id: str,
        approval_token_ref: str | None,
        confirm_token_ref: str | None,
        thread_id: str | None,
        turn_id: str | None,
        ts_ms: int | None,
        not_found: bool,
    ) -> ToolExecutionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        result_code = TOOL_NOT_FOUND if not_found else TOOL_HANDLER_MISSING
        detail = (
            f"tool not found: {tool_id}"
            if not_found
            else f"tool handler missing: {tool_id}"
        )
        return self._log_and_return(
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            intent_id=intent_id,
            tool_id=tool_id,
            approval_token_ref=approval_token_ref,
            confirm_token_ref=confirm_token_ref,
            status=RESULT_REJECTED,
            result_code=result_code,
            result_digest=detail,
            evidence_ref=None,
            ts_ms=now_value,
            output=None,
        )

    def _log_and_return(
        self,
        *,
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
        self.ledger.log_skill_exec(row)
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
