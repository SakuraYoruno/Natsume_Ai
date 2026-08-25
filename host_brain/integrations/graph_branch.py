"""只读并行分支执行器（M37-A）。

实现只读并行分支（Read-Only Parallel Branches，ModelPlane.md §10 Rule 4）：
- BranchSpec：分支规格数据类
- BranchResult：分支执行结果数据类
- BranchManifest：分支清单数据类
- run_parallel_branches()：并行执行所有分支，返回 BranchManifest
- build_branch_manifest_dict()：将 BranchManifest 转换为字典

设计原则：
- 分支节点不能写入 committed_* 字段（只读约束，由 _strip_committed_fields 强制执行）
- 分支节点不能修改主图 trace、status、reason_code 等核心字段
- 分支结果只能写入 branch_manifest
- 分支失败不影响主图执行（静默降级）
- 使用 ThreadPoolExecutor 并行执行，互不影响
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from host_brain.integrations.graph_contract import (
    BRANCH_STATUS_FAILED,
    BRANCH_STATUS_OK,
    BARRIER_JOIN_POLICY_STRICT,
    DEFAULT_BRANCH_MAX_WORKERS,
    GraphState,
)

# 分支节点处理器类型：接收 payload 和 ctx，返回 output dict
BranchHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class BranchSpec:
# 分支规格只携带独立输入与处理器；它不是主图状态的可写视图。
    """分支规格：定义一个只读并行分支。"""
    branch_id: str
    handler: BranchHandler
    input_payload: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5_000


@dataclass
class BranchResult:
# 分支结果先封装再汇总，避免工作线程直接竞争写入主图。
    """分支执行结果。"""
    branch_id: str
    status: str  # "OK" | "FAILED"
    output: dict[str, Any] = field(default_factory=dict)
    exec_duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "status": self.status,
            "output": self.output,
            "exec_duration_ms": self.exec_duration_ms,
            "error": self.error,
        }


@dataclass
class BranchManifest:
# 清单是并行工作回流主图的唯一容器，计数可帮助上层决定严格或部分汇合。
    """分支清单：所有分支的执行结果汇总。"""
    branches: list[BranchResult] = field(default_factory=list)
    total_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    committed_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return build_branch_manifest_dict(self)


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def run_parallel_branches(
    branch_specs: list[BranchSpec],
    state: GraphState,
    max_workers: int = DEFAULT_BRANCH_MAX_WORKERS,
) -> BranchManifest:
    # 空分支返回完整空清单，令下游 fan-in 无需用特殊值推断是否执行过。
    """并行执行所有只读分支，返回 BranchManifest。

    Args:
        branch_specs: 分支规格列表
        state: 当前主图状态（只读，分支不能修改）
        max_workers: 最大并行线程数（默认 4）

    Returns:
        BranchManifest（含所有分支的执行结果）
    """
    if not branch_specs:
        return BranchManifest(
            branches=[],
            total_count=0,
            success_count=0,
            fail_count=0,
            committed_at_ms=int(time.time() * 1000),
        )

    results: list[BranchResult] = []
    request_id = str(state.get("request_id") or "")
    graph_id = str(state.get("graph_id") or "")

    with ThreadPoolExecutor(max_workers=min(max_workers, len(branch_specs))) as executor:
        # 每个 future 只接受复制后的输入；主图状态不会在线程间共享写入。
        future_to_spec = {
            executor.submit(_execute_single_branch, spec, request_id, graph_id): spec
            for spec in branch_specs
        }
        for future in as_completed(future_to_spec):
            result = future.result()  # _execute_single_branch 内部已捕获异常
            results.append(result)

    # 按 branch_id 排序，保证输出确定性
    # 按稳定身份排序，消除 future 完成先后带来的可观测顺序抖动。
    results.sort(key=lambda r: r.branch_id)

    success_count = sum(1 for r in results if r.status == BRANCH_STATUS_OK)
    fail_count = len(results) - success_count

    return BranchManifest(
        branches=results,
        total_count=len(results),
        success_count=success_count,
        fail_count=fail_count,
        committed_at_ms=int(time.time() * 1000),
    )


def build_branch_manifest_dict(manifest: BranchManifest) -> dict[str, Any]:
    """将 BranchManifest 转换为字典，供 GraphState.branch_manifest 写入。

    Args:
        manifest: BranchManifest 实例

    Returns:
        字典（含 branches、total_count、success_count、fail_count、committed_at_ms）
    """
    return {
        "branches": [r.to_dict() for r in manifest.branches],
        "total_count": manifest.total_count,
        "success_count": manifest.success_count,
        "fail_count": manifest.fail_count,
        "committed_at_ms": manifest.committed_at_ms,
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _execute_single_branch(
    spec: BranchSpec,
    request_id: str,
    graph_id: str,
) -> BranchResult:
    # 捕获异常并将其局限在本分支：并行辅助失败不能中断主图或其他只读分支。
    """执行单个分支（内部函数，异常安全）。

    Args:
        spec: 分支规格
        request_id: 请求 ID（用于 ctx）
        graph_id: 图 ID（用于 ctx）

    Returns:
        BranchResult（无论成功或失败）
    """
    start_ms = int(time.time() * 1000)
    try:
        ctx = {
            "request_id": request_id,
            "graph_id": graph_id,
            "branch_id": spec.branch_id,
            "timeout_ms": spec.timeout_ms,
            "readonly": True,  # 明确标记为只读分支
        }
        output = spec.handler(dict(spec.input_payload), ctx) or {}
        # 即使处理器试图返回治理字段，也必须在跨越并行边界前移除。
        # 只读约束：移除 committed_* 字段
        _strip_committed_fields_from_branch(output)
        end_ms = int(time.time() * 1000)
        return BranchResult(
            branch_id=spec.branch_id,
            status=BRANCH_STATUS_OK,
            output=dict(output),
            exec_duration_ms=max(0, end_ms - start_ms),
            error=None,
        )
    except Exception as exc:
        end_ms = int(time.time() * 1000)
        return BranchResult(
            branch_id=spec.branch_id,
            status=BRANCH_STATUS_FAILED,
            output={},
            exec_duration_ms=max(0, end_ms - start_ms),
            error=f"{type(exc).__name__}: {exc}",
        )


def _strip_committed_fields_from_branch(output: dict[str, Any]) -> None:
    # ``committed_*`` 属于 Host Governor 控制面，分支结果永远无权伪造或覆盖它。
    """只读约束：从分支节点输出中移除 committed_* 字段。

    分支节点不允许写入 committed_* 字段（只有 Host Governor 可以写入）。
    """
    if not isinstance(output, dict):
        return
    committed_keys = [k for k in list(output.keys()) if str(k or "").startswith("committed_")]
    for key in committed_keys:
        del output[key]


# ---------------------------------------------------------------------------
# M40-A：AuditTicket、BarrierJoinResult、barrier_join()
# ---------------------------------------------------------------------------

@dataclass
class AuditTicket:
# fan-out 前签发的工单将输入摘要绑定到分支身份，为后续 fan-in 提供独立证据。
    """审计工单：在 fan-out 之前由 Host 签发，携带分支执行的审计凭证。

    每个并行分支拿到自己的 AuditTicket，只允许在 ticket 定义范围内执行。
    fan-in 时 barrier_join() 校验所有分支是否存在 audit_commit。
    """
    branch_id: str
    audit_commit: str = field(default_factory=lambda: "")
    input_digest: str = ""
    output_digest: str = ""
    issued_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "audit_commit": self.audit_commit,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "issued_at_ms": self.issued_at_ms,
        }


@dataclass
class BarrierJoinResult:
# 汇合结果显式表达证据缺口，避免把“部分完成”误标为可信的全量成功。
    """barrier_join 结果：fan-in 校验结果。

    passed=True：所有分支均有 audit_commit，校验通过
    passed=False：至少一个分支缺 audit_commit，fail closed
    """
    passed: bool
    failed_branches: list[str] = field(default_factory=list)
    join_policy_ref: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_branches": list(self.failed_branches),
            "join_policy_ref": self.join_policy_ref,
            "reason": self.reason,
        }


def build_audit_ticket(
    *,
    branch_id: str,
    input_payload: dict[str, Any] | None = None,
) -> AuditTicket:
    # 摘要而非原始输入进入工单，既可关联完整性又避免在审计面复制敏感载荷。
    """构建审计工单（在 fan-out 之前由 Host 签发）。

    Args:
        branch_id: 分支 ID
        input_payload: 分支输入 payload（用于计算 input_digest）

    Returns:
        AuditTicket（含 branch_id、audit_commit、input_digest）
    """
    import hashlib
    import json as _json

    payload = dict(input_payload or {})
    payload_json = _json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    input_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    # audit_commit：branch_id + input_digest + issued_at_ms 的 SHA-256
    issued_at_ms = int(time.time() * 1000)
    raw = f"{branch_id}::{input_digest}::{issued_at_ms}"
    audit_commit = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return AuditTicket(
        branch_id=str(branch_id),
        audit_commit=audit_commit,
        input_digest=input_digest,
        output_digest="",
        issued_at_ms=issued_at_ms,
    )


def barrier_join(
    *,
    branch_manifest: dict[str, Any] | None,
    audit_tickets: list[AuditTicket] | None = None,
    join_policy: str = BARRIER_JOIN_POLICY_STRICT,
) -> BarrierJoinResult:
    # 空清单代表没有 fan-out 工作，直接通过；它不同于“存在分支但缺少证据”。
    """并行分支 fan-in 校验（M40-A）。

    校验所有分支是否存在 audit_commit，缺任一证据则 fail closed（strict 模式）。
    若允许部分成功（partial 模式），必须记录 join_policy_ref。

    Args:
        branch_manifest: branch_manifest 字典（来自 GraphState.branch_manifest）
        audit_tickets: 审计工单列表（可选，若提供则校验 output_digest 一致性）
        join_policy: 校验策略（strict / partial）

    Returns:
        BarrierJoinResult（passed=True 或 passed=False + failed_branches）
    """
    # 空 branch_manifest：无分支无需校验
    if not branch_manifest:
        return BarrierJoinResult(
            passed=True,
            failed_branches=[],
            join_policy_ref="",
            reason="无分支，无需校验",
        )

    branches = list(branch_manifest.get("branches") or [])
    if not branches:
        return BarrierJoinResult(
            passed=True,
            failed_branches=[],
            join_policy_ref="",
            reason="无分支，无需校验",
        )

    # 构建 audit_tickets 索引（branch_id → AuditTicket）
    ticket_index: dict[str, AuditTicket] = {}
    if audit_tickets:
        for ticket in audit_tickets:
            ticket_index[str(ticket.branch_id)] = ticket

    failed_branches: list[str] = []

    for branch in branches:
        branch_id = str(branch.get("branch_id") or "")
        branch_output = dict(branch.get("output") or {})

        # 校验 audit_commit 是否存在
        audit_commit = str(branch_output.get("audit_commit") or "").strip()
        if not audit_commit:
            # 分支未携带提交证据时仅可由同身份的预签发工单补足，不能猜测成功。
            # 若有对应的 AuditTicket，也视为有 audit_commit
            if branch_id in ticket_index and ticket_index[branch_id].audit_commit:
                audit_commit = ticket_index[branch_id].audit_commit
            else:
                failed_branches.append(branch_id)
                continue

        # 若提供了 audit_tickets，校验 output_digest 一致性
        if branch_id in ticket_index:
            ticket = ticket_index[branch_id]
            if ticket.output_digest:
                # 计算实际 output_digest
                import hashlib
                import json as _json
                output_json = _json.dumps(branch_output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                actual_digest = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
                if actual_digest != ticket.output_digest:
                    # 输出与登记摘要不一致视为证据失效，严格策略下必须阻断汇合。
                    failed_branches.append(branch_id)
                    continue

    if not failed_branches:
        return BarrierJoinResult(
            passed=True,
            failed_branches=[],
            join_policy_ref="",
            reason="所有分支均有 audit_commit，校验通过",
        )

    # 有失败分支
    if join_policy == BARRIER_JOIN_POLICY_STRICT:
        # 严格模式不接受任何缺口，避免不完整的并行结论影响主图后续决定。
        return BarrierJoinResult(
            passed=False,
            failed_branches=failed_branches,
            join_policy_ref="",
            reason=f"strict 模式：{len(failed_branches)} 个分支缺 audit_commit，fail closed",
        )
    else:
        # 部分模式仍返回未通过，只额外留下可审计引用让上层显式决定是否继续。
        # partial 模式：允许继续流程，但 barrier_join 结果仍判定为未完全通过。
        # 上层可基于 join_policy_ref 审计“带缺口放行”的决策。
        join_policy_ref = f"partial::{int(time.time() * 1000)}::failed={','.join(failed_branches)}"
        return BarrierJoinResult(
            passed=False,
            failed_branches=failed_branches,
            join_policy_ref=join_policy_ref,
            reason=f"partial 模式：{len(failed_branches)} 个分支缺 audit_commit，已记录 join_policy_ref",
        )
