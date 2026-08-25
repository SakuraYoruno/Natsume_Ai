"""任务运行时的入口命令与执行器分配契约（v1）。

这是 Host/Ingress 与 Task Runtime 之间的窄边界：边界上只能传递有界标识和
可持久化引用，不能携带原始请求体、凭据、签名、Bearer 值或审批令牌。这样重放、
审计和跨进程传输都不会把短生命周期的秘密重新带入执行平面。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping


HOST_COMMAND_SCHEMA_VERSION = "Natsume.task.host_command.v1"
EXECUTOR_ASSIGNMENT_SCHEMA_VERSION = "Natsume.agent.runtime_assignment.v2"
EXECUTOR_ASSIGNMENT_INVALID = "RUNTIME_ASSIGNMENT_INVALID"


class HostCommandSurface(StrEnum):
    CHAT = "chat"
    HOST_INPUT = "host_input"
    TOOL = "tool"
    INTERNAL = "internal"
    SCHEDULER = "scheduler"
    GATEWAY = "gateway"
    OPERATOR = "operator"


class TaskExecutor(StrEnum):
    CANONICAL = "canonical"


class TaskContractReasonCode(StrEnum):
    PAYLOAD_INVALID = "TASK_CONTRACT_PAYLOAD_INVALID"
    FIELD_MISSING = "TASK_CONTRACT_FIELD_MISSING"
    FIELD_INVALID = "TASK_CONTRACT_FIELD_INVALID"
    FIELD_UNEXPECTED = "TASK_CONTRACT_FIELD_UNEXPECTED"
    VERSION_UNSUPPORTED = "TASK_CONTRACT_VERSION_UNSUPPORTED"
    SECRET_MATERIAL_FORBIDDEN = "TASK_CONTRACT_SECRET_MATERIAL_FORBIDDEN"


class TaskContractError(ValueError):
    """契约验证失败时的闭合错误，保留稳定的机器可读原因码。

    调用方可将 ``to_dict`` 安全地映射到既有运行时错误协议；审计端则使用
    ``audit_dict`` 取得字段路径，而无需依赖会变化的异常文本。
    """

    def __init__(
        self,
        reason_code: str | TaskContractReasonCode,
        detail: str,
        *,
        field_path: str = "",
    ) -> None:
        self.reason_code = str(getattr(reason_code, "value", reason_code) or TaskContractReasonCode.PAYLOAD_INVALID.value)
        self.detail = str(detail or "task contract validation failed")
        self.field_path = str(field_path or "")
        location = f" {self.field_path}" if self.field_path else ""
        super().__init__(f"{self.reason_code}:{location} {self.detail}".strip())

    def to_dict(self) -> dict[str, str]:
        """Keep the legacy RuntimeRouterError wire shape."""

        return {"reason_code": self.reason_code, "detail": self.detail}

    def audit_dict(self) -> dict[str, str]:
        payload = self.to_dict()
        if self.field_path:
            payload["field_path"] = self.field_path
        return payload


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_ROUTE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,475}$"
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
_FORBIDDEN_REFERENCE_SCHEMES = frozenset(
    {"api-key", "authorization", "bearer", "jwt", "password", "secret", "signature", "token"}
)
_FORBIDDEN_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "password",
        "raw_body",
        "raw_input",
        "raw_proof",
        "raw_secret",
        "raw_token",
        "secret",
        "shared_secret",
        "signature",
        "token",
    }
)


def _error(reason_code: str | TaskContractReasonCode, field_path: str, detail: str) -> TaskContractError:
    return TaskContractError(reason_code, detail, field_path=field_path)


def _normalise_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _require_mapping(value: object, field_path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(TaskContractReasonCode.PAYLOAD_INVALID, field_path, "must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise _error(TaskContractReasonCode.PAYLOAD_INVALID, field_path, "keys must be strings")
    return value


def _check_fields(
    value: Mapping[str, object],
    *,
    field_path: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    """先拒绝秘密字段，再严格校验 v1 的字段集合与 JSON 可序列化性。

    未知字段同样失败，避免上游误以为新字段已经被下游执行，也阻止名称伪装的
    运行时秘密悄悄穿越这个可持久化边界。
    """
    present = frozenset(value)
    forbidden = sorted(
        field for field in present if _normalise_field_name(field) in _FORBIDDEN_SECRET_FIELD_NAMES
    )
    if forbidden:
        raise _error(
            TaskContractReasonCode.SECRET_MATERIAL_FORBIDDEN,
            f"{field_path}.{forbidden[0]}",
            "secret or raw runtime material is forbidden",
        )
    missing = sorted(required - present)
    if missing:
        raise _error(
            TaskContractReasonCode.FIELD_MISSING,
            f"{field_path}.{missing[0]}",
            "required field is missing",
        )
    unexpected = sorted(present - required - optional)
    if unexpected:
        raise _error(
            TaskContractReasonCode.FIELD_UNEXPECTED,
            f"{field_path}.{unexpected[0]}",
            "field is not defined by schema v1",
        )
    _ensure_finite_json(value, field_path)


def _ensure_finite_json(value: object, field_path: str) -> None:
    """验证值可被确定性 JSON 编码，尤其拒绝 NaN/Infinity。

    哈希、审计和跨语言重放都要求相同语义的有限 JSON；非有限浮点数会破坏这一
    假设，因此在契约层而非消费者侧尽早失败。
    """
    try:
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _error(
            TaskContractReasonCode.PAYLOAD_INVALID,
            field_path,
            "must contain finite JSON data only",
        ) from exc


def _require_identifier(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _error(
            TaskContractReasonCode.FIELD_INVALID,
            field_path,
            "must be a 1..255 character identifier without whitespace or control characters",
        )
    return value


def _require_route_type(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _ROUTE_TYPE_RE.fullmatch(value):
        raise _error(
            TaskContractReasonCode.FIELD_INVALID,
            field_path,
            "must be a lower-case bounded route identifier",
        )
    return value


def _require_reference(value: object, field_path: str, *, prefix: str | None = None) -> str:
    """接受受限的持久化引用，拒绝把凭据伪装成 URI 的输入。"""
    if not isinstance(value, str) or not _REFERENCE_RE.fullmatch(value):
        raise _error(
            TaskContractReasonCode.FIELD_INVALID,
            field_path,
            "must be a bounded durable reference without query, fragment, whitespace, or control characters",
        )
    scheme = value.split("://", 1)[0]
    if scheme in _FORBIDDEN_REFERENCE_SCHEMES:
        raise _error(
            TaskContractReasonCode.SECRET_MATERIAL_FORBIDDEN,
            field_path,
            "raw credential/token reference schemes are forbidden",
        )
    if prefix is not None and not value.startswith(prefix):
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, f"must start with {prefix}")
    return value


def _optional_reference(value: object, field_path: str) -> str | None:
    if value is None:
        return None
    return _require_reference(value, field_path)


def _require_epoch_ms(value: object, field_path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _error(
            TaskContractReasonCode.FIELD_INVALID,
            field_path,
            "must be a non-negative integer epoch timestamp in milliseconds",
        )
    return value


def _require_exact_int(value: object, field_path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, f"must be an integer >= {minimum}")
    return value


def _require_bool(value: object, field_path: str) -> bool:
    if not isinstance(value, bool):
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, "must be a boolean")
    return value


def _parse_enum(enum_type: type[StrEnum], value: object, field_path: str) -> Any:
    if not isinstance(value, str):
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, "must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, "value is not supported") from exc


def _require_schema(value: object, *, expected: str, field_path: str) -> str:
    if value != expected:
        raise _error(TaskContractReasonCode.VERSION_UNSUPPORTED, field_path, "schema version is not supported")
    return expected


def _normalise_sha256(value: object, field_path: str) -> str:
    if isinstance(value, str) and _RAW_SHA256_RE.fullmatch(value):
        return f"sha256:{value.lower()}"
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value
    raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, "must be sha256 followed by 64 lower-case hexadecimal characters")


def _require_canonical_sha256(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise _error(TaskContractReasonCode.FIELD_INVALID, field_path, "must use canonical sha256:<64 lower-case hex> form")
    return value


def _command_id(surface: HostCommandSurface, request_id: str) -> str:
    """从入口面与请求号导出稳定命令标识，作为重试时的幂等锚点。"""
    digest = hashlib.sha256(
        json.dumps(
            {"request_id": request_id, "surface": surface.value},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"host-command://{surface.value}/{digest}"


@dataclass(frozen=True, slots=True)
class ExecutorAssignment:
    """一个 WorkOrder 的不可变执行器决策。

    ``assignment_id`` 是已作决策的持久化证据，``is_new_task`` 区分首次派发与
    恢复/复用路径；执行端不应仅凭当前路由配置重新推断这一历史决定。
    """

    work_order_id: str
    executor: TaskExecutor
    assignment_id: str
    assigned_at_ms: int
    is_new_task: bool
    reason_code: str
    schema_version: str = EXECUTOR_ASSIGNMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # 此对象可从事件存储恢复，故构造和反序列化走同一套边界检查。
        _require_schema(self.schema_version, expected=EXECUTOR_ASSIGNMENT_SCHEMA_VERSION, field_path="executor_assignment.schema_version")
        _require_identifier(self.work_order_id, "executor_assignment.work_order_id")
        if not isinstance(self.executor, TaskExecutor):
            raise _error(TaskContractReasonCode.FIELD_INVALID, "executor_assignment.executor", "must be a TaskExecutor")
        _require_reference(
            self.assignment_id,
            "executor_assignment.assignment_id",
            prefix="runtime-assignment://",
        )
        _require_epoch_ms(self.assigned_at_ms, "executor_assignment.assigned_at_ms")
        _require_bool(self.is_new_task, "executor_assignment.is_new_task")
        _require_identifier(self.reason_code, "executor_assignment.reason_code")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutorAssignment":
        """把不可信映射收窄为可安全恢复的执行器分配记录。"""
        data = _require_mapping(payload, "executor_assignment")
        required = frozenset(
            {
                "schema_version",
                "work_order_id",
                "executor",
                "assignment_id",
                "assigned_at_ms",
                "is_new_task",
                "reason_code",
            }
        )
        _check_fields(data, field_path="executor_assignment", required=required)
        try:
            executor = TaskExecutor(data["executor"])
        except (TypeError, ValueError) as exc:
            raise _error(EXECUTOR_ASSIGNMENT_INVALID, "executor_assignment", "executor is invalid") from exc
        return cls(
            work_order_id=_require_identifier(data["work_order_id"], "executor_assignment.work_order_id"),
            executor=executor,
            assignment_id=_require_reference(
                data["assignment_id"],
                "executor_assignment.assignment_id",
                prefix="runtime-assignment://",
            ),
            assigned_at_ms=_require_epoch_ms(data["assigned_at_ms"], "executor_assignment.assigned_at_ms"),
            is_new_task=_require_bool(data["is_new_task"], "executor_assignment.is_new_task"),
            reason_code=_require_identifier(data["reason_code"], "executor_assignment.reason_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        """输出唯一的持久化/传输形态，并在离开边界前再次确认 JSON 有效。"""
        payload = {
            "schema_version": self.schema_version,
            "work_order_id": self.work_order_id,
            "executor": self.executor.value,
            "assignment_id": self.assignment_id,
            "assigned_at_ms": self.assigned_at_ms,
            "is_new_task": self.is_new_task,
            "reason_code": self.reason_code,
        }
        _ensure_finite_json(payload, "executor_assignment")
        return payload


@dataclass(frozen=True, slots=True)
class HostCommand:
    """Task Runtime 的不可变入口：承载授权引用、路由、谱系与幂等性。

    它描述“允许哪个主体把哪份已存输入交给哪条图执行”，而非输入正文或授权
    载荷本身。运行时据此关联审计证据，却不能藉此扩大入口已授予的权限。
    """

    command_id: str
    surface: HostCommandSurface
    request_id: str
    session_id: str
    principal_id: str
    ingress_claim_ref: str | None
    ingress_audit_ref: str | None
    governor_grant_ref: str | None
    input_ref: str
    input_sha256: str
    root_work_order_id: str
    graph_id: str
    graph_revision: int
    route_type: str
    target_id: str
    business_scope: str
    operation_fingerprint: str
    created_at_ms: int
    schema_version: str = HOST_COMMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # command_id 必须由 surface/request_id 导出，防止重放时换用另一命令身份。
        _require_schema(self.schema_version, expected=HOST_COMMAND_SCHEMA_VERSION, field_path="host_command.schema_version")
        if not isinstance(self.surface, HostCommandSurface):
            raise _error(TaskContractReasonCode.FIELD_INVALID, "host_command.surface", "must be a HostCommandSurface")
        request_id = _require_identifier(self.request_id, "host_command.request_id")
        expected_command_id = _command_id(self.surface, request_id)
        _require_reference(self.command_id, "host_command.command_id", prefix="host-command://")
        if self.command_id != expected_command_id:
            raise _error(
                TaskContractReasonCode.FIELD_INVALID,
                "host_command.command_id",
                "does not match the deterministic surface/request identity",
            )
        _require_identifier(self.session_id, "host_command.session_id")
        _require_identifier(self.principal_id, "host_command.principal_id")
        claim = _optional_reference(self.ingress_claim_ref, "host_command.ingress_claim_ref")
        audit = _optional_reference(self.ingress_audit_ref, "host_command.ingress_audit_ref")
        _optional_reference(self.governor_grant_ref, "host_command.governor_grant_ref")
        if (claim is None) != (audit is None):
            # Claim 与其审计证据缺一不可；只保留一方会造成不可追溯的授权断链。
            raise _error(
                TaskContractReasonCode.FIELD_INVALID,
                "host_command.ingress_claim_ref",
                "ingress claim and audit references must be present or absent together",
            )
        _require_reference(self.input_ref, "host_command.input_ref")
        _require_canonical_sha256(self.input_sha256, "host_command.input_sha256")
        _require_identifier(self.root_work_order_id, "host_command.root_work_order_id")
        _require_identifier(self.graph_id, "host_command.graph_id")
        _require_exact_int(self.graph_revision, "host_command.graph_revision", minimum=1)
        _require_route_type(self.route_type, "host_command.route_type")
        _require_identifier(self.target_id, "host_command.target_id")
        _require_identifier(self.business_scope, "host_command.business_scope")
        _require_canonical_sha256(self.operation_fingerprint, "host_command.operation_fingerprint")
        _require_epoch_ms(self.created_at_ms, "host_command.created_at_ms")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HostCommand":
        """从边界输入恢复命令，拒绝版本漂移、额外字段和秘密材料。"""
        data = _require_mapping(payload, "host_command")
        required = frozenset(
            {
                "schema_version",
                "command_id",
                "surface",
                "request_id",
                "session_id",
                "principal_id",
                "input_ref",
                "input_sha256",
                "root_work_order_id",
                "graph_id",
                "graph_revision",
                "route_type",
                "target_id",
                "business_scope",
                "operation_fingerprint",
                "created_at_ms",
            }
        )
        optional = frozenset({"ingress_claim_ref", "ingress_audit_ref", "governor_grant_ref"})
        _check_fields(data, field_path="host_command", required=required, optional=optional)
        surface = _parse_enum(HostCommandSurface, data["surface"], "host_command.surface")
        return cls(
            command_id=_require_reference(data["command_id"], "host_command.command_id", prefix="host-command://"),
            surface=surface,
            request_id=_require_identifier(data["request_id"], "host_command.request_id"),
            session_id=_require_identifier(data["session_id"], "host_command.session_id"),
            principal_id=_require_identifier(data["principal_id"], "host_command.principal_id"),
            ingress_claim_ref=_optional_reference(data.get("ingress_claim_ref"), "host_command.ingress_claim_ref"),
            ingress_audit_ref=_optional_reference(data.get("ingress_audit_ref"), "host_command.ingress_audit_ref"),
            governor_grant_ref=_optional_reference(data.get("governor_grant_ref"), "host_command.governor_grant_ref"),
            input_ref=_require_reference(data["input_ref"], "host_command.input_ref"),
            input_sha256=_require_canonical_sha256(data["input_sha256"], "host_command.input_sha256"),
            root_work_order_id=_require_identifier(data["root_work_order_id"], "host_command.root_work_order_id"),
            graph_id=_require_identifier(data["graph_id"], "host_command.graph_id"),
            graph_revision=_require_exact_int(data["graph_revision"], "host_command.graph_revision", minimum=1),
            route_type=_require_route_type(data["route_type"], "host_command.route_type"),
            target_id=_require_identifier(data["target_id"], "host_command.target_id"),
            business_scope=_require_identifier(data["business_scope"], "host_command.business_scope"),
            operation_fingerprint=_require_canonical_sha256(
                data["operation_fingerprint"], "host_command.operation_fingerprint"
            ),
            created_at_ms=_require_epoch_ms(data["created_at_ms"], "host_command.created_at_ms"),
        )

    def to_dict(self) -> dict[str, Any]:
        """生成可持久化的命令快照；引用仍指向外部受控存储而非正文。"""
        payload = {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "surface": self.surface.value,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "ingress_claim_ref": self.ingress_claim_ref,
            "ingress_audit_ref": self.ingress_audit_ref,
            "governor_grant_ref": self.governor_grant_ref,
            "input_ref": self.input_ref,
            "input_sha256": self.input_sha256,
            "root_work_order_id": self.root_work_order_id,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "route_type": self.route_type,
            "target_id": self.target_id,
            "business_scope": self.business_scope,
            "operation_fingerprint": self.operation_fingerprint,
            "created_at_ms": self.created_at_ms,
        }
        _ensure_finite_json(payload, "host_command")
        return payload


def build_host_command(
    *,
    surface: HostCommandSurface | str,
    request_id: str,
    session_id: str,
    principal_id: str,
    input_ref: str,
    input_sha256: str,
    root_work_order_id: str,
    graph_id: str,
    graph_revision: int,
    route_type: str,
    target_id: str,
    business_scope: str,
    operation_fingerprint: str,
    created_at_ms: int,
    ingress_claim_ref: str | None = None,
    ingress_audit_ref: str | None = None,
    governor_grant_ref: str | None = None,
) -> HostCommand:
    """构造规范命令，并以入口面和请求号固定重放时的命令身份。

    此处接受裸 SHA-256 仅是为了兼容调用方，输出始终归一为带 ``sha256:`` 前缀的
    形式；其余验证仍由 ``HostCommand`` 集中执行，避免构造路径绕过安全边界。
    """

    try:
        parsed_surface = surface if isinstance(surface, HostCommandSurface) else HostCommandSurface(str(surface))
    except ValueError as exc:
        raise _error(TaskContractReasonCode.FIELD_INVALID, "host_command.surface", "value is not supported") from exc
    request_ref = _require_identifier(request_id, "host_command.request_id")
    return HostCommand(
        command_id=_command_id(parsed_surface, request_ref),
        surface=parsed_surface,
        request_id=request_ref,
        session_id=session_id,
        principal_id=principal_id,
        ingress_claim_ref=ingress_claim_ref,
        ingress_audit_ref=ingress_audit_ref,
        governor_grant_ref=governor_grant_ref,
        input_ref=input_ref,
        input_sha256=_normalise_sha256(input_sha256, "host_command.input_sha256"),
        root_work_order_id=root_work_order_id,
        graph_id=graph_id,
        graph_revision=graph_revision,
        route_type=route_type,
        target_id=target_id,
        business_scope=business_scope,
        operation_fingerprint=_normalise_sha256(
            operation_fingerprint, "host_command.operation_fingerprint"
        ),
        created_at_ms=created_at_ms,
    )


__all__ = [
    "EXECUTOR_ASSIGNMENT_INVALID",
    "EXECUTOR_ASSIGNMENT_SCHEMA_VERSION",
    "HOST_COMMAND_SCHEMA_VERSION",
    "ExecutorAssignment",
    "HostCommand",
    "HostCommandSurface",
    "TaskContractError",
    "TaskContractReasonCode",
    "TaskExecutor",
    "build_host_command",
]
