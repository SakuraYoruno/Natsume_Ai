"""
main_chain.py —— 主聊天链路时序编排器

[审计修复 #4] 已从死代码迁移为 _unified_chat_sse_generator 的适配层。
    通过 MainChain.run_as_sse_adapter() 将请求委托给真实的 SSE 路由逻辑，
    同时保留 ChainContext 审计追踪能力。

    实际路由入口：host_brain/app/chat_handler.py::_unified_chat_sse_generator
    适配层入口：MainChain.run_as_sse_adapter()
    标准调用方式：dependencies.run_chat_via_main_chain()

定义主链路节点顺序：
  Airlock(Quarantine) → Governor → GraphExecutor → RecallFusion
  → LlmGateway → QAContract → EpisodicStore → Audit → Airlock(Sanitizer)

每个节点有标准 TypedDict 输入/输出、最大超时常量和降级策略。
SceneRouter 在 Governor 放行后根据 task_type 分流到专用子链。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..schemas import TaskType
from ..schemas.memory_types import ProposerClass
from ..failure_matrix import FailureHandler, FailureEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 节点超时常量（秒）
# ---------------------------------------------------------------------------

NODE_TIMEOUT: Dict[str, float] = {
    "airlock_quarantine": 1.0,
    "governor": 2.0,
    "graph_executor": 5.0,
    "recall_fusion": 5.0,
    "llm_gateway": 30.0,
    "qa_contract": 3.0,
    "episodic_store": 2.0,
    "audit": 1.0,
    "airlock_sanitizer": 1.0,
}


# ---------------------------------------------------------------------------
# 链路上下文（贯穿所有节点的共享状态）
# ---------------------------------------------------------------------------

@dataclass
class ChainContext:
    """贯穿主链路所有节点的共享上下文对象。"""
    request_id: str
    """请求唯一 ID"""
    user_message: str
    """用户原始消息"""
    session_id: str
    """会话 ID"""
    proposer_class: ProposerClass = ProposerClass.USER
    """发起方类别"""
    task_type: TaskType = TaskType.CHAT
    """任务类型（Governor 放行后由 SceneRouter 确定）"""

    # 各节点填充的中间结果
    quarantine_passed: bool = False
    governor_passed: bool = False
    governor_block_reason: Optional[str] = None
    recall_context_pack: Optional[Any] = None
    llm_raw_output: Optional[str] = None
    qa_rewritten: bool = False
    qa_original_output: Optional[str] = None
    final_response: Optional[str] = None

    # 失败语义
    failure_event: Optional[str] = None
    failure_detail: Optional[str] = None

    # 审计追踪
    node_timings: Dict[str, float] = field(default_factory=dict)
    audit_tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 节点输入/输出 TypedDict
# ---------------------------------------------------------------------------

class AirlockQuarantineInput(TypedDict):
    user_message: str
    session_id: str
    request_id: str


class AirlockQuarantineOutput(TypedDict):
    passed: bool
    sanitized_message: str
    block_reason: Optional[str]


class GovernorInput(TypedDict):
    sanitized_message: str
    session_id: str
    proposer_class: str


class GovernorOutput(TypedDict):
    passed: bool
    task_type: str
    block_reason: Optional[str]


class RecallFusionInput(TypedDict):
    message: str
    session_id: str
    task_type: str


class RecallFusionOutput(TypedDict):
    context_pack: Any
    evidence_empty: bool


class LlmGatewayInput(TypedDict):
    message: str
    context_pack: Any
    task_type: str
    session_id: str


class LlmGatewayOutput(TypedDict):
    raw_output: str
    model_used: str
    is_fallback: bool


class QAContractInput(TypedDict):
    raw_output: str
    task_type: str


class QAContractOutput(TypedDict):
    final_output: str
    rewritten: bool
    original_output: Optional[str]


class EpisodicStoreInput(TypedDict):
    session_id: str
    user_message: str
    assistant_response: str
    task_type: str


class EpisodicStoreOutput(TypedDict):
    stored: bool
    record_id: Optional[str]


class AuditInput(TypedDict):
    context: Dict[str, Any]


class AuditOutput(TypedDict):
    audit_id: str


class AirlockSanitizerInput(TypedDict):
    raw_response: str


class AirlockSanitizerOutput(TypedDict):
    safe_response: str


# ---------------------------------------------------------------------------
# 节点基类
# ---------------------------------------------------------------------------

class ChainNode:
    """链路节点基类，所有节点继承此类。"""

    name: str = "base_node"
    timeout: float = 5.0

    async def execute(self, ctx: ChainContext) -> bool:
        """
        执行节点逻辑。

        Returns:
            True 表示继续执行下一节点，False 表示中止链路。
        """
        raise NotImplementedError

    async def execute_with_timeout(self, ctx: ChainContext) -> bool:
        """带超时保护的节点执行。"""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(self.execute(ctx), timeout=self.timeout)
            ctx.node_timings[self.name] = time.monotonic() - start
            return result
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            ctx.node_timings[self.name] = elapsed
            ctx.failure_event = "MODULE_TIMEOUT"
            ctx.failure_detail = f"节点 {self.name} 超时（{self.timeout}s）"
            logger.warning("节点 %s 超时，已触发 MODULE_TIMEOUT", self.name)
            return False


# ---------------------------------------------------------------------------
# 具体节点实现（骨架，业务逻辑由各模块填充）
# ---------------------------------------------------------------------------

class AirlockQuarantineNode(ChainNode):
    """入口隔离节点：对用户输入进行初步安全检查。"""
    name = "airlock_quarantine"
    timeout = NODE_TIMEOUT["airlock_quarantine"]

    async def execute(self, ctx: ChainContext) -> bool:
        raw_message = str(ctx.user_message or "")
        sanitized_message = raw_message.replace("\x00", "").strip()

        ctx.metadata["sanitized_user_message"] = sanitized_message
        ctx.metadata["airlock_quarantine"] = {
            "input_chars": len(raw_message),
            "output_chars": len(sanitized_message),
            "changed": sanitized_message != raw_message,
        }

        if not sanitized_message:
            ctx.quarantine_passed = False
            ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
            ctx.failure_detail = "Airlock 拒绝空消息输入"
            ctx.audit_tags.append("AIRLOCK_BLOCK_EMPTY_MESSAGE")
            logger.info("[%s] Airlock 拒绝：空消息", ctx.request_id)
            return False

        if sanitized_message != raw_message:
            ctx.audit_tags.append("AIRLOCK_INPUT_SANITIZED")
            ctx.user_message = sanitized_message

        ctx.quarantine_passed = True
        logger.debug("[%s] 入口隔离通过", ctx.request_id)
        return True


class GovernorNode(ChainNode):
    """治理节点：执行 R1-R7 规则检查，决定是否放行请求。"""
    name = "governor"
    timeout = NODE_TIMEOUT["governor"]

    async def execute(self, ctx: ChainContext) -> bool:
        if not ctx.quarantine_passed:
            ctx.governor_passed = False
            ctx.governor_block_reason = "AIRLOCK_NOT_PASSED"
            ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
            ctx.failure_detail = "Governor 前置条件失败：Airlock 未通过"
            ctx.audit_tags.append("GOVERNOR_BLOCK_AIRLOCK")
            logger.info("[%s] Governor 拒绝：Airlock 未通过", ctx.request_id)
            return False

        scene_hint = str(ctx.metadata.get("pre_classified_scene") or "").strip().upper()
        _scene_to_task: Dict[str, TaskType] = {
            "CHAT": TaskType.CHAT,
            "EXEC_INTENT": TaskType.REASONING,
            "WORK_CODE": TaskType.CODE,
            "WORK_TRANSLATE": TaskType.TRANSLATE,
            "VISION_UI": TaskType.CHAT,
        }
        if scene_hint:
            ctx.task_type = _scene_to_task.get(scene_hint, TaskType.CHAT)

        ctx.governor_passed = True
        ctx.governor_block_reason = None
        ctx.metadata["governor"] = {
            "scene_hint": scene_hint or None,
            "task_type": ctx.task_type.value,
        }
        ctx.audit_tags.append("GOVERNOR_PRECHECK_PASSED")
        logger.debug("[%s] Governor 放行 task_type=%s", ctx.request_id, ctx.task_type.value)
        return True


class GraphExecutorNode(ChainNode):
    """图执行节点：执行 LangGraph 子图（工具调用等）。"""
    name = "graph_executor"
    timeout = NODE_TIMEOUT["graph_executor"]

    async def execute(self, ctx: ChainContext) -> bool:
        execution_mode = "chat_single_stage" if ctx.task_type == TaskType.CHAT else "scene_graph_multi_stage"
        ctx.metadata["graph_executor"] = {
            "execution_mode": execution_mode,
            "task_type": ctx.task_type.value,
        }
        if execution_mode != "chat_single_stage":
            ctx.audit_tags.append("GRAPH_EXECUTOR_COMPLEX_PATH")
        logger.debug("[%s] GraphExecutor 执行 mode=%s", ctx.request_id, execution_mode)
        return True


class RecallFusionNode(ChainNode):
    """召回融合节点：执行七路召回并组装 ContextPack。"""
    name = "recall_fusion"
    timeout = NODE_TIMEOUT["recall_fusion"]

    async def execute(self, ctx: ChainContext) -> bool:
        evidence_empty = ctx.recall_context_pack in (None, "", [], {})
        ctx.metadata["recall_fusion"] = {
            "evidence_empty": bool(evidence_empty),
            "context_pack_type": (
                type(ctx.recall_context_pack).__name__ if ctx.recall_context_pack is not None else "none"
            ),
        }
        if evidence_empty:
            ctx.audit_tags.append("RECALL_EVIDENCE_EMPTY")
        logger.debug("[%s] RecallFusion 执行 evidence_empty=%s", ctx.request_id, evidence_empty)
        return True


class LlmGatewayNode(ChainNode):
    """LLM 网关节点：按路由矩阵选择模型并生成响应。"""
    name = "llm_gateway"
    timeout = NODE_TIMEOUT["llm_gateway"]

    async def execute(self, ctx: ChainContext) -> bool:
        has_raw_output = bool(str(ctx.llm_raw_output or "").strip())
        if has_raw_output and not ctx.final_response:
            ctx.final_response = str(ctx.llm_raw_output or "")
        ctx.metadata["llm_gateway"] = {
            "raw_output_present": has_raw_output,
            "final_response_present": bool(str(ctx.final_response or "").strip()),
        }
        if not has_raw_output:
            ctx.audit_tags.append("LLM_GATEWAY_DELEGATED_STREAM")
        logger.debug("[%s] LlmGateway 执行 raw_output_present=%s", ctx.request_id, has_raw_output)
        return True


class QAContractNode(ChainNode):
    """QA 合约节点：验证输出合规性，必要时重写。"""
    name = "qa_contract"
    timeout = NODE_TIMEOUT["qa_contract"]

    async def execute(self, ctx: ChainContext) -> bool:
        raw_output = str(ctx.final_response or ctx.llm_raw_output or "")
        if not raw_output:
            ctx.metadata["qa_contract"] = {
                "status": "SKIPPED_NO_OUTPUT",
                "rewritten": False,
            }
            logger.debug("[%s] QAContract 跳过：无输出", ctx.request_id)
            return True

        normalized_output = raw_output.strip()
        rewritten = normalized_output != raw_output
        ctx.qa_rewritten = rewritten
        ctx.qa_original_output = raw_output if rewritten else None
        ctx.final_response = normalized_output
        ctx.metadata["qa_contract"] = {
            "status": "OK",
            "rewritten": rewritten,
            "output_chars": len(normalized_output),
        }
        if rewritten:
            ctx.audit_tags.append("QA_REWRITE_WHITESPACE_TRIM")
        logger.debug("[%s] QAContract 执行 rewritten=%s", ctx.request_id, rewritten)
        return True


class EpisodicStoreNode(ChainNode):
    """情节记忆存储节点：按宪法规则决定是否写入 EpisodicStore。"""
    name = "episodic_store"
    timeout = NODE_TIMEOUT["episodic_store"]

    async def execute(self, ctx: ChainContext) -> bool:
        can_persist = bool(str(ctx.final_response or "").strip())
        ctx.metadata["episodic_store"] = {
            "status": "READY_TO_PERSIST" if can_persist else "SKIPPED_NO_RESPONSE",
            "task_type": ctx.task_type.value,
            "session_id": ctx.session_id,
        }
        logger.debug("[%s] EpisodicStore 执行 can_persist=%s", ctx.request_id, can_persist)
        return True


class AuditNode(ChainNode):
    """审计节点：向 triptych 写入审计事件。"""
    name = "audit"
    timeout = NODE_TIMEOUT["audit"]

    async def execute(self, ctx: ChainContext) -> bool:
        audit_id = f"audit::{ctx.request_id}::{int(time.time() * 1000)}"
        ctx.metadata["audit"] = {
            "audit_id": audit_id,
            "tag_count": len(ctx.audit_tags),
            "timing_keys": sorted(ctx.node_timings.keys()),
        }
        ctx.audit_tags.append("AUDIT_SNAPSHOT_WRITTEN")
        logger.debug("[%s] Audit 写入 audit_id=%s 标签: %s", ctx.request_id, audit_id, ctx.audit_tags)
        return True


class AirlockSanitizerNode(ChainNode):
    """出口净化节点：对最终响应进行安全过滤。"""
    name = "airlock_sanitizer"
    timeout = NODE_TIMEOUT["airlock_sanitizer"]

    async def execute(self, ctx: ChainContext) -> bool:
        raw_response = str(ctx.final_response or "")
        if not raw_response:
            ctx.metadata["airlock_sanitizer"] = {
                "status": "SKIPPED_NO_RESPONSE",
                "changed": False,
            }
            logger.debug("[%s] 出口净化跳过：无响应", ctx.request_id)
            return True

        safe_response = raw_response.replace("\x00", "").strip()
        changed = safe_response != raw_response
        ctx.final_response = safe_response
        ctx.metadata["airlock_sanitizer"] = {
            "status": "OK",
            "changed": changed,
            "output_chars": len(safe_response),
        }
        if changed:
            ctx.audit_tags.append("AIRLOCK_OUTPUT_SANITIZED")
        logger.debug("[%s] 出口净化完成 changed=%s", ctx.request_id, changed)
        return True


# ---------------------------------------------------------------------------
# 场景路由器
# ---------------------------------------------------------------------------

class ImageUploadPolicy(str, Enum):
    """图片上传策略枚举。

    控制各场景下是否允许将图片（base64/URL）发送给云端 API。

    Attributes:
        ALLOW:           允许上传图片（用户主动分享截图等低敏感场景）。
        DENY:            禁止上传，自动降级为纯文字模式（默认策略）。
        REQUIRE_CONSENT: 需要用户在请求中显式传入 ``image_upload_consent: true``
                         才允许上传，适用于高敏感场景。
    """
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONSENT = "REQUIRE_CONSENT"


class SceneRouter:
    """
    场景路由器：在 Governor 放行后根据 task_type / scene_type 分流到专用子链。

    主链路节点数量不超过 12 个（需求 1.3）。

    [任务3] 新增 route_sse() 方法，将 chat_handler.py 的 if-else 场景分流逻辑
    迁入 SceneRouter，实现真正的节点化路由。调用方通过 route_sse() 获取对应场景
    的 SSE 生成器，而不是在 chat_handler.py 中硬编码 if-else。

    场景路由优先级（从高到低）：
      SCENE_VISION_UI  → _vision_ui_sse_generator（视觉分析链路）
      SCENE_CHAT       → _sse_event_generator（token 级流式，含 Gaming Edge 分支）
      SCENE_WORK_CODE  → _complex_scene_sse_generator（LangGraph 多节点）
      SCENE_WORK_TRANSLATE → _complex_scene_sse_generator
      SCENE_EXEC_INTENT    → _complex_scene_sse_generator
      其他             → _complex_scene_sse_generator（兜底）

    图片上传策略（ImageUploadPolicy）：
      每个场景可独立配置图片上传策略，通过 register_image_upload_policy() 注册。
      默认策略为 DENY（禁止上传），可通过 get_image_upload_policy() 查询。

      内置默认策略：
        VISION_UI → REQUIRE_CONSENT（需用户显式授权，屏幕截图属高敏隐私内容）
        其他场景  → DENY（禁止上传）
    """

    # 专用子链注册表：task_type → 子链节点列表（供 MainChain.run() 使用）
    _sub_chains: Dict[str, List[ChainNode]] = {}

    # SSE 生成器注册表：scene_type → generator_factory（供 route_sse() 使用）
    # 格式：{scene_type: callable}，callable 接收统一关键字参数并返回 AsyncGenerator
    _sse_generators: Dict[str, Any] = {}

    # 图片上传策略注册表：scene_type → ImageUploadPolicy
    # 未注册的场景默认使用 DENY（禁止上传）
    _image_upload_policies: Dict[str, ImageUploadPolicy] = {
        # SCENE_VISION_UI：需要用户显式授权（屏幕截图属高敏隐私内容）
        "VISION_UI": ImageUploadPolicy.REQUIRE_CONSENT,
    }

    @classmethod
    def register_image_upload_policy(
        cls,
        scene_type: str,
        policy: ImageUploadPolicy,
    ) -> None:
        """注册场景的图片上传策略。

        Args:
            scene_type: 场景类型字符串（如 "VISION_UI"、"SCENE_WORK_CODE"）。
                        自动规范化为大写并去除 "SCENE_" 前缀。
            policy:     图片上传策略（ALLOW / DENY / REQUIRE_CONSENT）。

        示例::

            # 允许 VISION_UI 场景上传图片（用户已在产品层面授权）
            SceneRouter.register_image_upload_policy("VISION_UI", ImageUploadPolicy.ALLOW)

            # 自定义场景需要用户显式授权
            SceneRouter.register_image_upload_policy(
                "MY_CUSTOM_SCENE", ImageUploadPolicy.REQUIRE_CONSENT
            )
        """
        normalized = str(scene_type or "").strip().upper().removeprefix("SCENE_")
        cls._image_upload_policies[normalized] = policy
        logger.debug("SceneRouter 注册图片上传策略: %s → %s", normalized, policy.value)

    @classmethod
    def get_image_upload_policy(cls, scene_type: str) -> ImageUploadPolicy:
        """查询场景的图片上传策略。

        未注册的场景默认返回 ``ImageUploadPolicy.DENY``（禁止上传）。

        Args:
            scene_type: 场景类型字符串（大小写不敏感，自动规范化）。

        Returns:
            对应场景的 ImageUploadPolicy 枚举值。
        """
        normalized = str(scene_type or "").strip().upper().removeprefix("SCENE_")
        return cls._image_upload_policies.get(normalized, ImageUploadPolicy.DENY)

    @classmethod
    def check_image_upload_allowed(
        cls,
        scene_type: str,
        *,
        user_consent: bool = False,
    ) -> tuple[bool, str]:
        """检查当前场景下是否允许上传图片。

        Args:
            scene_type:   场景类型字符串。
            user_consent: 用户是否在请求中显式传入了 ``image_upload_consent: true``。

        Returns:
            (allowed: bool, reason: str)
              - allowed=True  → 可以上传图片
              - allowed=False → 禁止上传，reason 说明原因（用于日志/降级提示）

        示例::

            allowed, reason = SceneRouter.check_image_upload_allowed(
                "VISION_UI", user_consent=request_body.image_upload_consent
            )
            if not allowed:
                # 降级为纯文字模式
                ...
        """
        policy = cls.get_image_upload_policy(scene_type)

        if policy == ImageUploadPolicy.ALLOW:
            return True, "POLICY_ALLOW"

        if policy == ImageUploadPolicy.DENY:
            return False, "POLICY_DENY:图片上传在当前场景被禁止"

        if policy == ImageUploadPolicy.REQUIRE_CONSENT:
            if user_consent:
                return True, "POLICY_CONSENT_GRANTED"
            return (
                False,
                "POLICY_CONSENT_REQUIRED:屏幕截图属高敏隐私内容，"
                "请在请求中传入 image_upload_consent=true 以明确授权",
            )

        # 未知策略，保守拒绝
        return False, f"POLICY_UNKNOWN:{policy}"

    @classmethod
    def register_sub_chain(cls, task_type: TaskType, nodes: List[ChainNode]) -> None:
        """注册专用子链（供 MainChain.run() 使用）。"""
        cls._sub_chains[task_type.value] = nodes

    @classmethod
    def register_sse_generator(cls, scene_type: str, generator_factory: Any) -> None:
        """注册 SSE 生成器工厂（供 route_sse() 使用）。

        Args:
            scene_type: 场景类型字符串（如 "SCENE_WORK_CODE"）
            generator_factory: 异步生成器工厂函数，接收统一关键字参数
        """
        cls._sse_generators[str(scene_type).strip().upper()] = generator_factory
        logger.debug("SceneRouter 注册 SSE 生成器: %s", scene_type)

    @classmethod
    def get_sub_chain(cls, task_type: TaskType) -> Optional[List[ChainNode]]:
        """获取专用子链，不存在则返回 None（走主链路）。"""
        return cls._sub_chains.get(task_type.value)

    @classmethod
    def route(cls, ctx: ChainContext) -> List[ChainNode]:
        """
        根据 task_type 返回应执行的节点列表（供 MainChain.run() 使用）。

        普通 chat 走主链路；其他类型若有注册子链则走子链。
        """
        if ctx.task_type == TaskType.CHAT:
            return []  # 空列表表示走默认主链路
        sub_chain = cls.get_sub_chain(ctx.task_type)
        if sub_chain:
            logger.info("[%s] SceneRouter 分流到 %s 子链", ctx.request_id, ctx.task_type)
            return sub_chain
        return []  # 未注册子链，走主链路

    @classmethod
    async def route_sse(
        cls,
        *,
        scene_type: str,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: Dict[str, Any],
        scene_type_source: str,
        governor_result: Dict[str, Any],
        emotion_context: Dict[str, Any],
        emotion_snapshot: Dict[str, Any],
        intent_id: Any,
        reason_code: Any,
        metrics: Any,
        # SCENE_CHAT 专用参数
        sys_mode: str = "normal",
        gaming_edge_enabled: bool = False,
    ):
        """[任务3] 根据 scene_type 分流到对应的 SSE 生成器。

        将 chat_handler.py 中的 if-else 场景分流逻辑迁入 SceneRouter，
        实现真正的节点化路由。调用方只需传入 scene_type，SceneRouter 负责
        选择并调用正确的 SSE 生成器。

        路由优先级：
          1. 已注册的自定义生成器（_sse_generators 注册表）
          2. 内置场景路由（VISION_UI / CHAT / 复杂场景）

        Args:
            scene_type: 场景类型（如 "SCENE_CHAT"、"SCENE_WORK_CODE"）
            request_body: ChatRequest 实例
            request_id: 请求唯一 ID
            session_id: 会话 ID
            ts_ms: 请求时间戳（毫秒）
            intent_draft: Governor 意图草稿
            scene_type_source: 场景类型来源（"pre_classifier"/"explicit" 等）
            governor_result: Governor 鉴权结果
            emotion_context: 情感上下文
            emotion_snapshot: 情感快照
            intent_id: 意图 ID
            reason_code: 原因码
            metrics: MetricsCollector 实例
            sys_mode: 系统模式（"normal"/"gaming"）
            gaming_edge_enabled: 是否启用 Gaming Edge 路由

        Yields:
            SSE 事件字符串
        """
        # 延迟导入，避免循环依赖
        from host_brain.app.chat_stream_handler import _sse_event_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_edge import (  # noqa: PLC0415
            _gaming_edge_chat_sse_generator,
            _parse_bool,
        )
        from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_vision import _vision_ui_sse_generator  # noqa: PLC0415
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            SCENE_CHAT,
            SCENE_VISION_UI,
        )

        normalized_scene = str(scene_type or "").strip().upper()

        # ── 1. 检查自定义注册表 ────────────────────────────────────────────
        custom_generator = cls._sse_generators.get(normalized_scene)
        if custom_generator is not None:
            logger.info(
                "[%s] SceneRouter 使用自定义生成器: %s",
                request_id, normalized_scene,
            )
            async for event in custom_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                scene_type=normalized_scene,
                scene_type_source=scene_type_source,
                governor_result=governor_result,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                intent_id=intent_id,
                reason_code=reason_code,
                metrics=metrics,
            ):
                yield event
            return

        # ── 2. SCENE_VISION_UI：视觉分析链路 ──────────────────────────────
        if normalized_scene == SCENE_VISION_UI:
            logger.info("[%s] SceneRouter → SCENE_VISION_UI", request_id)
            async for event in _vision_ui_sse_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                scene_type=normalized_scene,
                scene_type_source=scene_type_source,
                governor_result=governor_result,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                intent_id=intent_id,
                reason_code=reason_code,
                metrics=metrics,
            ):
                yield event
            return

        # ── 3. SCENE_CHAT：token 级流式（含 Gaming Edge 分支）─────────────
        if normalized_scene == SCENE_CHAT:
            import os as _os  # noqa: PLC0415
            _sys_mode = str(_os.environ.get("HOST_SYS_MODE") or sys_mode).strip().lower() or "normal"
            _gaming_edge_chat_enabled = _parse_bool(
                _os.environ.get("HOST_GAMING_EDGE_CHAT_ENABLED"),
                default=False,
            )
            _host_edge_rpc_enabled = _parse_bool(
                _os.environ.get("HOST_ENABLE_EDGE_RPC"),
                default=True,
            )
            _use_gaming_edge = bool(
                _sys_mode == "gaming"
                and _gaming_edge_chat_enabled
                and _host_edge_rpc_enabled
            )
            if _use_gaming_edge:
                logger.info("[%s] SceneRouter → SCENE_CHAT (Gaming Edge)", request_id)
                async for event in _gaming_edge_chat_sse_generator(
                    request_body=request_body,
                    request_id=request_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
                    governor_result=governor_result,
                    emotion_context=emotion_context,
                    emotion_snapshot=emotion_snapshot,
                    intent_id=intent_id,
                    reason_code=reason_code,
                    sys_mode=_sys_mode,
                    metrics=metrics,
                ):
                    yield event
                return

            logger.info("[%s] SceneRouter → SCENE_CHAT (token stream)", request_id)
            async for event in _sse_event_generator(
                message=request_body.message,
                session_id=session_id,
                thread_id=request_body.thread_id,
                mood_state=request_body.mood_state,
                emotion_tag=request_body.emotion_tag,
                request_id=request_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                _pre_approved_governor_result=governor_result,
            ):
                yield event
            return

        # ── 4. 复杂场景：LangGraph 多节点（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / 兜底）
        logger.info("[%s] SceneRouter → 复杂场景: %s", request_id, normalized_scene)
        async for event in _complex_scene_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type=normalized_scene,
            scene_type_source=scene_type_source,
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
            metrics=metrics,
        ):
            yield event


# ---------------------------------------------------------------------------
# 主链路编排器
# ---------------------------------------------------------------------------

class MainChain:
    """
    主聊天链路时序编排器。

    固定节点顺序（需求 1.1）：
      AirlockQuarantine → Governor → GraphExecutor → RecallFusion
      → LlmGateway → QAContract → EpisodicStore → Audit → AirlockSanitizer

    任意节点失败时统一走 FailureHandler（需求 1.2）。
    """

    # 主链路节点工厂（类级别只读元组，不可变，避免共享可变状态）
    # 注意：不在此处实例化节点，而是在 __init__ 中为每个实例创建独立的节点列表，
    # 防止多个 MainChain 实例共享同一可变列表导致并发污染。
    _NODE_FACTORIES: tuple = (
        AirlockQuarantineNode,
        GovernorNode,
        GraphExecutorNode,
        RecallFusionNode,
        LlmGatewayNode,
        QAContractNode,
        EpisodicStoreNode,
        AuditNode,
        AirlockSanitizerNode,
    )
    # Backward-compatible class-level chain definition for callers/tests that
    # read MainChain.MAIN_NODES directly (for example node-count assertions).
    MAIN_NODES: tuple[type[ChainNode], ...] = _NODE_FACTORIES

    def __init__(self, failure_handler: Optional[Callable] = None) -> None:
        """
        Args:
            failure_handler: 统一失败处理回调，签名为 (event: str, ctx: ChainContext) -> None。
                             若未传入，则自动创建默认 FailureHandler 实例。
        """
        # 修复：为每个实例创建独立的节点列表，避免类变量共享可变列表导致并发污染。
        # 原实现 MAIN_NODES 是类级别可变列表，所有实例共享同一对象，
        # 若某处代码修改 MainChain.MAIN_NODES 会影响所有实例。
        self.MAIN_NODES: List[ChainNode] = [factory() for factory in self._NODE_FACTORIES]

        if failure_handler is not None:
            self._failure_handler = failure_handler
        else:
            # 默认创建 FailureHandler 实例，确保链路失败时有统一处理
            _default_handler = FailureHandler()
            self._failure_handler = _default_handler.handle
            logger.info("MainChain 使用默认 FailureHandler（无审计写入）")

    async def run(self, ctx: ChainContext) -> ChainContext:
        """
        执行主链路。

        Governor 放行后由 SceneRouter 决定是否分流；
        任意节点返回 False 或抛出异常时，调用 failure_handler 并终止链路。

        注意：使用 while 循环 + 索引而非 for-in 迭代，
        确保 Governor 放行后替换子链时新节点能被正确执行。
        （for-in 迭代器绑定原始列表对象，重新赋值 nodes 不会影响迭代，
        导致子链节点永远不会被执行。）
        """
        # 确定执行节点列表（使用 while + 索引，支持运行时替换子链）
        nodes: List[ChainNode] = list(self.MAIN_NODES)
        idx = 0

        while idx < len(nodes):
            node = nodes[idx]
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value  # 使用枚举值字符串，与 FailureHandler 解析保持一致
                ctx.failure_detail = f"节点 {node.name} 未捕获异常: {exc}"
                logger.exception("节点 %s 执行异常", node.name)
                should_continue = False

            if not should_continue:
                # 确保 failure_event 有值
                if not ctx.failure_event:
                    ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                    ctx.failure_detail = f"节点 {node.name} 返回 False 但未设置 failure_event"

                # 统一失败处理
                # 修复：_failure_handler 可能是同步或异步函数，需自动检测并适配，
                # 防止注入同步 Callable 时 await 报错（TypeError: object NoneType can't be used in 'await' expression）
                if self._failure_handler:
                    try:
                        import asyncio as _asyncio
                        if _asyncio.iscoroutinefunction(self._failure_handler):
                            await self._failure_handler(ctx.failure_event, ctx)
                        else:
                            self._failure_handler(ctx.failure_event, ctx)
                    except Exception as fh_exc:
                        logger.error("FailureHandler 执行异常: %s", fh_exc)
                logger.warning(
                    "[%s] 链路在节点 %s 中止，failure_event=%s",
                    ctx.request_id, node.name, ctx.failure_event,
                )
                break

            # Governor 放行后执行场景路由
            if node.name == "governor" and ctx.governor_passed:
                sub_chain = SceneRouter.route(ctx)
                if sub_chain:
                    # 替换后续主链路节点为子链节点
                    # 必须直接修改 nodes 列表（而非重新赋值变量），
                    # 并将 idx 推进到子链起始位置
                    nodes = nodes[: idx + 1] + sub_chain
                    logger.info(
                        "[%s] 已切换到 %s 子链，共 %d 个节点",
                        ctx.request_id, ctx.task_type, len(nodes),
                    )

            idx += 1

        return ctx

    def set_failure_handler(self, handler: Callable) -> None:
        """注入统一失败处理器（任务 7 完成后调用）。"""
        self._failure_handler = handler

    @staticmethod
    def _build_reject_sse(
        *,
        request_id: str,
        reason_code: str,
        detail: str | None = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": reason_code,
            "request_id": request_id,
        }
        if detail:
            payload["detail"] = detail
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _parse_sse_payload(event: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(event, str):
            return None
        text = event.strip()
        if not text.startswith("data:"):
            return None
        payload_raw = text[len("data:"):].strip()
        if not payload_raw:
            return None
        try:
            data = json.loads(payload_raw)
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        return None

    async def _invoke_failure_handler(self, *, ctx: ChainContext) -> None:
        if not self._failure_handler:
            return
        try:
            if asyncio.iscoroutinefunction(self._failure_handler):
                await self._failure_handler(str(ctx.failure_event or FailureEvent.MODULE_TIMEOUT.value), ctx)
            else:
                self._failure_handler(str(ctx.failure_event or FailureEvent.MODULE_TIMEOUT.value), ctx)
        except Exception as fh_exc:
            logger.error("FailureHandler 执行异常: %s", fh_exc)

    def _find_node(self, name: str) -> Optional[ChainNode]:
        for node in self.MAIN_NODES:
            if node.name == name:
                return node
        return None

    async def _run_adapter_nodes(
        self,
        *,
        ctx: ChainContext,
        node_names: tuple[str, ...],
    ) -> bool:
        for node_name in node_names:
            node = self._find_node(node_name)
            if node is None:
                logger.warning("[%s] MainChain 适配层缺少节点: %s", ctx.request_id, node_name)
                continue
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                ctx.failure_detail = f"适配层节点 {node_name} 异常: {exc}"
                logger.exception("MainChain 适配层节点异常: %s", node_name)
                return False
            if not should_continue:
                if not ctx.failure_event:
                    ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
                    ctx.failure_detail = f"适配层节点 {node_name} 返回 False"
                return False
        return True

    @staticmethod
    def _observe_adapter_sse_event(
        *,
        ctx: ChainContext,
        payload: Dict[str, Any],
    ) -> None:
        phase = str(payload.get("phase") or "").strip().upper()
        if not phase:
            return

        observed = ctx.metadata.setdefault("observed_phases", [])
        if isinstance(observed, list) and phase not in observed:
            observed.append(phase)

        if phase == "PRESENCE":
            evt = str(payload.get("event") or "").strip().upper()
            if evt == "GOV_APPROVED":
                ctx.governor_passed = True
            if evt == "SCENE_ROUTED":
                ctx.metadata["scene_type"] = payload.get("scene_type")

        if phase == "GRAPH_START":
            ctx.metadata.setdefault("scene_type", payload.get("scene_type"))

        if phase == "RENDER":
            chunk = str(payload.get("chunk") or "")
            if chunk:
                ctx.metadata["render_chunk_count"] = int(ctx.metadata.get("render_chunk_count") or 0) + 1

        if phase == "COMMIT" and bool(payload.get("done")):
            ctx.metadata["commit_observed"] = True
            decision = str(payload.get("decision") or "").strip().upper()
            if decision:
                ctx.metadata["commit_decision"] = decision
            narrative = payload.get("narrative")
            if narrative is not None:
                ctx.final_response = str(narrative)
            audit_refs = payload.get("audit_refs")
            if isinstance(audit_refs, dict):
                ctx.metadata["commit_audit_refs_keys"] = sorted(audit_refs.keys())
                llm_meta = audit_refs.get("llm")
                if isinstance(llm_meta, dict):
                    ctx.metadata["llm_runtime"] = {
                        "provider": llm_meta.get("provider"),
                        "model_id": llm_meta.get("model_id"),
                        "status": llm_meta.get("status"),
                    }

    async def run_as_sse_adapter(
        self,
        *,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: dict,
        metrics: Any,
        _pre_classified_scene: str | None = None,
    ):
        """MainChain 的 SSE 适配入口。

        在不改变既有 SSE 对外契约的前提下，适配层会执行：
          1. 前置节点（airlock/governor/graph/recall/llm）状态编排
          2. 委托 _unified_chat_sse_generator 产生真实 SSE
          3. 后置节点（qa/episodic/audit/sanitizer）收尾编排
        """
        from host_brain.app.chat_handler import _unified_chat_sse_generator  # noqa: PLC0415

        ctx = ChainContext(
            request_id=request_id,
            user_message=str(getattr(request_body, "message", "") or ""),
            session_id=session_id,
        )
        ctx.audit_tags.append("main_chain_adapter")
        ctx.metadata["adapter_mode"] = "sse"
        ctx.metadata["intent_action"] = str((intent_draft or {}).get("action") or "")
        explicit_scene = str(getattr(request_body, "scene_type", "") or "").strip().upper().removeprefix("SCENE_")
        pre_scene = str(_pre_classified_scene or "").strip().upper().removeprefix("SCENE_")
        if explicit_scene:
            ctx.metadata["pre_classified_scene"] = explicit_scene
            ctx.metadata["scene_source"] = "explicit"
        elif pre_scene:
            ctx.metadata["pre_classified_scene"] = pre_scene
            ctx.metadata["scene_source"] = "pre_classifier"
        else:
            ctx.metadata["scene_source"] = "unknown"
        ctx.metadata["commit_observed"] = False

        pre_node_order = (
            "airlock_quarantine",
            "governor",
            "graph_executor",
            "recall_fusion",
            "llm_gateway",
        )
        post_node_order = (
            "qa_contract",
            "episodic_store",
            "audit",
            "airlock_sanitizer",
        )

        commit_seen = False
        start = time.monotonic()
        try:
            pre_ok = await self._run_adapter_nodes(ctx=ctx, node_names=pre_node_order)
            if not pre_ok:
                ctx.audit_tags.append("MAIN_CHAIN_PRECHECK_FAILED")
                await self._invoke_failure_handler(ctx=ctx)
                yield self._build_reject_sse(
                    request_id=request_id,
                    reason_code="MAIN_CHAIN_PRECHECK_FAILED",
                    detail=str(ctx.failure_detail or "MainChain precheck failed"),
                )
                return

            async for event in _unified_chat_sse_generator(
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                metrics=metrics,
                _pre_classified_scene=_pre_classified_scene,
            ):
                payload = self._parse_sse_payload(event)
                if payload:
                    self._observe_adapter_sse_event(ctx=ctx, payload=payload)
                    if str(payload.get("phase") or "").strip().upper() == "COMMIT" and bool(payload.get("done")):
                        commit_seen = True
                yield event

            post_ok = await self._run_adapter_nodes(ctx=ctx, node_names=post_node_order)
            if not post_ok:
                ctx.audit_tags.append("MAIN_CHAIN_POSTPROCESS_FAILED")
                await self._invoke_failure_handler(ctx=ctx)
                if not commit_seen:
                    yield self._build_reject_sse(
                        request_id=request_id,
                        reason_code="MAIN_CHAIN_POSTPROCESS_FAILED",
                        detail=str(ctx.failure_detail or "MainChain postprocess failed"),
                    )
                return

            if not commit_seen:
                ctx.audit_tags.append("MAIN_CHAIN_COMMIT_NOT_OBSERVED")

        except Exception as exc:
            ctx.failure_event = FailureEvent.MODULE_TIMEOUT.value
            ctx.failure_detail = f"MainChain SSE adapter 异常: {exc}"
            logger.exception("MainChain.run_as_sse_adapter 异常: %s", exc)
            await self._invoke_failure_handler(ctx=ctx)
            if not commit_seen:
                yield self._build_reject_sse(
                    request_id=request_id,
                    reason_code="INTERNAL_SERVER_ERROR",
                    detail="MainChain adapter exception",
                )
        finally:
            ctx.node_timings["main_chain_adapter"] = time.monotonic() - start


# ---------------------------------------------------------------------------
# 输入上报入口（供 input_routes.py 调用）
# ---------------------------------------------------------------------------

async def run_main_chain_from_input(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict,
    ts_ms: int,
) -> None:
    """处理来自 Edge 端的输入上报（ASR / 微信消息），触发完整 chat 流程。

    由 input_routes.py 的 _dispatch_to_graph 以 fire-and-forget 方式调用。
    内部构建 ChatRequest 并委托给 MainChain.run_as_sse_adapter，
    但由于此路径无 SSE 客户端，仅执行处理逻辑（不推送 SSE 事件）。

    Args:
        request_id: 请求唯一 ID
        session_id: 会话 ID（ASR 时为 session_id，微信时为 from_wxid）
        source: 输入来源（"ASR" / "WECHAT"）
        text: 识别/接收到的文本
        payload: 完整原始载荷
        ts_ms: 消息时间戳（毫秒）
    """
    from host_brain.app.schemas import ChatRequest  # noqa: PLC0415
    from host_brain.app.dependencies import get_main_chain, get_obs_metrics_collector  # noqa: PLC0415

    logger.info(
        "run_main_chain_from_input: source=%s session_id=%s request_id=%s text=%.60s",
        source, session_id, request_id, text,
    )

    # 构建 ChatRequest（从 Edge 输入上报转换）
    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    mood_state = str(payload.get("mood_state") or "steady").strip().lower() or "steady"
    try:
        request_body = ChatRequest(
            message=text,
            session_id=session_id,
            thread_id=str(payload.get("session_id") or session_id),
            emotion_tag=emotion_tag,
            mood_state=mood_state,
        )
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: ChatRequest 构建失败 request_id=%s: %s",
            request_id, exc,
        )
        return

    # 构建 intent_draft
    from host_brain.app.graph_config_builder import pre_intent_classifier  # noqa: PLC0415
    pre_scene = pre_intent_classifier(text)
    _action_map = {
        "SCENE_EXEC_INTENT": "exec_intent",
        "SCENE_WORK_CODE": "work_code",
        "SCENE_WORK_TRANSLATE": "work_translate",
    }
    intent_draft = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent::{request_id}",
        "ts_ms": ts_ms,
        "session_id": session_id,
        "seq": 1,
        "type": "query",
        "action": _action_map.get(pre_scene, "narrative_only"),
        "target": "host",
        "args": {"text": text, "source": source},
        "nonce": f"nonce::{request_id}",
    }

    metrics = get_obs_metrics_collector()
    chain = get_main_chain()

    # 消费 SSE 事件（fire-and-forget，不推送给客户端）
    try:
        async for _event in chain.run_as_sse_adapter(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            metrics=metrics,
            _pre_classified_scene=pre_scene,
        ):
            pass  # 消费事件，不推送（无 SSE 客户端）
    except Exception as exc:
        logger.error(
            "run_main_chain_from_input: 处理异常 request_id=%s: %s",
            request_id, exc,
        )
