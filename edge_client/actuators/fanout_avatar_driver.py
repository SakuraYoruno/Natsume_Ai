"""Fan-out avatar driver for mirroring speech/avatar commands.

The primary driver owns the runtime contract. Optional mirror drivers receive
best-effort copies of commands, so a slow or unavailable mirror cannot break the
main desktop pet path.
"""

from __future__ import annotations

from typing import Any

from edge_client.actuators.vts_driver import (
    AVATAR_FREE_ACTION_BLOCKED,
    VtsDriverResult,
    VtsState,
    is_blocked_avatar_hotkey_id,
)


class FanoutAvatarDriver:
    """Mirror avatar commands from a primary driver to secondary drivers."""

    backend = "vrm"

    def __init__(self, primary: Any, mirrors: list[Any] | None = None) -> None:
        self._primary = primary
        self._mirrors = list(mirrors or [])
        self._last_mirror_results: dict[str, dict[str, Any]] = {}

    @property
    def state(self) -> VtsState:
        return getattr(self._primary, "state", VtsState.DISCONNECTED)

    @property
    def talking(self) -> bool:
        return bool(getattr(self._primary, "talking", False))

    @property
    def current_expression(self) -> str | None:
        return getattr(self._primary, "current_expression", None)

    def startup(self) -> None:
        # 主驱动承载用户可见的关键路径，镜像仅为增强体验；镜像初始化失败必须被记录
        # 而非反向阻断主驱动，避免可选形象端影响桌面陪伴的可用性。
        if hasattr(self._primary, "startup"):
            self._primary.startup()
        for mirror in self._mirrors:
            try:
                if hasattr(mirror, "startup"):
                    mirror.startup()
            except Exception as exc:
                self._remember_mirror_result(mirror, "startup", False, str(exc))

    def shutdown(self) -> None:
        for mirror in self._mirrors:
            try:
                if hasattr(mirror, "shutdown"):
                    mirror.shutdown()
            except Exception as exc:
                self._remember_mirror_result(mirror, "shutdown", False, str(exc))
        if hasattr(self._primary, "shutdown"):
            self._primary.shutdown()

    def connect(self) -> VtsDriverResult:
        primary_result = self._primary.connect()
        for mirror in self._mirrors:
            try:
                if hasattr(mirror, "connect"):
                    result = mirror.connect()
                    self._remember_mirror_vts_result(mirror, result)
            except Exception as exc:
                self._remember_mirror_result(mirror, "connect", False, str(exc))
        return primary_result

    def disconnect(self) -> VtsDriverResult:
        # 先尝试释放镜像，再断开权威主驱动；每个镜像独立容错，避免一个失效副本
        # 遮蔽主通道的真实断开结果。
        for mirror in self._mirrors:
            try:
                if hasattr(mirror, "disconnect"):
                    result = mirror.disconnect()
                    self._remember_mirror_vts_result(mirror, result)
            except Exception as exc:
                self._remember_mirror_result(mirror, "disconnect", False, str(exc))
        return self._primary.disconnect()

    def talk_start(self) -> VtsDriverResult:
        result = self._primary.talk_start()
        self._fanout_authenticated("talk_start")
        return result

    def talk_end(self) -> VtsDriverResult:
        result = self._primary.talk_end()
        self._fanout_authenticated("talk_end")
        return result

    def display_text(self, text: str) -> VtsDriverResult:
        display_text = getattr(self._primary, "display_text", None)
        if callable(display_text):
            return display_text(text)
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="primary driver has no display_text",
            action="display_text",
        )

    def set_expression(self, emotion_tag: str | None, *, intensity: float | None = None) -> VtsDriverResult:
        result = self._primary.set_expression(emotion_tag, intensity=intensity)
        self._fanout_authenticated("set_expression", emotion_tag, intensity=intensity)
        return result

    def transition_expression(
        self,
        emotion_tag: str | None,
        *,
        transition_frames: list[dict[str, Any]] | None = None,
    ) -> VtsDriverResult:
        result = self._primary.transition_expression(emotion_tag, transition_frames=transition_frames)
        self._fanout_authenticated("transition_expression", emotion_tag, transition_frames=transition_frames)
        return result

    def reset_to_neutral(self) -> VtsDriverResult:
        result = self._primary.reset_to_neutral()
        if result.success and result.detail != "VTS reset_to_neutral disabled; hotkey reset skipped":
            self._fanout_authenticated("reset_to_neutral")
        return result

    def set_mouth_open_level(
        self,
        value: float,
        *,
        _include_companions: bool = False,
        _batch_companions: bool = False,
        _motion_values: dict[str, float] | None = None,
    ) -> VtsDriverResult:
        # 主端保留调用方给出的伴随角色策略；镜像端则统一批量展开，防止镜像拓扑
        # 因各自的局部配置出现不一致的口型帧。
        result = self._primary.set_mouth_open_level(
            value,
            _include_companions=_include_companions,
            _batch_companions=_batch_companions,
            _motion_values=_motion_values,
        )
        self._fanout_authenticated(
            "set_mouth_open_level",
            value,
            _include_companions=True,
            _batch_companions=True,
            _motion_values=_motion_values,
        )
        return result

    def move_model(
        self,
        *,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 0.0,
        relative: bool = True,
        move_time_s: float = 0.1,
    ) -> VtsDriverResult:
        result = self._primary.move_model(
            position_x=position_x,
            position_y=position_y,
            rotation=rotation,
            size=size,
            relative=relative,
            move_time_s=move_time_s,
        )
        self._fanout_authenticated(
            "move_model",
            position_x=position_x,
            position_y=position_y,
            rotation=rotation,
            size=size,
            relative=relative,
            move_time_s=move_time_s,
        )
        return result

    def trigger_hotkey(self, hotkey_id: str | None) -> VtsDriverResult:
        # 先在聚合边界拒绝自由热键，保证镜像不会借由 fan-out 绕过主驱动的
        # 形象动作白名单；只有主端成功后才复制该动作。
        if is_blocked_avatar_hotkey_id(hotkey_id):
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_FREE_ACTION_BLOCKED,
                detail=f"blocked avatar hotkey: {str(hotkey_id or '').strip()}",
                action="trigger_hotkey",
                expression_id=str(hotkey_id or "").strip() or None,
            )
        result = self._primary.trigger_hotkey(hotkey_id)
        if result.success:
            self._fanout_authenticated("trigger_hotkey", hotkey_id)
        return result

    def trigger_reconnect(self) -> None:
        if hasattr(self._primary, "trigger_reconnect"):
            self._primary.trigger_reconnect()
        for mirror in self._mirrors:
            try:
                if hasattr(mirror, "trigger_reconnect"):
                    mirror.trigger_reconnect()
                elif hasattr(mirror, "connect"):
                    mirror.connect()
            except Exception as exc:
                self._remember_mirror_result(mirror, "trigger_reconnect", False, str(exc))

    def health(self) -> dict[str, Any]:
        primary_health = self._primary.health()
        mirror_health: list[dict[str, Any]] = []
        for mirror in self._mirrors:
            try:
                health = mirror.health() if hasattr(mirror, "health") else {}
            except Exception as exc:
                health = {
                    "backend": getattr(mirror, "backend", type(mirror).__name__),
                    "status": "down",
                    "connection_error": str(exc),
                }
            mirror_health.append(dict(health or {}))
        return {
            **dict(primary_health or {}),
            "backend": getattr(self._primary, "backend", self.backend),
            "fanout": {
                "enabled": bool(self._mirrors),
                "mirrors": mirror_health,
                "last_results": dict(self._last_mirror_results),
            },
        }

    def _fanout_authenticated(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        # 镜像尚未完成认证时只留下可观测结果，不发起隐式连接或重试，避免一条高频
        # 口型/表情指令意外放大为连接风暴。
        for mirror in self._mirrors:
            if getattr(mirror, "state", None) != VtsState.AUTHENTICATED:
                self._remember_mirror_result(mirror, method_name, False, "mirror not authenticated")
                continue
            method = getattr(mirror, method_name, None)
            if not callable(method):
                self._remember_mirror_result(mirror, method_name, False, "method unavailable")
                continue
            try:
                result = method(*args, **kwargs)
                self._remember_mirror_vts_result(mirror, result)
            except Exception as exc:
                self._remember_mirror_result(mirror, method_name, False, str(exc))

    def _remember_mirror_vts_result(self, mirror: Any, result: Any) -> None:
        self._remember_mirror_result(
            mirror,
            str(getattr(result, "action", None) or "command"),
            bool(getattr(result, "success", False)),
            str(getattr(result, "detail", "") or ""),
        )

    def _remember_mirror_result(self, mirror: Any, action: str, success: bool, detail: str) -> None:
        # 以 backend 名为稳定诊断键保存最近结果；这些信息只服务健康检查，
        # 不会改变主驱动的成功语义。
        key = str(getattr(mirror, "backend", type(mirror).__name__))
        self._last_mirror_results[key] = {
            "action": action,
            "success": bool(success),
            "detail": detail,
        }
