"""默认关闭的 Secure Ingress 适配边界，供 Host/Obs/Gateway/Bridge 分阶段切换。

该层将部署模式与安全内核决策分开：影子模式可产生审计证据却不能允许执行，只有显式
ENFORCE 且内核允许时才给出执行许可，未知环境值始终回退关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping

from .models import SecureIngressDecision, SecureIngressRequest
from .service import SecureIngressService


SECURE_INGRESS_MODE_ENV = "HOST_SECURE_INGRESS_MODE"


class SecureIngressMode(str, Enum):
    """部署切换模式：关闭、仅观测或强制执行。"""

    OFF = "OFF"
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"


def load_secure_ingress_mode(
    env: Mapping[str, str] | None = None,
) -> SecureIngressMode:
    """从环境读取模式；无效值按 OFF 处理，避免拼写错误意外开启强制准入。"""
    source = os.environ if env is None else env
    raw = str(source.get(SECURE_INGRESS_MODE_ENV, "OFF")).strip().upper()
    try:
        return SecureIngressMode(raw)
    except ValueError:
        return SecureIngressMode.OFF


@dataclass(frozen=True, slots=True)
class SecureIngressAdapterOutcome:
    """适配层结果：评估是否发生与执行许可必须分别读取。"""

    mode: SecureIngressMode
    evaluated: bool
    permit_execution: bool
    decision: SecureIngressDecision | None


class SecureIngressAdapter:
    """暴露唯一显式执行许可，不保留旧链路的隐式放行。

    OFF 不调用服务也不授予许可；SHADOW 调用服务但许可恒为假；ENFORCE 才把内核的
    ``decision.allowed`` 转成许可，确保渐进上线不会把观测误当作授权。
    """

    def __init__(
        self,
        *,
        service: SecureIngressService,
        mode: SecureIngressMode = SecureIngressMode.OFF,
    ) -> None:
        self._service = service
        self._mode = mode

    @property
    def mode(self) -> SecureIngressMode:
        """返回初始化时冻结的部署模式，避免单个请求中读取到漂移环境。"""
        return self._mode

    def evaluate(self, request: SecureIngressRequest) -> SecureIngressAdapterOutcome:
        """按当前模式评估请求，并仅在 ENFORCE 的明确允许下放行执行。"""
        if self._mode is SecureIngressMode.OFF:
            return SecureIngressAdapterOutcome(
                mode=self._mode,
                evaluated=False,
                permit_execution=False,
                decision=None,
            )
        decision = self._service.decide(request)
        return SecureIngressAdapterOutcome(
            mode=self._mode,
            evaluated=True,
            permit_execution=bool(
                self._mode is SecureIngressMode.ENFORCE and decision.allowed
            ),
            decision=decision,
        )


__all__ = [
    "SECURE_INGRESS_MODE_ENV",
    "SecureIngressAdapter",
    "SecureIngressAdapterOutcome",
    "SecureIngressMode",
    "load_secure_ingress_mode",
]
