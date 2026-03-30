"""
scene_router.py —— 场景路由器

在 Governor 放行后根据 task_type / scene_type 分流到专用子链。
从 main_chain.py 拆分而来。
"""
from __future__ import annotations

import logging
import os as _os
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from .chain_types import ChainContext
from ...schemas import TaskType

logger = logging.getLogger(__name__)

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
        # 预召回结果（来自 MainChain.RecallFusionNode），传入时跳过 _sse_event_generator 内部重复召回
        _pre_recall_evidence: list[dict] | None = None,
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
            gaming_edge_enabled: 是否启用 Gaming Edge 路由（已废弃，保留参数兼容性）

        Yields:
            SSE 事件字符串
        """
        # 延迟导入，避免循环依赖
        from host_brain.app.chat_stream_handler import _sse_event_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415
        from host_brain.app.chat_handler_vision import _vision_ui_sse_generator  # noqa: PLC0415
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            SCENE_CHAT,
            SCENE_VISION_UI,
        )
        from host_brain.app.prompt_builder import SCENE_GATEWAY  # noqa: PLC0415

        normalized_scene = str(scene_type or "").strip().upper()
        _input_source = str(getattr(request_body, "input_source", "") or "").strip().upper()
        _reply_channel = str(getattr(request_body, "reply_channel", "") or "").strip().upper()
        _wechat_like_channel = _reply_channel in {"WECHAT", "WX", "WEIXIN"}
        _suppress_edge_tts = _parse_bool(
            getattr(request_body, "suppress_edge_tts", None),
            default=_wechat_like_channel,
        )
        _suppress_edge_vts = _parse_bool(
            getattr(request_body, "suppress_edge_vts", None),
            default=_wechat_like_channel,
        )
        if _wechat_like_channel or _input_source == "WECHAT":
            logger.info(
                "[%s] SceneRouter suppress context input_source=%s reply_channel=%s raw_tts=%r raw_vts=%r",
                request_id,
                _input_source,
                _reply_channel,
                getattr(request_body, "suppress_edge_tts", None),
                getattr(request_body, "suppress_edge_vts", None),
            )
        if _wechat_like_channel:
            logger.info(
                "[%s] SceneRouter wechat suppress flags reply_channel=%s raw_tts=%r raw_vts=%r parsed_tts=%s parsed_vts=%s",
                request_id,
                _reply_channel,
                getattr(request_body, "suppress_edge_tts", None),
                getattr(request_body, "suppress_edge_vts", None),
                _suppress_edge_tts,
                _suppress_edge_vts,
            )

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

        # ── 1.5. SCENE_GATEWAY：Gateway 编排链路（中文回复）─────────────
        if normalized_scene == SCENE_GATEWAY:
            logger.info("[%s] SceneRouter → SCENE_GATEWAY", request_id)
            from host_brain.app.handlers.gateway_orchestrator_handler import (  # noqa: PLC0415
                gateway_orchestrator_sse_generator,
            )
            async for event in gateway_orchestrator_sse_generator(
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
                _pre_recall_evidence=_pre_recall_evidence,
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

        # ── 3. SCENE_CHAT：token 级流式 ─────────────────────────
        if normalized_scene == SCENE_CHAT:
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
                _pre_recall_evidence=_pre_recall_evidence,
                suppress_edge_tts=bool(_suppress_edge_tts),
                suppress_edge_vts=bool(_suppress_edge_vts),
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

