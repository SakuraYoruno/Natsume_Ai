"""Agent Loop：视觉驱动的 UI 自动化闭环。

工作流程：
  1. 截图（ClawdBot）
  2. 分析（夏目 LLM）→ 生成 ActionPlan
  3. 执行（ClawdBot）→ 返回 ActionReport
  4. 判断是否达成目标（goal_achieved 或 DONE 指令）
  5. 未达成 → 回到步骤 1（最多 max_rounds 轮）

设计原则：
  - 每轮循环都通过 SSE 推送进度事件，前端可实时展示
  - ClawdBot 通过 HTTP 回调接口与夏目通信（异步）
  - 支持超时、最大轮数、用户中断等安全机制
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

from .clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionReport,
    ActionStatus,
    ActionType,
)

_logger = logging.getLogger("Natsume.agent_loop")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_MAX_ROUNDS = 5          # 最大循环轮数（防止无限循环）
DEFAULT_ROUND_TIMEOUT_SEC = 30  # 单轮超时（秒）
DEFAULT_TOTAL_TIMEOUT_SEC = 120 # 总超时（秒）


# ---------------------------------------------------------------------------
# ClawdBot 客户端接口（抽象）
# ---------------------------------------------------------------------------

class ClawdbotClient:
    """与 ClawdBot 通信的客户端。

    实际部署时替换为真实的 HTTP/WebSocket 客户端。
    当前提供 mock 实现，用于开发和测试。
    """

    def __init__(self, base_url: str = "http://localhost:7788", timeout_sec: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    async def capture_screenshot(self, session_id: str) -> Dict[str, Any]:
        """请求 ClawdBot 截图，返回 {image_base64, image_mime, width, height}。"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/screenshot",
                    json={"session_id": session_id},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            _logger.warning("ClawdBot 截图失败: %s", exc)
            return {"error": str(exc), "image_base64": "", "image_mime": "image/png"}

    async def execute_plan(self, plan: ActionPlan) -> ActionReport:
        """将 ActionPlan 发送给 ClawdBot 执行，返回 ActionReport。"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/execute",
                    json=plan.model_dump(),
                )
                resp.raise_for_status()
                return ActionReport.model_validate(resp.json())
        except Exception as exc:
            _logger.warning("ClawdBot 执行失败: %s", exc)
            return ActionReport(
                request_id=plan.request_id,
                session_id=plan.session_id,
                status=ActionStatus.FAILED,
                goal_achieved=False,
                total_actions=len(plan.actions),
                failed_count=len(plan.actions),
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Mock ClawdBot 客户端（开发/测试用）
# ---------------------------------------------------------------------------

class MockClawdbotClient(ClawdbotClient):
    """Mock 实现，不发送真实 HTTP 请求，用于开发和单元测试。"""

    async def capture_screenshot(self, session_id: str) -> Dict[str, Any]:
        """返回空截图（测试用）。"""
        await asyncio.sleep(0.05)  # 模拟网络延迟
        return {
            "image_base64": "",
            "image_mime": "image/png",
            "width": 1920,
            "height": 1080,
            "mock": True,
        }

    async def execute_plan(self, plan: ActionPlan) -> ActionReport:
        """模拟执行，所有指令标记为 SUCCESS。"""
        await asyncio.sleep(0.1 * len(plan.actions))  # 模拟执行时间
        from .clawdbot_protocol import ActionResult
        results = [
            ActionResult(
                action_index=i,
                action_type=action.type,
                status=ActionStatus.SUCCESS,
                latency_ms=100,
            )
            for i, action in enumerate(plan.actions)
        ]
        goal_achieved = any(a.type == ActionType.DONE for a in plan.actions)
        return ActionReport(
            request_id=plan.request_id,
            session_id=plan.session_id,
            status=ActionStatus.SUCCESS,
            goal_achieved=goal_achieved,
            results=results,
            total_actions=len(plan.actions),
            success_count=len(plan.actions),
            total_latency_ms=100 * len(plan.actions),
        )


# ---------------------------------------------------------------------------
# Agent Loop 核心
# ---------------------------------------------------------------------------

def _persist(ledger: "SQLiteLedger | None", row: dict) -> None:
    """安全写入 agent_loop_log，失败不影响主流程。"""
    if ledger is None:
        return
    try:
        ledger.log_agent_loop(row)
    except Exception as exc:
        _logger.warning("agent_loop: 持久化写入失败（不影响主流程）: %s", exc)


async def run_agent_loop(
    *,
    request_id: str,
    session_id: str,
    goal: str,
    llm_runtime: Dict[str, Any],
    clawdbot_client: Optional[ClawdbotClient] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    round_timeout_sec: float = DEFAULT_ROUND_TIMEOUT_SEC,
    total_timeout_sec: float = DEFAULT_TOTAL_TIMEOUT_SEC,
    image_upload_allowed: bool = False,
    use_mock: bool = False,
    ledger: "SQLiteLedger | None" = None,
) -> AsyncGenerator[str, None]:
    """Agent Loop SSE 生成器。

    每轮循环推送以下事件：
      LOOP_START      → 循环开始
      ROUND_START     → 本轮开始（含轮次编号）
      SCREENSHOT_DONE → 截图完成
      PLAN_GENERATED  → ActionPlan 生成完毕
      EXECUTE_DONE    → ClawdBot 执行完毕
      ROUND_DONE      → 本轮结束（含是否达成目标）
      LOOP_DONE       → 循环结束（含最终状态）
    """
    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    from .action_planner import generate_action_plan

    # 选择 ClawdBot 客户端
    if clawdbot_client is None:
        clawdbot_client = MockClawdbotClient() if use_mock else ClawdbotClient()

    loop_start_ms = int(time.time() * 1000)
    goal_achieved = False
    final_status = "TIMEOUT"
    round_summaries: List[Dict[str, Any]] = []

    _persist(ledger, {
        "request_id": request_id,
        "session_id": session_id,
        "goal": goal,
        "round": 0,
        "phase": "LOOP_START",
        "status": "RUNNING",
        "goal_achieved": False,
        "ts_ms": loop_start_ms,
    })

    yield _sse({
        "phase": "LOOP_START",
        "request_id": request_id,
        "session_id": session_id,
        "goal": goal,
        "max_rounds": max_rounds,
        "total_timeout_sec": total_timeout_sec,
    })

    try:
        async with asyncio.timeout(total_timeout_sec):
            for round_num in range(1, max_rounds + 1):
                round_start_ms = int(time.time() * 1000)

                yield _sse({
                    "phase": "ROUND_START",
                    "round": round_num,
                    "max_rounds": max_rounds,
                    "request_id": request_id,
                })

                # ── 步骤 1：截图 ──────────────────────────────────────────
                try:
                    screenshot_data = await asyncio.wait_for(
                        clawdbot_client.capture_screenshot(session_id),
                        timeout=round_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    yield _sse({
                        "phase": "ROUND_DONE",
                        "round": round_num,
                        "status": "FAILED",
                        "reason": "SCREENSHOT_TIMEOUT",
                        "request_id": request_id,
                    })
                    final_status = "SCREENSHOT_TIMEOUT"
                    break

                screenshot_error = screenshot_data.get("error")
                yield _sse({
                    "phase": "SCREENSHOT_DONE",
                    "round": round_num,
                    "status": "FAILED" if screenshot_error else "OK",
                    "error": screenshot_error,
                    "mock": screenshot_data.get("mock", False),
                    "request_id": request_id,
                })

                if screenshot_error:
                    final_status = "SCREENSHOT_FAILED"
                    break

                # 将截图注入 vision_context
                vision_context: Dict[str, Any] = {
                    "image_base64": screenshot_data.get("image_base64", ""),
                    "image_mime": screenshot_data.get("image_mime", "image/png"),
                    "resolution": f"{screenshot_data.get('width', 0)}x{screenshot_data.get('height', 0)}",
                }

                # ── 步骤 2：生成 ActionPlan ───────────────────────────────
                try:
                    plan = await asyncio.wait_for(
                        generate_action_plan(
                            request_id=request_id,
                            session_id=session_id,
                            goal=goal,
                            vision_context=vision_context,
                            llm_runtime=llm_runtime,
                            image_upload_allowed=image_upload_allowed,
                            stop_on_error=True,
                            need_screenshot_feedback=(round_num < max_rounds),
                        ),
                        timeout=round_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    yield _sse({
                        "phase": "ROUND_DONE",
                        "round": round_num,
                        "status": "FAILED",
                        "reason": "PLAN_TIMEOUT",
                        "request_id": request_id,
                    })
                    final_status = "PLAN_TIMEOUT"
                    break

                is_fallback = plan.extra.get("fallback", False)
                _persist(ledger, {
                    "request_id": request_id,
                    "session_id": session_id,
                    "goal": goal,
                    "round": round_num,
                    "phase": "PLAN_GENERATED",
                    "status": "FALLBACK" if is_fallback else "OK",
                    "goal_achieved": False,
                    "action_plan_json": plan.model_dump_json(),
                    "action_count": len(plan.actions),
                    "ts_ms": int(time.time() * 1000),
                })

                yield _sse({
                    "phase": "PLAN_GENERATED",
                    "round": round_num,
                    "action_count": len(plan.actions),
                    "goal_understood": plan.extra.get("goal_understood", ""),
                    "confidence": plan.extra.get("confidence", 0.0),
                    "is_fallback": is_fallback,
                    "actions_preview": [
                        {"type": a.type.value, "target_desc": a.target_desc}
                        for a in plan.actions[:5]  # 只预览前5条
                    ],
                    "request_id": request_id,
                })

                # 降级计划直接终止
                if is_fallback:
                    final_status = "PLAN_FAILED"
                    break

                # ── 步骤 3：执行 ActionPlan ───────────────────────────────
                try:
                    report = await asyncio.wait_for(
                        clawdbot_client.execute_plan(plan),
                        timeout=round_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    yield _sse({
                        "phase": "ROUND_DONE",
                        "round": round_num,
                        "status": "FAILED",
                        "reason": "EXECUTE_TIMEOUT",
                        "request_id": request_id,
                    })
                    final_status = "EXECUTE_TIMEOUT"
                    break

                _persist(ledger, {
                    "request_id": request_id,
                    "session_id": session_id,
                    "goal": goal,
                    "round": round_num,
                    "phase": "EXECUTE_DONE",
                    "status": report.status.value,
                    "goal_achieved": report.goal_achieved,
                    "report_json": report.model_dump_json(),
                    "action_count": report.total_actions,
                    "success_count": report.success_count,
                    "failed_count": report.failed_count,
                    "latency_ms": report.total_latency_ms,
                    "error": report.error,
                    "ts_ms": int(time.time() * 1000),
                })

                yield _sse({
                    "phase": "EXECUTE_DONE",
                    "round": round_num,
                    "status": report.status.value,
                    "goal_achieved": report.goal_achieved,
                    "total_actions": report.total_actions,
                    "success_count": report.success_count,
                    "failed_count": report.failed_count,
                    "latency_ms": report.total_latency_ms,
                    "error": report.error,
                    "request_id": request_id,
                })

                round_latency_ms = int(time.time() * 1000) - round_start_ms
                round_summaries.append({
                    "round": round_num,
                    "status": report.status.value,
                    "goal_achieved": report.goal_achieved,
                    "latency_ms": round_latency_ms,
                })

                # ── 步骤 4：判断是否达成目标 ──────────────────────────────
                # 注意：report.status == SUCCESS 仅表示所有指令执行成功，
                # 不代表语义目标达成；目标达成必须由 ClawdBot 的 goal_achieved 字段决定。
                if report.goal_achieved:
                    goal_achieved = True
                    final_status = "SUCCESS"
                    yield _sse({
                        "phase": "ROUND_DONE",
                        "round": round_num,
                        "status": "SUCCESS",
                        "goal_achieved": True,
                        "latency_ms": round_latency_ms,
                        "request_id": request_id,
                    })
                    break

                if report.status == ActionStatus.FAILED and plan.stop_on_error:
                    final_status = "EXECUTE_FAILED"
                    yield _sse({
                        "phase": "ROUND_DONE",
                        "round": round_num,
                        "status": "FAILED",
                        "goal_achieved": False,
                        "reason": report.error or "EXECUTE_FAILED",
                        "latency_ms": round_latency_ms,
                        "request_id": request_id,
                    })
                    break

                # 未达成，继续下一轮
                yield _sse({
                    "phase": "ROUND_DONE",
                    "round": round_num,
                    "status": "CONTINUE",
                    "goal_achieved": False,
                    "latency_ms": round_latency_ms,
                    "request_id": request_id,
                })

            else:
                # 达到最大轮数
                final_status = "MAX_ROUNDS_REACHED"

    except asyncio.TimeoutError:
        final_status = "TOTAL_TIMEOUT"
        _logger.warning("agent_loop: 总超时", extra={"request_id": request_id})

    except Exception as exc:
        final_status = f"INTERNAL_ERROR:{type(exc).__name__}"
        _logger.error("agent_loop: 内部错误 %s", exc, extra={"request_id": request_id})

    total_latency_ms = int(time.time() * 1000) - loop_start_ms

    _persist(ledger, {
        "request_id": request_id,
        "session_id": session_id,
        "goal": goal,
        "round": 0,
        "phase": "LOOP_DONE",
        "status": final_status,
        "goal_achieved": goal_achieved,
        "action_count": len(round_summaries),
        "latency_ms": total_latency_ms,
        "extra_json": json.dumps({"round_summaries": round_summaries}, ensure_ascii=False),
        "ts_ms": int(time.time() * 1000),
    })

    yield _sse({
        "phase": "LOOP_DONE",
        "request_id": request_id,
        "goal": goal,
        "goal_achieved": goal_achieved,
        "final_status": final_status,
        "total_rounds": len(round_summaries),
        "total_latency_ms": total_latency_ms,
        "round_summaries": round_summaries,
    })


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "ClawdbotClient",
    "MockClawdbotClient",
    "run_agent_loop",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_ROUND_TIMEOUT_SEC",
    "DEFAULT_TOTAL_TIMEOUT_SEC",
]
