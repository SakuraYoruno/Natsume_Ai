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
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from host_brain.integrations.graph_contract import (
    BRANCH_STATUS_FAILED,
    BRANCH_STATUS_OK,
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
    """分支规格：定义一个只读并行分支。"""
    branch_id: str
    handler: BranchHandler
    input_payload: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5_000


@dataclass
class BranchResult:
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
        future_to_spec = {
            executor.submit(_execute_single_branch, spec, request_id, graph_id): spec
            for spec in branch_specs
        }
        for future in as_completed(future_to_spec):
            result = future.result()  # _execute_single_branch 内部已捕获异常
            results.append(result)

    # 按 branch_id 排序，保证输出确定性
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
    """只读约束：从分支节点输出中移除 committed_* 字段。

    分支节点不允许写入 committed_* 字段（只有 Host Governor 可以写入）。
    """
    if not isinstance(output, dict):
        return
    committed_keys = [k for k in list(output.keys()) if str(k or "").startswith("committed_")]
    for key in committed_keys:
        del output[key]
