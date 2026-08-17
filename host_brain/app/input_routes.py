"""Host HTTP 输入适配器的兼容性导出层。

实际的载荷规范化、治理命令准入与 worker 分发均由 ``host_brain.input_runtime``
拥有；本模块不新增路由、不执行鉴权，也不应成为绕过该运行时边界的替代入口。
"""

from host_brain.input_runtime.contracts_and_normalization import GovernedCommandApplicationProvider, InputPayload, InputResponse
from host_brain.input_runtime.ingress import receive_input
from host_brain.input_runtime.worker_dispatch import create_input_router

__all__ = [
    "GovernedCommandApplicationProvider",
    "InputPayload",
    "InputResponse",
    "create_input_router",
    "receive_input",
]
